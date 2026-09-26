"""One captured image and the clock that belongs to it.

A raw fisheye read can return the same picture twice. A Frame is only created
for a new sample, and its sequence is counted in this process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ORIGIN_FILE = "file"
ORIGIN_A2_H264 = "a2_h264"
ORIGIN_A2_FISHEYE = "a2_fisheye"
ORIGINS = frozenset({ORIGIN_FILE, ORIGIN_A2_H264, ORIGIN_A2_FISHEYE})


@dataclass(frozen=True, slots=True)
class Frame:
    frame_seq: int
    capture_monotonic_ns: int
    width: int
    height: int
    image: Any
    camera_id: str
    origin: str

    def __post_init__(self) -> None:
        _require_non_negative_int("frame_seq", self.frame_seq)
        _require_non_negative_int("capture_monotonic_ns", self.capture_monotonic_ns)
        _require_positive_int("width", self.width)
        _require_positive_int("height", self.height)
        if not isinstance(self.camera_id, str) or not self.camera_id.strip():
            raise ValueError("camera_id must be a non-empty string")
        if self.camera_id != self.camera_id.strip():
            raise ValueError("camera_id must not have surrounding whitespace")
        if self.origin not in ORIGINS:
            raise ValueError(f"origin must be one of {sorted(ORIGINS)}")
        image_height, image_width = _bgr_size(self.image)
        if image_width != self.width or image_height != self.height:
            raise ValueError(
                "width and height must match the BGR image shape "
                f"(image is {image_width}x{image_height}, "
                f"frame says {self.width}x{self.height})"
            )


def _require_non_negative_int(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative int")


def _require_positive_int(name: str, value: object) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive int")


def _bgr_size(image: object) -> tuple[int, int]:
    shape = getattr(image, "shape", None)
    if shape is None or len(tuple(shape)) != 3:
        raise ValueError("image must be a BGR array with shape (height, width, 3)")
    height, width, channels = (int(shape[0]), int(shape[1]), int(shape[2]))
    if channels != 3 or height <= 0 or width <= 0:
        raise ValueError("image must be a BGR array with shape (height, width, 3)")
    return height, width
