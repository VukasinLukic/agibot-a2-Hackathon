from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import struct
from dataclasses import dataclass
from typing import Optional

LOG = logging.getLogger("robot_mic")

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2


@dataclass(frozen=True)
class RobotMicConfig:
    mcast_group: str
    local_iface_ip: str
    frame_ms: int = 20
    queue_size: int = 50


class RobotMicInput:
    def __init__(self, cfg: RobotMicConfig) -> None:
        self.cfg = cfg
        self.sample_rate = SAMPLE_RATE
        self.num_channels = CHANNELS
        self.samples_per_frame = SAMPLE_RATE * cfg.frame_ms // 1000
        self._bytes_per_frame = (
            self.samples_per_frame * CHANNELS * SAMPLE_WIDTH_BYTES
        )
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=cfg.queue_size)
        self._sock: Optional[socket.socket] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._sock:
            return
        self._sock = self._make_socket()
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._reader_task:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        if self._sock:
            with contextlib.suppress(Exception):
                self._sock.close()
            self._sock = None

    async def frames(self):
        while not self._stop_event.is_set():
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            yield frame
            self._queue.task_done()

    def _make_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 5555))
        mreq = struct.pack(
            "4s4s",
            socket.inet_aton(self.cfg.mcast_group),
            socket.inet_aton(self.cfg.local_iface_ip),
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except OSError:
            pass
        LOG.info(
            "Robot mic joined %s:5555 via %s",
            self.cfg.mcast_group,
            self.cfg.local_iface_ip,
        )
        return sock

    async def _reader_loop(self) -> None:
        assert self._sock is not None
        loop = asyncio.get_running_loop()
        buffer = bytearray()
        LOG.info("Robot mic reader running, frame=%d bytes", self._bytes_per_frame)

        try:
            while not self._stop_event.is_set():
                data, _ = await loop.run_in_executor(None, self._sock.recvfrom, 65535)
                if not data:
                    continue
                buffer.extend(data)

                while len(buffer) >= self._bytes_per_frame:
                    frame = bytes(buffer[: self._bytes_per_frame])
                    del buffer[: self._bytes_per_frame]

                    if self._queue.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            self._queue.get_nowait()
                            self._queue.task_done()
                    await self._queue.put(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error("Robot mic reader error: %s", exc)
            self._stop_event.set()
