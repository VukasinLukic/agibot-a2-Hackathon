from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

LOG = logging.getLogger("host_mic")


@dataclass(frozen=True)
class HostMicConfig:
    device: int | str | None
    sample_rate: int = 48000
    channels: int = 1
    frame_ms: int = 20
    queue_size: int = 50


class HostMicInput:
    def __init__(self, cfg: HostMicConfig) -> None:
        self.cfg = cfg
        self.sample_rate = cfg.sample_rate
        self.num_channels = cfg.channels
        self.samples_per_frame = int(cfg.sample_rate * cfg.frame_ms / 1000)
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=cfg.queue_size)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stream = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._stream:
            return
        try:
            import sounddevice as sd  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "sounddevice is required for host audio; install it with `pip install sounddevice`."
            ) from exc

        self._loop = asyncio.get_running_loop()
        device = self.cfg.device
        if device is None:
            device, _ = sd.default.device

        self._stream = sd.InputStream(
            callback=self._callback,
            dtype="int16",
            channels=self.cfg.channels,
            device=device,
            samplerate=self.cfg.sample_rate,
            blocksize=self.samples_per_frame,
        )
        self._stream.start()
        LOG.info("Host mic started (device=%s, %d Hz)", device, self.cfg.sample_rate)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        LOG.info("Host mic stopped")

    async def frames(self):
        while not self._stop_event.is_set():
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            yield frame
            self._queue.task_done()

    def _callback(self, indata, frames, time, status) -> None:
        if status:
            LOG.debug("Host mic status: %s", status)
        if not self._loop or self._loop.is_closed():
            return

        payload = indata.copy().tobytes()

        def _enqueue() -> None:
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    pass
            try:
                self._queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

        self._loop.call_soon_threadsafe(_enqueue)
