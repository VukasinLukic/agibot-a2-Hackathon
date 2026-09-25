"""
Conversation camera stream service implementation.
Provides an explicit start/stop lifecycle for operator-facing camera monitoring.

Legacy fallback note:
The frontend now prefers subscribing to the LiveKit camera publication from
`camera-bridge`. This MJPEG/OpenCV path is kept for backward compatibility and
should not be extended unless the LiveKit monitor path is unavailable.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from livekit_shared.video_devices import resolve_camera_device

from ..video_stream import parse_resolution
from .base import BaseService, ConfigParameter, ServiceState

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - runtime dependency
    cv2 = None  # type: ignore


class ConversationCameraStreamService(BaseService):
    """Legacy operator-only camera stream service (non-LiveKit fallback)."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "Conversation Camera Stream"),
            config=config,
        )
        self._stream_lock = asyncio.Lock()
        self._stream_condition = asyncio.Condition(self._stream_lock)
        self._stream_generation = 0
        self._next_stream_id = 0
        self._active_streams: Dict[int, int] = {}
        self._last_frame_at: Optional[float] = None

    @staticmethod
    def _device_to_index(device: str) -> Optional[int]:
        if device.isdigit():
            return int(device)
        if device.startswith("/dev/video"):
            suffix = device.replace("/dev/video", "")
            if suffix.isdigit():
                return int(suffix)
        return None

    def _resolve_capture_settings(
        self, device
    ) -> Tuple[str, Optional[int], Optional[int], Optional[float]]:
        resolution = self._config.get("resolution")
        width, height = parse_resolution(resolution) if resolution else (None, None)

        framerate_raw = self._config.get("framerate")
        framerate = float(framerate_raw) if framerate_raw not in (None, "") else None
        if framerate is not None and framerate <= 0:
            raise RuntimeError("Conversation stream framerate must be greater than 0")

        return device, width, height, framerate

    def _probe_camera(self) -> None:
        if cv2 is None:  # pragma: no cover - runtime dependency
            raise RuntimeError("OpenCV is required for conversation camera streaming")
        device = str(self._config.get("device", "/dev/video0"))
        resolved_device = resolve_camera_device(device)
        device, width, height, framerate = self._resolve_capture_settings(resolved_device)
        backend = cv2.CAP_V4L2 if sys.platform == "linux" else 0
        index = self._device_to_index(resolved_device)
        capture = cv2.VideoCapture(index if index is not None else resolved_device, backend)
        try:
            if not capture.isOpened():
                raise RuntimeError(f"Unable to open camera device {device} (resolved to {resolved_device})")

            if width:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            if height:
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            if framerate:
                capture.set(cv2.CAP_PROP_FPS, framerate)
            if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            ret, frame = capture.read()
            if not ret or frame is None:
                raise RuntimeError(f"Failed to read an initial frame from camera device {device}")
        finally:
            capture.release()

    def _max_clients(self) -> int:
        configured = self._config.get("max_clients", 1)
        try:
            max_clients = int(configured)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Conversation camera stream max_clients must be an integer") from exc
        if max_clients <= 0:
            raise RuntimeError("Conversation camera stream max_clients must be greater than 0")
        return max_clients

    async def start(self) -> None:
        if self._state != ServiceState.STOPPED:
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            await asyncio.to_thread(self._probe_camera)
            async with self._stream_condition:
                self._stream_generation += 1
                self._active_streams.clear()
                self._last_frame_at = None
                self._stream_condition.notify_all()
            self._state = ServiceState.RUNNING
            self._mark_started()
        except Exception as exc:
            self._state = ServiceState.FAILED
            self._last_error = str(exc)
            raise

    async def stop(self) -> None:
        if self._state == ServiceState.STOPPED:
            return

        self._state = ServiceState.STOPPING
        async with self._stream_condition:
            self._stream_generation += 1
            self._active_streams.clear()
            self._last_frame_at = None
            self._stream_condition.notify_all()

        self._state = ServiceState.STOPPED
        self._start_time = None

    async def check_health(self) -> bool:
        if self._state != ServiceState.RUNNING:
            return False

        async with self._stream_lock:
            active_streams = sum(
                1 for generation in self._active_streams.values() if generation == self._stream_generation
            )
            last_frame_at = self._last_frame_at

        if active_streams == 0 or last_frame_at is None:
            return True

        framerate_raw = self._config.get("framerate", 30)
        try:
            framerate = float(framerate_raw) if framerate_raw not in (None, "") else 30.0
        except (TypeError, ValueError):
            framerate = 30.0
        frame_interval = max(1.0 / max(framerate, 1.0), 0.01)
        stale_after = max(5.0, frame_interval * 10.0)
        return (time.time() - last_frame_at) <= stale_after

    async def update_config(self, updates: Dict[str, Any]) -> None:
        self._config.update(updates)
        if self._state == ServiceState.RUNNING:
            await self.restart()

    async def begin_stream(self) -> Tuple[int, int]:
        acquire_timeout = 1.5
        deadline = time.monotonic() + acquire_timeout

        async with self._stream_condition:
            while True:
                if self._state != ServiceState.RUNNING:
                    raise RuntimeError("Start Conversation Camera Stream service first")

                active_streams = sum(
                    1 for generation in self._active_streams.values() if generation == self._stream_generation
                )
                max_clients = self._max_clients()
                if active_streams < max_clients:
                    self._next_stream_id += 1
                    lease_id = self._next_stream_id
                    generation = self._stream_generation
                    self._active_streams[lease_id] = generation
                    self._last_frame_at = time.time()
                    return lease_id, generation

                if max_clients == 1:
                    # Browser MJPEG viewers can briefly overlap during remounts or
                    # cache-busting URL changes. Prefer a clean handoff to the new
                    # request instead of failing the operator stream.
                    self._stream_generation += 1
                    self._active_streams.clear()
                    self._stream_condition.notify_all()
                    continue

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Conversation camera stream is already in use by another client")

                try:
                    await asyncio.wait_for(self._stream_condition.wait(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        "Conversation camera stream is already in use by another client"
                    ) from exc

    def is_stream_active(self, lease_id: int, generation: int) -> bool:
        return (
            self._state == ServiceState.RUNNING
            and generation == self._stream_generation
            and self._active_streams.get(lease_id) == generation
        )

    def note_stream_frame(self, lease_id: int, generation: int) -> None:
        if self.is_stream_active(lease_id, generation):
            self._last_frame_at = time.time()
            self._last_error = None

    def register_stream_error(self, message: str) -> None:
        self._last_error = message

    async def end_stream(self, lease_id: int) -> None:
        async with self._stream_condition:
            self._active_streams.pop(lease_id, None)
            if not self._active_streams:
                self._last_frame_at = None
            self._stream_condition.notify_all()

    def get_log_path(self) -> Optional[str]:
        return None

    def get_backend_type(self) -> str:
        return "in_process"

    def get_config_parameters(self) -> List[ConfigParameter]:
        return [
            ConfigParameter(
                key="device",
                value=self._config.get("device", "/dev/video0"),
                type="string",
                description="Camera device path, index, or serial for operator conversation stream",
                required=True,
            ),
            ConfigParameter(
                key="resolution",
                value=self._config.get("resolution", "1280x720"),
                type="choice",
                choices=["640x480", "1280x720", "1920x1080"],
                description="Conversation stream resolution",
                required=False,
            ),
            ConfigParameter(
                key="framerate",
                value=self._config.get("framerate", 30),
                type="int",
                description="Conversation stream framerate",
                required=False,
            ),
            ConfigParameter(
                key="jpeg_quality",
                value=self._config.get("jpeg_quality", 85),
                type="int",
                description="MJPEG quality (10-95)",
                required=False,
            ),
        ]
