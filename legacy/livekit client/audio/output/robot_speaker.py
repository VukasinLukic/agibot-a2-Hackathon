from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

os.environ.setdefault(
    "CYCLONEDDS_URI",
    r"""
<CycloneDDS>
  <Domain>
    <Tracing>
      <Verbosity>warning</Verbosity>
      <OutputFile>stdout</OutputFile>
    </Tracing>
  </Domain>
</CycloneDDS>
""",
)

from unitree_sdk2py.core.channel import ChannelFactory
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

LOG = logging.getLogger("robot_speaker")

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH_BYTES


@dataclass(frozen=True)
class RobotSpeakerConfig:
    iface: str
    app_name: str = "livekit_bridge"
    chunk_ms: int = 200
    prebuffer_ms: int = 600
    volume: Optional[int] = None


class RobotSpeaker:
    def __init__(
        self,
        iface: str,
        app_name: str,
        chunk_ms: int,
        prebuffer_ms: int,
        volume: Optional[int],
    ) -> None:
        self.channel_factory = ChannelFactory()
        self.channel_factory.Init(0, iface)

        self.client = AudioClient()
        self.client.Init()
        self.client.SetTimeout(10.0)
        if volume is not None:
            clipped = max(0, min(100, volume))
            LOG.info("Setting robot volume to %d", clipped)
            self.client.SetVolume(clipped)

        self.app_name = app_name
        self.chunk_bytes = max(320, int(BYTES_PER_SECOND * (chunk_ms / 1000.0)) // 2 * 2)
        self.prebuffer_bytes = max(
            320, int(BYTES_PER_SECOND * (prebuffer_ms / 1000.0)) // 2 * 2
        )
        self.buffer = bytearray()
        self.stream_id: Optional[str] = None
        self.started = False
        self._lock = asyncio.Lock()

    async def start_stream(self, label: str) -> None:
        async with self._lock:
            await self._stop_locked()
            self.stream_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
            self.buffer.clear()
            self.started = False
            LOG.info(
                "Robot speaker session started for %s (stream_id=%s)",
                label,
                self.stream_id,
            )

    async def feed(self, payload: bytes) -> None:
        if not self.stream_id or not payload:
            return
        self.buffer.extend(payload)

        if not self.started and len(self.buffer) >= self.prebuffer_bytes:
            pre = bytes(self.buffer[: self.prebuffer_bytes])
            del self.buffer[: self.prebuffer_bytes]
            await self._send(pre)
            self.started = True
            LOG.info(
                "Robot speaker primed (%.1f ms buffered)",
                (len(pre) / BYTES_PER_SECOND) * 1000.0,
            )

        if not self.started:
            return

        while len(self.buffer) >= self.chunk_bytes:
            chunk = bytes(self.buffer[: self.chunk_bytes])
            del self.buffer[: self.chunk_bytes]
            await self._send(chunk)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if not self.stream_id:
            self.buffer.clear()
            return
        try:
            await asyncio.to_thread(self.client.PlayStop, self.app_name)
            LOG.info("Robot speaker stopped")
        finally:
            self.buffer.clear()
            self.stream_id = None
            self.started = False

    async def _send(self, payload: bytes) -> None:
        if not self.stream_id or not payload:
            return
        data = list(payload)
        ret = await asyncio.to_thread(
            self.client.PlayStream, self.app_name, self.stream_id, data
        )
        if isinstance(ret, tuple):
            ok = int(ret[0]) == 0
        else:
            ok = int(ret) == 0
        if not ok:
            raise RuntimeError(f"PlayStream error: {ret}")


class RobotSpeakerOutput:
    def __init__(self, cfg: RobotSpeakerConfig) -> None:
        self.cfg = cfg
        self.sample_rate = SAMPLE_RATE
        self.num_channels = CHANNELS
        self._speaker = RobotSpeaker(
            iface=cfg.iface,
            app_name=cfg.app_name,
            chunk_ms=cfg.chunk_ms,
            prebuffer_ms=cfg.prebuffer_ms,
            volume=cfg.volume,
        )

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        await self._speaker.stop()

    async def start_stream(self, label: str) -> None:
        await self._speaker.start_stream(label)

    async def stop_stream(self) -> None:
        await self._speaker.stop()

    async def feed(self, payload: bytes) -> None:
        await self._speaker.feed(payload)
