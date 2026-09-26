"""Read a local clip one frame at a time.

The capture loop only reads bytes and stamps a sequence. Drawing, audio, and
HTTP stay outside it. A short file is reported as dropped frames; missing
samples are not invented.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Iterator

from table_tennis.vision.frame import ORIGIN_FILE, Frame
from table_tennis.vision.image import BgrImage

MAGIC = b"TTCLIP01"
HEADER = struct.Struct("<8sIIIQ")
HEADER_SIZE = HEADER.size
MAX_FRAME_BYTES = 1920 * 1536 * 3


@dataclass
class CaptureStats:
    frames_emitted: int = 0
    frames_dropped: int = 0
    duplicate_frames: int = 0


class FileCapture:
    """Stream every complete frame from a TTCLIP file, then close it."""

    def __init__(self, path: Path | str, camera_id: str) -> None:
        if not isinstance(camera_id, str) or not camera_id.strip() or camera_id != camera_id.strip():
            raise ValueError("camera_id must be a non-empty string")
        self._path = Path(path)
        self._camera_id = camera_id
        self._file = None
        self._width = 0
        self._height = 0
        self._declared = 0
        self._period_ns = 0
        self.stats = CaptureStats()

    def __enter__(self) -> FileCapture:
        handle = self._path.open("rb")
        try:
            raw = handle.read(HEADER_SIZE)
            if len(raw) != HEADER_SIZE:
                raise ValueError("clip header is truncated")
            magic, width, height, declared, period_ns = HEADER.unpack(raw)
            if magic != MAGIC:
                raise ValueError("clip magic is not TTCLIP01")
            frame_bytes = _frame_bytes(width, height)
            if type(period_ns) is not int or period_ns <= 0:
                raise ValueError("frame period must be a positive int")
            self._file = handle
            self._width = width
            self._height = height
            self._declared = declared
            self._period_ns = period_ns
        except Exception:
            handle.close()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        handle = self._file
        self._file = None
        if handle is not None and not handle.closed:
            handle.close()

    def __iter__(self) -> Iterator[Frame]:
        handle = self._file
        if handle is None:
            raise RuntimeError("capture is not open")
        frame_bytes = _frame_bytes(self._width, self._height)
        for index in range(self._declared):
            blob = handle.read(frame_bytes)
            if len(blob) != frame_bytes:
                self.stats.frames_dropped += self._declared - index
                return
            image = BgrImage(self._width, self._height, blob)
            self.stats.frames_emitted += 1
            yield Frame(
                frame_seq=index,
                capture_monotonic_ns=index * self._period_ns,
                width=self._width,
                height=self._height,
                image=image,
                camera_id=self._camera_id,
                origin=ORIGIN_FILE,
            )


class ClipWriter:
    """Write a TTCLIP, patching the frame count when the file closes."""

    def __init__(self, path: Path | str, width: int, height: int, period_ns: int) -> None:
        _frame_bytes(width, height)
        if type(period_ns) is not int or period_ns <= 0:
            raise ValueError("frame period must be a positive int")
        self._path = Path(path)
        self._width = width
        self._height = height
        self._period_ns = period_ns
        self._file = None
        self._count = 0

    def __enter__(self) -> ClipWriter:
        handle = self._path.open("wb")
        handle.write(HEADER.pack(MAGIC, self._width, self._height, 0, self._period_ns))
        self._file = handle
        self._count = 0
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._file
        self._file = None
        if handle is None:
            return
        try:
            handle.seek(0)
            handle.write(HEADER.pack(MAGIC, self._width, self._height, self._count, self._period_ns))
        finally:
            handle.close()

    def write_frame(self, image: BgrImage) -> None:
        handle = self._file
        if handle is None:
            raise RuntimeError("clip writer is not open")
        if image.width != self._width or image.height != self._height:
            raise ValueError("frame size does not match the clip")
        handle.write(image.data)
        self._count += 1


def _frame_bytes(width: int, height: int) -> int:
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("width and height must be positive ints")
    frame_bytes = width * height * 3
    if frame_bytes > MAX_FRAME_BYTES:
        raise ValueError("frame is larger than the capture limit")
    return frame_bytes
