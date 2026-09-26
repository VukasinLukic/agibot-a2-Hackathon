"""A2 chest fisheye as a Frame stream.

The subscription itself is ``Ros2VideoCapture`` in
``robot_services.vision.detection.ros2_capture``. This module only refuses the
H.264 topics, stamps a sequence, and drops a repeated picture. It does not
open ROS until a real reader is requested.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from types import TracebackType
from typing import Any

from table_tennis.vision.capture import CaptureStats
from table_tennis.vision.frame import ORIGIN_A2_FISHEYE, Frame

FISHEYE_ALIASES = ("CHEST_LEFT_FISHEYE", "CHEST_RIGHT_FISHEYE")
# One empty read is a hiccup. The camera is missing only after this many in a row.
_READ_FAILURES = 3


def require_raw_fisheye(device: str) -> str:
    """Resolve a device with the existing alias table and keep only raw fisheye."""
    topics, is_h264, resolve = _ros2_topic_api()
    topic = resolve(device)
    allowed = {topics[name] for name in FISHEYE_ALIASES}
    if is_h264(topic) or topic not in allowed:
        raise ValueError(
            f"{device!r} is not a raw chest fisheye topic. "
            f"Use one of {', '.join(FISHEYE_ALIASES)}."
        )
    return topic


def marks_inside(width: int, height: int, points: Sequence[tuple[int, int]], margin_px: int) -> bool:
    """True when every mark sits inside the picture, away from the edge."""
    if margin_px < 0 or width <= margin_px * 2 or height <= margin_px * 2 or not points:
        return False
    for x, y in points:
        if x < margin_px or y < margin_px or x >= width - margin_px or y >= height - margin_px:
            return False
    return True


class A2FisheyeCapture:
    """Turn raw fisheye reads into Frames. A repeated image does not advance the sequence."""

    def __init__(
        self,
        device: str,
        *,
        reader: Any = None,
        now_ns: Callable[[], int] | None = None,
        margin_px: int = 8,
    ) -> None:
        self.topic = require_raw_fisheye(device)
        self._reader = reader
        self._owns_reader = reader is None
        self._now_ns = now_ns or time.monotonic_ns
        self.margin_px = margin_px
        self.stats = CaptureStats()
        self.camera_missing = False
        self._width = 0
        self._height = 0
        self._seq = 0
        self._previous: bytes | None = None
        self._last_ns = -1

    def __enter__(self) -> A2FisheyeCapture:
        if self._reader is None:
            capture_cls = _ros2_capture_cls()
            self._reader = capture_cls(self.topic)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        reader = self._reader
        if self._owns_reader and reader is not None:
            release = getattr(reader, "release", None)
            if callable(release):
                release()
        self._reader = None

    def __iter__(self) -> Iterator[Frame]:
        reader = self._reader
        if reader is None:
            raise RuntimeError("capture is not open")
        missed_reads = 0
        while True:
            ok, image = reader.read()
            if not ok or image is None:
                missed_reads += 1
                if missed_reads >= _READ_FAILURES:
                    self.camera_missing = True
                    self.stats.frames_dropped += 1
                    return
                continue
            missed_reads = 0
            payload = _sample_bytes(image)
            if payload == self._previous:
                self.stats.duplicate_frames += 1
                continue
            height, width = _shape(image)
            stamp = self._now_ns()
            if stamp <= self._last_ns:
                stamp = self._last_ns + 1
            frame = Frame(
                frame_seq=self._seq,
                capture_monotonic_ns=stamp,
                width=width,
                height=height,
                image=image,
                camera_id=self.topic,
                origin=ORIGIN_A2_FISHEYE,
            )
            self._seq += 1
            self._previous = payload
            self._last_ns = stamp
            self._width = width
            self._height = height
            self.stats.frames_emitted += 1
            yield frame

    def table_in_frame(self, points: Sequence[tuple[int, int]]) -> bool:
        """Corners and the net, in pixels. False until a frame has been seen."""
        if self._width == 0 or self._height == 0:
            return False
        return marks_inside(self._width, self._height, points, self.margin_px)


def _sample_bytes(image: object) -> bytes:
    data = getattr(image, "data", None)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    tobytes = getattr(image, "tobytes", None)
    if callable(tobytes):
        return bytes(tobytes())
    raise ValueError("camera image has no pixel bytes")


def _shape(image: object) -> tuple[int, int]:
    shape = getattr(image, "shape", None)
    if shape is None or len(tuple(shape)) != 3:
        raise ValueError("camera image must have shape (height, width, 3)")
    height, width, channels = (int(shape[0]), int(shape[1]), int(shape[2]))
    if channels != 3:
        raise ValueError("camera image must have shape (height, width, 3)")
    return height, width


def _ros2_topic_api():
    from robot_services.vision.detection.ros2_capture import (
        A2_ROS2_TOPICS,
        is_h264_topic,
        resolve_ros2_topic,
    )

    return A2_ROS2_TOPICS, is_h264_topic, resolve_ros2_topic


def _ros2_capture_cls():
    from robot_services.vision.detection.ros2_capture import Ros2VideoCapture

    return Ros2VideoCapture
