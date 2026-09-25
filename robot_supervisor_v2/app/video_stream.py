"""
MJPEG video streaming utilities for the frontend.

Captures frames from a camera device and yields multipart JPEG chunks.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import AsyncGenerator, Optional, Tuple

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - runtime dependency
    cv2 = None  # type: ignore

from livekit_shared.video_devices import resolve_camera_device

LOG = logging.getLogger("video_stream")


@dataclass
class StreamConfig:
    device: str
    width: Optional[int] = None
    height: Optional[int] = None
    framerate: Optional[float] = None
    jpeg_quality: int = 85


class CameraCapture:
    def __init__(
        self,
        *,
        device: str,
        width: Optional[int],
        height: Optional[int],
        framerate: Optional[float],
        jpeg_quality: int,
    ) -> None:
        self._device = device
        self._width = width
        self._height = height
        self._framerate = framerate
        self._jpeg_quality = jpeg_quality
        self._capture: Optional["cv2.VideoCapture"] = None

    def _open_capture(self) -> "cv2.VideoCapture":
        if cv2 is None:  # pragma: no cover - runtime dependency
            raise RuntimeError(
                "OpenCV is required for video streaming. Install with `pip install opencv-python`."
            )

        backend = cv2.CAP_V4L2 if sys.platform == "linux" else 0
        resolved_device = resolve_camera_device(self._device)
        index = _device_to_index(resolved_device)
        cap = cv2.VideoCapture(index if index is not None else resolved_device, backend)

        if not cap.isOpened():
            raise RuntimeError(f"Unable to open camera device {self._device} (resolved to {resolved_device})")

        if self._width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        if self._height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        if self._framerate:
            cap.set(cv2.CAP_PROP_FPS, self._framerate)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        return cap

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        self._capture = await loop.run_in_executor(None, self._open_capture)

    async def capture_jpeg(self) -> bytes:
        if self._capture is None:
            raise RuntimeError("Camera not opened")

        loop = asyncio.get_running_loop()
        ret, frame = await loop.run_in_executor(None, self._capture.read)
        if not ret or frame is None:
            raise RuntimeError("Failed to grab frame from camera")

        success, buffer = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality],
        )
        if not success:
            raise RuntimeError("Failed to encode frame as JPEG")
        return buffer.tobytes()

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


def _device_to_index(device: str) -> Optional[int]:
    if device.isdigit():
        return int(device)
    if device.startswith("/dev/video"):
        suffix = device.replace("/dev/video", "")
        if suffix.isdigit():
            return int(suffix)
    return None


def parse_resolution(value: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    if not value:
        return None, None
    if "x" not in value.lower():
        raise ValueError("Resolution must be WIDTHxHEIGHT (e.g. 1280x720)")
    width_str, height_str = value.lower().split("x", 1)
    return int(width_str), int(height_str)


async def mjpeg_stream(config: StreamConfig) -> AsyncGenerator[bytes, None]:
    camera = CameraCapture(
        device=config.device,
        width=config.width,
        height=config.height,
        framerate=config.framerate,
        jpeg_quality=config.jpeg_quality,
    )
    await camera.open()

    target_fps = config.framerate or 10.0
    interval = max(1.0 / target_fps, 0.01)
    loop = asyncio.get_running_loop()
    next_frame_at = loop.time()

    try:
        while True:
            try:
                frame = await camera.capture_jpeg()
            except Exception as exc:
                LOG.error("Video frame capture failed: %s", exc, exc_info=True)
                await asyncio.sleep(max(interval, 0.2))
                continue

            header = (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
            )
            yield header + frame + b"\r\n"
            next_frame_at += interval
            sleep_for = next_frame_at - loop.time()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                next_frame_at = loop.time()
    finally:
        camera.close()
