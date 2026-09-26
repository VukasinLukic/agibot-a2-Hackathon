"""A phone recording (.mov, .mp4, anything OpenCV opens) as a Frame stream.

Laptop testing only; the robot reads ``a2.A2FisheyeCapture``. The frame time
is the file's own clock (``CAP_PROP_POS_MSEC``). With ``realtime`` the reader
waits so frames arrive at the recording's pace, which leaves the operator time
to press serve in the app. When the reader falls behind (slow frame, paused
preview) it continues from where it is instead of rushing to catch up.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Iterator

from table_tennis.vision.capture import CaptureStats
from table_tennis.vision.frame import ORIGIN_FILE, Frame

# Further behind than this, the pace restarts from the current frame.
_MAX_LAG_S = 0.5


class VideoFileCapture:
    """Stream every decoded frame of a video file, then stop. A file end is not a missing camera."""

    def __init__(
        self,
        path: Path | str,
        camera_id: str,
        *,
        realtime: bool = True,
        start_s: float = 0.0,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(camera_id, str) or not camera_id.strip() or camera_id != camera_id.strip():
            raise ValueError("camera_id must be a non-empty string")
        self._path = Path(path)
        self._camera_id = camera_id
        self._realtime = realtime
        self._start_s = max(0.0, float(start_s))
        self._now = now
        self._sleep = sleep
        self._capture: Any = None
        self._fps = 30.0
        self.camera_missing = False
        self.stats = CaptureStats()

    def __enter__(self) -> VideoFileCapture:
        import cv2

        capture = cv2.VideoCapture(str(self._path))
        if not capture.isOpened():
            capture.release()
            raise ValueError(f"cannot open video {self._path}")
        if self._start_s > 0:
            capture.set(cv2.CAP_PROP_POS_MSEC, self._start_s * 1000.0)
        self._fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        self._capture = capture
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        capture = self._capture
        self._capture = None
        if capture is not None:
            capture.release()

    def __iter__(self) -> Iterator[Frame]:
        import cv2

        capture = self._capture
        if capture is None:
            raise RuntimeError("capture is not open")
        anchor: float | None = None
        last_ns = -1
        index = 0
        while True:
            ok, image = capture.read()
            if not ok or image is None:
                return
            position_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
            stamp = int(position_ms * 1_000_000) if position_ms > 0 else int(index * 1e9 / self._fps)
            stamp = max(stamp, last_ns + 1)
            last_ns = stamp
            if self._realtime:
                anchor = self._pace(anchor, stamp / 1e9)
            height, width = image.shape[:2]
            self.stats.frames_emitted += 1
            yield Frame(index, stamp, width, height, image, self._camera_id, ORIGIN_FILE)
            index += 1

    def _pace(self, anchor: float | None, stamp_s: float) -> float:
        """Wait until this frame's moment. ``anchor`` is the wall time of video time zero."""
        now = self._now()
        if anchor is None or now - (anchor + stamp_s) > _MAX_LAG_S:
            return now - stamp_s
        wait = anchor + stamp_s - now
        if wait > 0:
            self._sleep(wait)
        return anchor
