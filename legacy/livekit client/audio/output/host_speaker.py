from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np

LOG = logging.getLogger("host_speaker")


@dataclass(frozen=True)
class HostSpeakerConfig:
    device: int | str | None
    sample_rate: int = 48000
    channels: int = 1
    frame_ms: int = 20
    max_buffer_ms: int = 500
    volume: float | None = None


class HostSpeakerOutput:
    def __init__(self, cfg: HostSpeakerConfig) -> None:
        self.cfg = cfg
        self.sample_rate = cfg.sample_rate
        self.num_channels = cfg.channels
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stream = None
        self._bytes_per_frame = int(cfg.sample_rate * cfg.frame_ms / 1000) * cfg.channels * 2
        self._max_buffer_bytes = max(
            self._bytes_per_frame,
            int(cfg.sample_rate * cfg.max_buffer_ms / 1000) * cfg.channels * 2,
        )
        self._volume = _clamp_volume(cfg.volume)

    async def start(self) -> None:
        if self._stream:
            return
        try:
            import sounddevice as sd  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "sounddevice is required for host audio; install it with `pip install sounddevice`."
            ) from exc

        device = self.cfg.device
        if device is None:
            _, device = sd.default.device

        self._stream = sd.OutputStream(
            callback=self._callback,
            dtype="int16",
            channels=self.cfg.channels,
            device=device,
            samplerate=self.cfg.sample_rate,
            blocksize=int(self.cfg.sample_rate * self.cfg.frame_ms / 1000),
        )
        self._stream.start()
        LOG.info("Host speaker started (device=%s, %d Hz)", device, self.cfg.sample_rate)

    async def stop(self) -> None:
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            self._buffer.clear()
        LOG.info("Host speaker stopped")

    async def start_stream(self, label: str) -> None:
        with self._lock:
            self._buffer.clear()
        LOG.info("Host speaker stream started for %s", label)

    async def stop_stream(self) -> None:
        with self._lock:
            self._buffer.clear()

    async def feed(self, payload: bytes) -> None:
        if not payload:
            return
        with self._lock:
            self._buffer.extend(payload)
            if len(self._buffer) > self._max_buffer_bytes:
                excess = len(self._buffer) - self._max_buffer_bytes
                del self._buffer[:excess]

    def _callback(self, outdata, frames, time, status) -> None:
        if status:
            LOG.debug("Host speaker status: %s", status)

        bytes_needed = frames * self.cfg.channels * 2
        with self._lock:
            if len(self._buffer) < bytes_needed:
                available = len(self._buffer)
                if available:
                    data = np.frombuffer(
                        self._buffer[:available], dtype=np.int16, count=available // 2
                    )
                    data = _apply_volume(data, self._volume)
                    data = data.reshape(-1, self.cfg.channels)
                    outdata[: data.shape[0]] = data
                    del self._buffer[:available]
                if available < bytes_needed:
                    outdata[available // (2 * self.cfg.channels) :] = 0
            else:
                chunk = self._buffer[:bytes_needed]
                data = np.frombuffer(chunk, dtype=np.int16, count=bytes_needed // 2)
                data = _apply_volume(data, self._volume)
                outdata[:] = data.reshape(-1, self.cfg.channels)
                del self._buffer[:bytes_needed]


def _clamp_volume(value: float | None) -> float:
    if value is None:
        return 1.0
    return max(0.0, min(1.0, value))


def _apply_volume(data: np.ndarray, volume: float) -> np.ndarray:
    if volume >= 0.999:
        return data
    scaled = data.astype(np.float32) * volume
    np.clip(scaled, -32768, 32767, out=scaled)
    return scaled.astype(np.int16)
