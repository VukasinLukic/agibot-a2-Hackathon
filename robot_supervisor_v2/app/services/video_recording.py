"""
Video recording service.

Provides an operator-controlled local camera preview, image capture, and MP4
recording workflow for the supervisor UI.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Deque, Dict, List, Optional, Tuple

from livekit_shared.video_devices import resolve_camera_device

from ..video_stream import parse_resolution
from .base import BaseService, ConfigParameter, ServiceState

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - runtime dependency
    cv2 = None  # type: ignore

try:
    from robot_services.vision.detection.card_capture import CardCaptureProcessor
    from robot_services.vision.detection.defaults import DEFAULT_CARD_CAPTURE_FPS
    from robot_services.vision.detection.debug_overlay import (
        card_capture_status_line,
        draw_card_capture_preview,
        draw_portrait_debug_preview,
        json_safe_debug_info,
        portrait_debug_status_line,
    )
except ImportError:  # pragma: no cover - runtime environment mismatch
    CardCaptureProcessor = None  # type: ignore
    DEFAULT_CARD_CAPTURE_FPS = 8.0  # type: ignore
    card_capture_status_line = None  # type: ignore
    draw_card_capture_preview = None  # type: ignore
    draw_portrait_debug_preview = None  # type: ignore
    json_safe_debug_info = None  # type: ignore
    portrait_debug_status_line = None  # type: ignore

try:
    _vision_detection_dir = Path(__file__).resolve().parents[3] / "robot_services" / "vision" / "detection"
    if str(_vision_detection_dir) not in sys.path:
        sys.path.insert(0, str(_vision_detection_dir))
    from robot_services.vision.detection.main import PersonLockProcessor
    from robot_services.vision.detection.defaults import DEFAULT_DETECTION_FPS
except ImportError:  # pragma: no cover - optional heavy runtime dependency
    PersonLockProcessor = None  # type: ignore
    DEFAULT_DETECTION_FPS = 8.0  # type: ignore


DEFAULT_DEVICE = "/dev/video10"
DEFAULT_RESOLUTION = "960x540"
DEFAULT_FRAMERATE = 30.0
DEFAULT_OUTPUT_DIR = "tmp/video_recording_service"
DEFAULT_JPEG_QUALITY = 90
DEFAULT_ID_DEBUG_MODE = "capture"
DEFAULT_CV_DEBUG_MODE = "id_capture"
DEFAULT_ID_DEBUG_CAPTURE_IMAGE_MODE = "crop"
EVENT_LIMIT = 40
ID_DEBUG_MODES = {"capture", "portrait"}
CV_DEBUG_MODES = {"id_capture", "portrait_edges", "vision_dispatch"}
ID_CV_DEBUG_MODES = {"id_capture", "portrait_edges"}
ID_DEBUG_CAPTURE_IMAGE_MODES = {"crop", "whole"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".mp4"}
SAFE_BASENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")


def _device_to_index(device: str) -> Optional[int]:
    if device.isdigit():
        return int(device)
    if device.startswith("/dev/video"):
        suffix = device.replace("/dev/video", "")
        if suffix.isdigit():
            return int(suffix)
    return None


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _id_mode_to_cv_mode(mode: str) -> str:
    return "portrait_edges" if mode == "portrait" else "id_capture"


def _cv_mode_to_id_mode(mode: str) -> str:
    return "portrait" if mode == "portrait_edges" else "capture"


class _LocalVisionDispatchPublisher:
    """Capture production dispatch lock events locally instead of POSTing to supervisor."""

    def __init__(self, service: "VideoRecordingService"):
        self.service = service
        self.last_locked = False

    def reset(self) -> None:
        self.last_locked = False

    def publish_if_changed(self, locked_track_id, timings=None):
        is_locked = locked_track_id is not None
        if is_locked == self.last_locked:
            return None

        self.last_locked = is_locked
        self.service._record_vision_dispatch_event(
            locked=is_locked,
            track_id=locked_track_id,
            timings=timings or {},
        )
        return "locked" if is_locked else "unlocked"


class VideoRecordingService(BaseService):
    """In-process camera preview/capture/recording service."""

    def __init__(self, name: str, config: Dict[str, Any]):
        config.setdefault("device", DEFAULT_DEVICE)
        config.setdefault("resolution", DEFAULT_RESOLUTION)
        config.setdefault("framerate", DEFAULT_FRAMERATE)
        config.setdefault("output_dir", DEFAULT_OUTPUT_DIR)
        config.setdefault("record_overlay_enabled", False)
        config.setdefault("id_debug_enabled", False)
        config.setdefault("id_debug_overlay_enabled", False)
        config.setdefault("id_debug_record_captures_enabled", False)
        config.setdefault("id_debug_mode", DEFAULT_ID_DEBUG_MODE)
        config.setdefault("id_debug_capture_image_mode", DEFAULT_ID_DEBUG_CAPTURE_IMAGE_MODE)
        config.setdefault("cv_debug_enabled", bool(config.get("id_debug_enabled", False)))
        config.setdefault("cv_debug_mode", _id_mode_to_cv_mode(str(config.get("id_debug_mode", DEFAULT_ID_DEBUG_MODE))))
        config.setdefault("manual_only", True)
        config.setdefault("optional", True)
        super().__init__(
            name=name,
            display_name=config.get("display_name", "Video Recording Service"),
            config=config,
        )
        self._capture = None
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._recording_lock = threading.Lock()
        self._latest_frame = None
        self._latest_save_frame = None
        self._latest_jpeg: Optional[bytes] = None
        self._frame_seq = 0
        self._last_frame_at: Optional[float] = None
        self._actual_width: Optional[int] = None
        self._actual_height: Optional[int] = None
        self._actual_fps: Optional[float] = None
        self._recording_process: Optional[subprocess.Popen] = None
        self._recording_temp_path: Optional[Path] = None
        self._recording_final_path: Optional[Path] = None
        self._recording_started_at: Optional[float] = None
        self._recording_error: Optional[str] = None
        self._recording_metadata_snapshot: Optional[Dict[str, Any]] = None
        self._id_debug_processor = None
        self._id_debug_request_id: Optional[str] = None
        self._id_debug_captured_count = 0
        self._id_debug_last_status: Optional[str] = None
        self._id_debug_last_capture: Optional[Dict[str, Any]] = None
        self._id_debug_last_error: Optional[str] = None
        self._id_debug_last_process_at: Optional[float] = None
        self._id_debug_capture_completed = False
        self._id_debug_card_capture_fps_value = float(DEFAULT_CARD_CAPTURE_FPS)
        self._vision_dispatch_processor = None
        self._vision_dispatch_publisher = None
        self._vision_dispatch_event_count = 0
        self._vision_dispatch_locked = False
        self._vision_dispatch_track_id: Optional[str] = None
        self._vision_dispatch_last_event_at: Optional[float] = None
        self._vision_dispatch_last_status: Optional[str] = None
        self._vision_dispatch_last_error: Optional[str] = None
        self._vision_dispatch_last_process_at: Optional[float] = None
        self._vision_dispatch_detection_fps_value = float(DEFAULT_DETECTION_FPS)
        self._vision_dispatch_current_frame = None
        self._vision_dispatch_last_capture: Optional[Dict[str, Any]] = None
        self._event_lock = threading.Lock()
        self._events: Deque[Dict[str, Any]] = deque(maxlen=EVENT_LIMIT)
        self._event_seq = 0

    @property
    def output_dir(self) -> Path:
        return Path(str(self._config.get("output_dir", DEFAULT_OUTPUT_DIR))).expanduser()

    def _capture_settings(self) -> Tuple[str, Optional[int], Optional[int], float, int]:
        device = str(self._config.get("device", DEFAULT_DEVICE))
        width, height = parse_resolution(str(self._config.get("resolution", DEFAULT_RESOLUTION)))
        framerate_raw = self._config.get("framerate", DEFAULT_FRAMERATE)
        framerate = float(framerate_raw) if framerate_raw not in (None, "") else DEFAULT_FRAMERATE
        if framerate <= 0:
            raise RuntimeError("Video recording framerate must be greater than 0")
        jpeg_quality = int(self._config.get("jpeg_quality", DEFAULT_JPEG_QUALITY))
        jpeg_quality = max(10, min(95, jpeg_quality))
        return device, width, height, framerate, jpeg_quality

    def _open_capture(self):
        if cv2 is None:  # pragma: no cover - runtime dependency
            raise RuntimeError("OpenCV is required for Video Recording Service")

        device, width, height, framerate, jpeg_quality = self._capture_settings()
        resolved_device = resolve_camera_device(device)
        index = _device_to_index(resolved_device)
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else 0
        capture = cv2.VideoCapture(index if index is not None else resolved_device, backend)
        if not capture.isOpened():
            raise RuntimeError(f"Unable to open camera device {device} (resolved to {resolved_device})")

        if width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv2.CAP_PROP_FPS, framerate)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        ret, frame = capture.read()
        if not ret or frame is None:
            capture.release()
            raise RuntimeError(f"Failed to read an initial frame from camera device {device}")

        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or frame.shape[1]
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or frame.shape[0]
        actual_fps = float(capture.get(cv2.CAP_PROP_FPS) or framerate)
        self._store_frame(frame, jpeg_quality)
        self._actual_width = actual_width
        self._actual_height = actual_height
        self._actual_fps = actual_fps
        return capture

    async def start(self) -> None:
        if self._state not in (ServiceState.STOPPED, ServiceState.FAILED):
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None
        self._recording_error = None
        self._stop_event.clear()

        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._capture = await asyncio.to_thread(self._open_capture)
            self._reset_cv_debug()
            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name=f"{self.name}-capture",
                daemon=True,
            )
            self._capture_thread.start()
            self._state = ServiceState.RUNNING
            self._mark_started()
            self._append_event(
                "service_started",
                "Service started",
                message=f"Preview started on {self._config.get('device', DEFAULT_DEVICE)}.",
            )
        except Exception as exc:
            self._state = ServiceState.FAILED
            self._last_error = str(exc)
            self._release_capture()
            self._clear_cv_debug_processors()
            self._append_event(
                "service_error",
                "Service failed to start",
                message=str(exc),
                level="error",
            )
            raise

    async def stop(self) -> None:
        if self._state == ServiceState.STOPPED:
            return

        self._state = ServiceState.STOPPING
        if self.is_recording:
            await asyncio.to_thread(self.stop_recording)

        self._stop_event.set()
        thread = self._capture_thread
        if thread and thread.is_alive():
            await asyncio.to_thread(thread.join, 3.0)
        self._capture_thread = None
        self._release_capture()

        with self._frame_lock:
            self._latest_frame = None
            self._latest_save_frame = None
            self._latest_jpeg = None
            self._frame_seq += 1
            self._last_frame_at = None

        self._clear_cv_debug_processors()
        self._config["cv_debug_enabled"] = False
        self._config["id_debug_enabled"] = False
        self._config["id_debug_overlay_enabled"] = False
        self._state = ServiceState.STOPPED
        self._start_time = None
        self._append_event("service_stopped", "Service stopped", message="Camera preview stopped.")

    async def check_health(self) -> bool:
        if self._state != ServiceState.RUNNING:
            return False
        if not self._capture_thread or not self._capture_thread.is_alive():
            return False
        last_frame_at = self._last_frame_at
        if last_frame_at is None:
            return True
        return (time.time() - last_frame_at) < 5.0

    async def update_config(self, updates: Dict[str, Any]) -> None:
        if self.is_recording:
            raise RuntimeError("Cannot update Video Recording Service config while recording")

        previous_cv_debug_enabled = self._cv_debug_enabled()
        previous_cv_debug_mode = self._cv_debug_mode()
        needs_restart = any(key in updates for key in {"device", "resolution", "framerate"})
        self._config.update(updates)

        if self._state == ServiceState.RUNNING:
            if needs_restart:
                await self.restart()
                self._config.update(updates)
                if self._cv_debug_enabled():
                    self._reset_cv_debug()
                return

            next_cv_debug_enabled = self._cv_debug_enabled()
            next_cv_debug_mode = self._cv_debug_mode()
            if not next_cv_debug_enabled:
                self._clear_cv_debug_processors()
            elif (
                not previous_cv_debug_enabled
                or previous_cv_debug_mode != next_cv_debug_mode
            ):
                self._reset_cv_debug()

    def get_log_path(self) -> Optional[str]:
        return None

    def get_backend_type(self) -> str:
        return "in_process"

    def get_config_parameters(self) -> List[ConfigParameter]:
        return [
            ConfigParameter(
                key="device",
                value=self._config.get("device", DEFAULT_DEVICE),
                type="string",
                description="Camera device path, index, or serial",
                required=True,
            ),
            ConfigParameter(
                key="resolution",
                value=self._config.get("resolution", DEFAULT_RESOLUTION),
                type="string",
                description="Camera resolution as WIDTHxHEIGHT",
                required=False,
            ),
            ConfigParameter(
                key="framerate",
                value=self._config.get("framerate", DEFAULT_FRAMERATE),
                type="number",
                description="Requested camera capture FPS",
                required=False,
            ),
            ConfigParameter(
                key="output_dir",
                value=self._config.get("output_dir", DEFAULT_OUTPUT_DIR),
                type="string",
                description="Local directory for image captures and recordings",
                required=False,
            ),
        ]

    @property
    def is_recording(self) -> bool:
        return self._recording_process is not None

    def _append_event(
        self,
        event_type: str,
        title: str,
        *,
        message: Optional[str] = None,
        level: str = "info",
        file: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        with self._event_lock:
            self._event_seq += 1
            event = {
                "id": f"{int(now * 1000)}-{self._event_seq}",
                "timestamp": now,
                "type": event_type,
                "level": level,
                "title": title,
                "message": message,
                "file": file,
                "details": details or {},
            }
            self._events.appendleft(event)
        return event

    def events_status(self) -> List[Dict[str, Any]]:
        with self._event_lock:
            return list(self._events)

    def _release_capture(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def _capture_loop(self) -> None:
        _, _, _, framerate, jpeg_quality = self._capture_settings()
        interval = max(1.0 / framerate, 0.01)
        next_frame_at = time.monotonic()

        while not self._stop_event.is_set():
            try:
                ret, frame = self._capture.read() if self._capture is not None else (False, None)
                if not ret or frame is None:
                    time.sleep(max(interval, 0.2))
                    continue

                preview_frame = self._build_preview_frame(frame)
                self._store_frame(frame, jpeg_quality, preview_frame=preview_frame)
                self._write_recording_frame(preview_frame if self._record_overlay_enabled() else frame)
                next_frame_at += interval
                sleep_for = next_frame_at - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_frame_at = time.monotonic()
            except Exception as exc:
                self._last_error = str(exc)
                time.sleep(max(interval, 0.2))

    def _store_frame(self, frame, jpeg_quality: int, *, preview_frame=None) -> None:
        display_frame = preview_frame if preview_frame is not None else frame
        success, buffer = cv2.imencode(
            ".jpg",
            display_frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality],
        )
        if not success:
            raise RuntimeError("Failed to encode frame as JPEG")

        with self._frame_lock:
            self._latest_frame = frame.copy()
            self._latest_save_frame = display_frame.copy()
            self._latest_jpeg = buffer.tobytes()
            self._frame_seq += 1
            self._last_frame_at = time.time()

    def _id_debug_enabled(self) -> bool:
        return self._cv_debug_enabled() and self._cv_debug_mode() in ID_CV_DEBUG_MODES

    def _cv_debug_enabled(self) -> bool:
        return bool(self._config.get("cv_debug_enabled", False)) or bool(self._config.get("id_debug_enabled", False))

    def _cv_debug_mode(self) -> str:
        if bool(self._config.get("id_debug_enabled", False)) and not bool(self._config.get("cv_debug_enabled", False)):
            return _id_mode_to_cv_mode(str(self._config.get("id_debug_mode", DEFAULT_ID_DEBUG_MODE)))
        mode = str(self._config.get("cv_debug_mode") or "")
        if not mode:
            mode = _id_mode_to_cv_mode(str(self._config.get("id_debug_mode", DEFAULT_ID_DEBUG_MODE)))
        return mode if mode in CV_DEBUG_MODES else DEFAULT_CV_DEBUG_MODE

    def _record_overlay_enabled(self) -> bool:
        return bool(self._config.get("record_overlay_enabled", False))

    def _content_variant(self) -> str:
        return "overlay" if self._record_overlay_enabled() else "normal"

    def _id_debug_capture_image_mode(self) -> str:
        if self._record_overlay_enabled():
            return "whole"
        mode = str(self._config.get("id_debug_capture_image_mode", DEFAULT_ID_DEBUG_CAPTURE_IMAGE_MODE))
        return mode if mode in ID_DEBUG_CAPTURE_IMAGE_MODES else DEFAULT_ID_DEBUG_CAPTURE_IMAGE_MODE

    def _vision_dispatch_debug_enabled(self) -> bool:
        return self._cv_debug_enabled() and self._cv_debug_mode() == "vision_dispatch"

    def _id_debug_overlay_enabled(self) -> bool:
        return self._id_debug_enabled()

    def _id_debug_record_captures_enabled(self) -> bool:
        return self._id_debug_enabled() and bool(self._config.get("id_debug_record_captures_enabled", False))

    def _cv_debug_record_captures_enabled(self) -> bool:
        return self._cv_debug_enabled() and bool(self._config.get("id_debug_record_captures_enabled", False))

    def _vision_dispatch_record_captures_enabled(self) -> bool:
        return self._vision_dispatch_debug_enabled() and self._cv_debug_record_captures_enabled()

    def _id_debug_mode(self) -> str:
        return _cv_mode_to_id_mode(self._cv_debug_mode())

    def _recording_type(self) -> str:
        if not self._cv_debug_enabled():
            return "standard"
        if self._cv_debug_mode() == "vision_dispatch":
            return "vision_dispatch"
        return "id_scan"

    def _id_debug_card_capture_fps(self) -> float:
        return self._id_debug_card_capture_fps_value

    def set_id_debug_card_capture_fps(self, value: Any) -> None:
        try:
            fps = float(value)
        except (TypeError, ValueError):
            fps = float(DEFAULT_CARD_CAPTURE_FPS)
        self._id_debug_card_capture_fps_value = max(0.0, fps)

    def _vision_dispatch_detection_fps(self) -> float:
        return self._vision_dispatch_detection_fps_value

    def set_vision_dispatch_detection_fps(self, value: Any) -> None:
        try:
            fps = float(value)
        except (TypeError, ValueError):
            fps = float(DEFAULT_DETECTION_FPS)
        self._vision_dispatch_detection_fps_value = max(0.0, fps)

    def _id_debug_available(self) -> bool:
        return all(
            dependency is not None
            for dependency in (
                CardCaptureProcessor,
                card_capture_status_line,
                draw_card_capture_preview,
                draw_portrait_debug_preview,
                json_safe_debug_info,
                portrait_debug_status_line,
            )
        )

    def _vision_dispatch_debug_available(self) -> bool:
        return PersonLockProcessor is not None

    def _cv_debug_available(self) -> bool:
        if self._cv_debug_mode() == "vision_dispatch":
            return self._vision_dispatch_debug_available()
        return self._id_debug_available()

    def _dispose_debug_processor(self, processor: Any) -> None:
        for method_name in ("close", "release", "shutdown"):
            method = getattr(processor, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass
                break

    def _clear_cv_debug_processors(self) -> None:
        if self._id_debug_processor is not None:
            self._dispose_debug_processor(self._id_debug_processor)
        if self._vision_dispatch_processor is not None:
            self._dispose_debug_processor(self._vision_dispatch_processor)
        self._id_debug_processor = None
        self._vision_dispatch_processor = None
        self._vision_dispatch_publisher = None
        self._vision_dispatch_current_frame = None
        self._vision_dispatch_last_capture = None

    def _reset_id_debug(self, *, log_event: bool = False) -> None:
        self._id_debug_captured_count = 0
        self._id_debug_last_status = None
        self._id_debug_last_capture = None
        self._id_debug_last_error = None
        self._id_debug_last_process_at = None
        self._id_debug_capture_completed = False
        self._id_debug_request_id = f"video-recording-debug-{int(time.time())}"
        if self._id_debug_enabled() and self._id_debug_available():
            self._id_debug_processor = CardCaptureProcessor()
            self._id_debug_processor.reset(self._id_debug_request_id)
        else:
            self._id_debug_processor = None
        if log_event:
            self._append_event(
                "id_debug_reset",
                "ID debug reset",
                message="Local ID scan debug state was reset.",
                details={"request_id": self._id_debug_request_id, "mode": self._id_debug_mode()},
            )

    def _reset_vision_dispatch_debug(self, *, log_event: bool = False) -> None:
        self._vision_dispatch_event_count = 0
        self._vision_dispatch_locked = False
        self._vision_dispatch_track_id = None
        self._vision_dispatch_last_event_at = None
        self._vision_dispatch_last_status = None
        self._vision_dispatch_last_error = None
        self._vision_dispatch_last_process_at = None
        self._vision_dispatch_current_frame = None
        self._vision_dispatch_last_capture = None
        if self._vision_dispatch_debug_enabled() and self._vision_dispatch_debug_available():
            self._vision_dispatch_publisher = _LocalVisionDispatchPublisher(self)
            self._vision_dispatch_processor = PersonLockProcessor(publisher=self._vision_dispatch_publisher)
        else:
            self._vision_dispatch_processor = None
            self._vision_dispatch_publisher = None
        if log_event:
            self._append_event(
                "vision_dispatch_reset",
                "Vision dispatch debug reset",
                message="Local vision dispatch debug state was reset.",
                details={"mode": "vision_dispatch"},
            )

    def _reset_cv_debug(self, *, log_event: bool = False) -> None:
        self._reset_id_debug(log_event=log_event and self._id_debug_enabled())
        self._reset_vision_dispatch_debug(log_event=log_event and self._vision_dispatch_debug_enabled())

    def reset_id_debug(self) -> Dict[str, Any]:
        self._reset_id_debug(log_event=True)
        return self.id_debug_status()

    def reset_cv_debug(self) -> Dict[str, Any]:
        self._reset_cv_debug(log_event=True)
        return self.cv_debug_status()

    def _id_debug_request_state(self) -> Dict[str, Any]:
        if not self._id_debug_request_id:
            self._id_debug_request_id = f"video-recording-debug-{int(time.time())}"
        return {
            "request_id": self._id_debug_request_id,
            "target_type": "id_card",
            "source": "video-recording-service",
            "expires_at": time.time() + 3600.0,
        }

    def _process_id_debug_capture_result(
        self,
        processor,
        result: Optional[Dict[str, Any]],
        *,
        frame=None,
        display_frame=None,
    ) -> None:
        if not result:
            return

        self._id_debug_captured_count += 1
        if self._id_debug_record_captures_enabled():
            self._id_debug_last_capture = self._save_id_debug_capture(
                result,
                frame=frame,
                display_frame=display_frame,
            )
            event_title = "ID capture saved"
            event_message = "Production ID capture processor emitted and saved a capture."
            event_file = self._id_debug_last_capture
        else:
            self._id_debug_last_capture = None
            event_title = "ID capture detected"
            event_message = "Production ID capture processor emitted a capture. Recording captures is off."
            event_file = None
        self._append_event(
            "id_debug_capture",
            event_title,
            message=event_message,
            level="success",
            file=event_file,
            details={
                "request_id": result.get("request_id"),
                "status": result.get("status"),
                "mode": self._id_debug_mode(),
                "image_mode": self._id_debug_capture_image_mode(),
                "content_variant": self._content_variant() if self._id_debug_capture_image_mode() == "whole" else "normal",
                "saved": self._id_debug_record_captures_enabled(),
            },
        )
        self._id_debug_capture_completed = True
        self._id_debug_request_id = None
        processor.reset()

    def _process_id_debug_capture_frame(self, processor, frame) -> Optional[Dict[str, Any]]:
        if self._id_debug_capture_completed:
            return None
        now = time.monotonic()

        fps = self._id_debug_card_capture_fps()
        if fps > 0:
            interval = 1.0 / fps
            if self._id_debug_last_process_at is not None and now - self._id_debug_last_process_at < interval:
                return None

        self._id_debug_last_process_at = now
        return processor.process_frame(frame, self._id_debug_request_state())

    def _record_vision_dispatch_event(self, *, locked: bool, track_id: Any, timings: Dict[str, Any]) -> None:
        now = time.time()
        normalized_track_id = str(track_id) if track_id is not None else None
        self._vision_dispatch_event_count += 1
        self._vision_dispatch_locked = locked
        self._vision_dispatch_track_id = normalized_track_id
        self._vision_dispatch_last_event_at = now
        self._vision_dispatch_last_status = (
            f"{'locked' if locked else 'unlocked'}"
            + (f" track_id={normalized_track_id}" if normalized_track_id else "")
        )
        event_file = None
        if self._vision_dispatch_record_captures_enabled() and self._vision_dispatch_current_frame is not None:
            try:
                event_file = self._save_vision_dispatch_capture(
                    self._vision_dispatch_current_frame,
                    locked=locked,
                    track_id=normalized_track_id,
                    timings=timings,
                )
                self._vision_dispatch_last_capture = event_file
            except Exception as exc:
                self._vision_dispatch_last_error = f"Vision dispatch capture failed: {exc}"
                self._append_event(
                    "vision_dispatch_capture_error",
                    "Vision dispatch capture failed",
                    message=str(exc),
                    level="error",
                )
        self._append_event(
            "vision_dispatch_locked" if locked else "vision_dispatch_unlocked",
            "Person locked" if locked else "Person unlocked",
            message=self._vision_dispatch_last_status,
            level="success" if locked else "info",
            file=event_file,
            details={
                "locked": locked,
                "track_id": normalized_track_id,
                "timings": timings,
                "saved": event_file is not None,
            },
        )

    def _process_vision_dispatch_frame(self, frame) -> None:
        if not self._vision_dispatch_debug_enabled():
            return
        if not self._vision_dispatch_debug_available():
            message = "Vision dispatch debug dependencies are not available"
            if self._vision_dispatch_last_error != message:
                self._append_event("vision_dispatch_error", "Vision dispatch unavailable", message=message, level="error")
            self._vision_dispatch_last_error = message
            return
        if self._vision_dispatch_processor is None:
            self._reset_vision_dispatch_debug()
        processor = self._vision_dispatch_processor
        if processor is None:
            return

        now = time.monotonic()
        fps = self._vision_dispatch_detection_fps()
        if fps > 0:
            interval = 1.0 / fps
            if self._vision_dispatch_last_process_at is not None and now - self._vision_dispatch_last_process_at < interval:
                return
        self._vision_dispatch_last_process_at = now

        self._vision_dispatch_current_frame = frame.copy()
        try:
            locked_track_id, event = processor.process_frame(frame)
        finally:
            self._vision_dispatch_current_frame = None
        normalized_track_id = str(locked_track_id) if locked_track_id is not None else None
        self._vision_dispatch_locked = locked_track_id is not None
        self._vision_dispatch_track_id = normalized_track_id
        if event:
            self._vision_dispatch_last_status = (
                f"{event}"
                + (f" track_id={normalized_track_id}" if normalized_track_id else "")
            )
        else:
            self._vision_dispatch_last_status = (
                "locked" if locked_track_id is not None else "waiting for lock"
            )

    def _build_preview_frame(self, frame):
        if not self._cv_debug_enabled():
            return frame

        if self._cv_debug_mode() == "vision_dispatch":
            try:
                self._process_vision_dispatch_frame(frame)
            except Exception as exc:
                message = str(exc)
                if self._vision_dispatch_last_error != message:
                    self._append_event("vision_dispatch_error", "Vision dispatch debug error", message=message, level="error")
                self._vision_dispatch_last_error = message
                self._last_error = f"Vision dispatch debug failed: {exc}"
            return frame

        if not self._id_debug_available():
            message = "ID debug overlay dependencies are not available"
            if self._id_debug_last_error != message:
                self._append_event("id_debug_error", "ID debug unavailable", message=message, level="error")
            self._id_debug_last_error = message
            return frame
        if self._id_debug_processor is None:
            self._reset_id_debug()
        processor = self._id_debug_processor
        if processor is None:
            return frame

        try:
            if self._id_debug_mode() == "portrait":
                debug_info = processor.inspect_portrait_edges(frame, max_candidates=8)
                # Keep production capture behavior authoritative; this is the
                # same method used by the live vision runtime.
                result = self._process_id_debug_capture_frame(processor, frame)
                self._id_debug_last_status = portrait_debug_status_line(debug_info)
                preview_frame = draw_portrait_debug_preview(frame, debug_info)
                self._process_id_debug_capture_result(
                    processor,
                    result,
                    frame=frame,
                    display_frame=preview_frame,
                )
                return preview_frame

            debug_info = processor.debug_frame(frame)
            candidate = processor._detect_best_candidate(frame)
            # Keep production capture behavior authoritative; overlay diagnostics
            # never decide whether a capture event is emitted.
            result = self._process_id_debug_capture_frame(processor, frame)
            self._id_debug_last_status = card_capture_status_line(candidate, processor, debug_info)
            preview_frame = draw_card_capture_preview(
                frame,
                candidate,
                processor,
                self._id_debug_captured_count,
                debug_info,
            )
            self._process_id_debug_capture_result(
                processor,
                result,
                frame=frame,
                display_frame=preview_frame,
            )
            return preview_frame
        except Exception as exc:
            message = str(exc)
            if self._id_debug_last_error != message:
                self._append_event("id_debug_error", "ID debug error", message=message, level="error")
            self._id_debug_last_error = message
            self._last_error = f"ID debug overlay failed: {exc}"
            return frame

    def get_latest_jpeg(self) -> Tuple[int, Optional[bytes]]:
        with self._frame_lock:
            return self._frame_seq, self._latest_jpeg

    async def mjpeg_stream(self) -> AsyncGenerator[bytes, None]:
        last_seq = -1
        while self._state == ServiceState.RUNNING:
            seq, jpeg = self.get_latest_jpeg()
            if jpeg and seq != last_seq:
                last_seq = seq
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                )
                yield header + jpeg + b"\r\n"
            await asyncio.sleep(0.02)

    def capture_image(self) -> Dict[str, Any]:
        if self._state != ServiceState.RUNNING:
            raise RuntimeError("Start Video Recording Service first")

        with self._frame_lock:
            source_frame = self._latest_save_frame if self._record_overlay_enabled() else self._latest_frame
            frame = None if source_frame is None else source_frame.copy()
        if frame is None:
            raise RuntimeError("No camera frame is available yet")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self._next_available_path(f"capture-{_now_stamp()}", ".jpg")
        if not cv2.imwrite(str(path), frame):
            raise RuntimeError("Failed to write image capture")
        self._write_manual_capture_metadata(path)
        payload = self._file_payload(path)
        self._append_event(
            "image_capture",
            "Image captured",
            message=payload["filename"],
            level="success",
            file=payload,
        )
        return payload

    def _write_manual_capture_metadata(self, path: Path) -> None:
        metadata = {
            "schema_version": 1,
            "filename": path.name,
            "type": "image_capture",
            "timestamp": time.time(),
            "content_variant": self._content_variant(),
            "record_overlay_enabled": self._record_overlay_enabled(),
            "device": self._config.get("device", DEFAULT_DEVICE),
            "resolution": self._config.get("resolution", DEFAULT_RESOLUTION),
            "framerate": self._config.get("framerate", DEFAULT_FRAMERATE),
            "actual_width": self._actual_width,
            "actual_height": self._actual_height,
            "actual_fps": self._actual_fps,
            "cv_debug_enabled": self._cv_debug_enabled(),
            "cv_debug_mode": self._cv_debug_mode(),
        }
        self._sidecar_metadata_path(path).write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _id_debug_dir(self) -> Path:
        return self.output_dir / "id_debug"

    def _vision_dispatch_debug_dir(self) -> Path:
        return self.output_dir / "vision_dispatch_debug"

    def _save_id_debug_capture(self, result: Dict[str, Any], *, frame=None, display_frame=None) -> Dict[str, Any]:
        metadata = result.get("metadata") or {}
        image_mode = self._id_debug_capture_image_mode()
        content_variant = self._content_variant() if image_mode == "whole" else "normal"

        debug_dir = self._id_debug_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = _now_stamp()
        image_path = debug_dir / f"id-debug-capture-{timestamp}-{self._id_debug_captured_count:02d}.jpg"
        if image_mode == "whole":
            source_frame = display_frame if self._record_overlay_enabled() else frame
            if source_frame is None:
                raise RuntimeError("Whole-image ID debug capture requested but no frame was available")
            if not cv2.imwrite(str(image_path), source_frame):
                raise RuntimeError("Failed to write whole-image ID debug capture")
        else:
            image_base64 = metadata.get("image_jpeg_base64")
            if not isinstance(image_base64, str) or not image_base64:
                raise RuntimeError("ID debug capture result did not include image_jpeg_base64")
            image_path.write_bytes(base64.b64decode(image_base64))
        metadata_path = image_path.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "filename": image_path.name,
                    "recording_type": "id_scan",
                    "cv_debug_mode": self._cv_debug_mode(),
                    "image_mode": image_mode,
                    "content_variant": content_variant,
                    "record_overlay_enabled": self._record_overlay_enabled(),
                    "request_id": result.get("request_id"),
                    "status": result.get("status"),
                    "timestamp": result.get("timestamp"),
                    "metadata": {
                        key: value
                        for key, value in metadata.items()
                        if key != "image_jpeg_base64"
                    },
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        payload = self._file_payload(image_path)
        payload["metadata_id"] = metadata_path.name
        payload["recording_type"] = "id_scan"
        payload["image_mode"] = image_mode
        payload["content_variant"] = content_variant
        return payload

    def _save_vision_dispatch_capture(
        self,
        frame,
        *,
        locked: bool,
        track_id: Optional[str],
        timings: Dict[str, Any],
    ) -> Dict[str, Any]:
        debug_dir = self._vision_dispatch_debug_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = _now_stamp()
        event_name = "lock" if locked else "unlock"
        image_path = debug_dir / f"vision-dispatch-{event_name}-{timestamp}-{self._vision_dispatch_event_count:02d}.jpg"
        if not cv2.imwrite(str(image_path), frame):
            raise RuntimeError("Failed to write vision dispatch capture")

        metadata_path = image_path.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "recording_type": "vision_dispatch",
                    "event": "locked" if locked else "unlocked",
                    "track_id": track_id,
                    "timestamp": time.time(),
                    "timings": timings,
                    "cv_debug_mode": "vision_dispatch",
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        payload = self._file_payload(image_path)
        payload["metadata_id"] = metadata_path.name
        payload["recording_type"] = "vision_dispatch"
        payload["cv_debug_mode"] = "vision_dispatch"
        return payload

    def start_recording(self) -> Dict[str, Any]:
        if self._state != ServiceState.RUNNING:
            raise RuntimeError("Start Video Recording Service first")
        with self._recording_lock:
            if self._recording_process is not None:
                raise RuntimeError("Recording is already active")
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                self._append_event(
                    "recording_error",
                    "Recording failed to start",
                    message="ffmpeg is required for MP4 H.264 recording but was not found",
                    level="error",
                )
                raise RuntimeError("ffmpeg is required for MP4 H.264 recording but was not found")

            width = self._actual_width
            height = self._actual_height
            fps = float(self._config.get("framerate", DEFAULT_FRAMERATE))
            if not width or not height:
                self._append_event(
                    "recording_error",
                    "Recording failed to start",
                    message="Camera frame dimensions are not available yet",
                    level="error",
                )
                raise RuntimeError("Camera frame dimensions are not available yet")

            self.output_dir.mkdir(parents=True, exist_ok=True)
            final_path = self._next_available_path(f"recording-{_now_stamp()}", ".mp4")
            temp_path = final_path.with_name(f".{final_path.stem}.tmp{final_path.suffix}")
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                f"{width}x{height}",
                "-r",
                f"{fps:g}",
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(temp_path),
            ]
            try:
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except Exception as exc:
                self._append_event(
                    "recording_error",
                    "Recording failed to start",
                    message=str(exc),
                    level="error",
                )
                raise RuntimeError(f"Failed to start ffmpeg: {exc}") from exc

            self._recording_process = process
            self._recording_temp_path = temp_path
            self._recording_final_path = final_path
            self._recording_started_at = time.time()
            self._recording_metadata_snapshot = self._recording_metadata_started(final_path, width, height, fps)
            self._recording_error = None
            self._append_event(
                "recording_started",
                "Recording started",
                message=final_path.name,
                details={
                    "filename": final_path.name,
                    "recording_type": self._recording_metadata_snapshot["recording_type"],
                },
            )
            return self.recording_status()

    def _write_recording_frame(self, frame) -> None:
        process: Optional[subprocess.Popen]
        with self._recording_lock:
            process = self._recording_process
        if not process or not process.stdin:
            return

        try:
            process.stdin.write(frame.tobytes())
        except Exception as exc:
            self._recording_error = str(exc)
            self._last_error = f"Recording failed: {exc}"
            try:
                self.stop_recording()
            except Exception as stop_exc:
                self._recording_error = str(stop_exc)

    def stop_recording(self) -> Dict[str, Any]:
        with self._recording_lock:
            process = self._recording_process
            temp_path = self._recording_temp_path
            final_path = self._recording_final_path
            self._recording_process = None
            self._recording_temp_path = None
            self._recording_final_path = None
            self._recording_started_at = None
            metadata_snapshot = self._recording_metadata_snapshot
            self._recording_metadata_snapshot = None

        if not process:
            raise RuntimeError("Recording is not active")

        stderr = b""
        if process.stdin:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
            process.stdin = None
        try:
            _, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()

        if process.returncode != 0:
            if temp_path and temp_path.exists():
                temp_path.unlink()
            message = stderr.decode("utf-8", errors="replace").strip() or f"ffmpeg exited with {process.returncode}"
            self._recording_error = message
            self._append_event("recording_error", "Recording failed", message=message, level="error")
            raise RuntimeError(f"Recording failed: {message}")

        if not temp_path or not final_path or not temp_path.exists():
            self._append_event(
                "recording_error",
                "Recording failed",
                message="Recording finished but the MP4 file was not created",
                level="error",
            )
            raise RuntimeError("Recording finished but the MP4 file was not created")

        os.replace(temp_path, final_path)
        self._recording_error = None
        if metadata_snapshot is not None:
            self._write_recording_metadata(final_path, metadata_snapshot)
        payload = self._file_payload(final_path)
        self._append_event(
            "recording_saved",
            "Recording saved",
            message=payload["filename"],
            level="success",
            file=payload,
        )
        return payload

    def _recording_metadata_started(self, final_path: Path, width: int, height: int, fps: float) -> Dict[str, Any]:
        cv_mode = self._cv_debug_mode()
        started_at = time.time()
        return {
            "schema_version": 1,
            "filename": final_path.name,
            "recording_type": self._recording_type(),
            "content_variant": self._content_variant(),
            "record_overlay_enabled": self._record_overlay_enabled(),
            "started_at": started_at,
            "device": self._config.get("device", DEFAULT_DEVICE),
            "resolution": self._config.get("resolution", DEFAULT_RESOLUTION),
            "framerate": fps,
            "actual_width": width,
            "actual_height": height,
            "actual_fps": self._actual_fps,
            "cv_debug_enabled": self._cv_debug_enabled(),
            "cv_debug_mode": cv_mode,
            "id_scan": {
                "enabled": self._id_debug_enabled(),
                "mode": self._id_debug_mode(),
                "record_captures_enabled": self._id_debug_record_captures_enabled(),
                "captured_count_at_start": self._id_debug_captured_count,
            },
            "vision_dispatch": {
                "enabled": self._vision_dispatch_debug_enabled(),
                "event_count_at_start": self._vision_dispatch_event_count,
                "locked_at_start": self._vision_dispatch_locked,
                "track_id_at_start": self._vision_dispatch_track_id,
            },
        }

    def _write_recording_metadata(self, final_path: Path, metadata: Dict[str, Any]) -> None:
        stopped_at = time.time()
        started_at = float(metadata.get("started_at") or stopped_at)
        metadata = dict(metadata)
        metadata.update(
            {
                "filename": final_path.name,
                "stopped_at": stopped_at,
                "duration_s": max(0.0, stopped_at - started_at),
                "id_scan": {
                    **dict(metadata.get("id_scan") or {}),
                    "captured_count_at_stop": self._id_debug_captured_count,
                },
                "vision_dispatch": {
                    **dict(metadata.get("vision_dispatch") or {}),
                    "event_count_at_stop": self._vision_dispatch_event_count,
                    "locked_at_stop": self._vision_dispatch_locked,
                    "track_id_at_stop": self._vision_dispatch_track_id,
                    "last_event_at": self._vision_dispatch_last_event_at,
                },
            }
        )
        metadata_path = self._sidecar_metadata_path(final_path)
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def recording_status(self) -> Dict[str, Any]:
        return {
            "active": self.is_recording,
            "started_at": self._recording_started_at,
            "filename": self._recording_final_path.name if self._recording_final_path else None,
            "recording_type": self._recording_metadata_snapshot.get("recording_type") if self._recording_metadata_snapshot else None,
            "content_variant": self._recording_metadata_snapshot.get("content_variant") if self._recording_metadata_snapshot else None,
            "error": self._recording_error,
        }

    def id_debug_status(self) -> Dict[str, Any]:
        return {
            "enabled": self._id_debug_enabled(),
            "overlay_enabled": self._id_debug_overlay_enabled(),
            "record_captures_enabled": self._id_debug_record_captures_enabled(),
            "mode": self._id_debug_mode(),
            "capture_image_mode": self._id_debug_capture_image_mode(),
            "available": self._id_debug_available(),
            "request_id": self._id_debug_request_id,
            "captured_count": self._id_debug_captured_count,
            "capture_completed": self._id_debug_capture_completed,
            "card_capture_fps": self._id_debug_card_capture_fps(),
            "last_status": self._id_debug_last_status,
            "last_capture": self._id_debug_last_capture,
            "error": self._id_debug_last_error,
        }

    def vision_dispatch_debug_status(self) -> Dict[str, Any]:
        return {
            "enabled": self._vision_dispatch_debug_enabled(),
            "available": self._vision_dispatch_debug_available(),
            "locked": self._vision_dispatch_locked,
            "track_id": self._vision_dispatch_track_id,
            "event_count": self._vision_dispatch_event_count,
            "last_event_at": self._vision_dispatch_last_event_at,
            "detection_fps": self._vision_dispatch_detection_fps(),
            "last_status": self._vision_dispatch_last_status,
            "last_capture": self._vision_dispatch_last_capture,
            "record_captures_enabled": self._vision_dispatch_record_captures_enabled(),
            "error": self._vision_dispatch_last_error,
        }

    def cv_debug_status(self) -> Dict[str, Any]:
        mode = self._cv_debug_mode()
        id_status = self.id_debug_status()
        dispatch_status = self.vision_dispatch_debug_status()
        last_status = dispatch_status["last_status"] if mode == "vision_dispatch" else id_status["last_status"]
        error = dispatch_status["error"] if mode == "vision_dispatch" else id_status["error"]
        return {
            "enabled": self._cv_debug_enabled(),
            "mode": mode,
            "available": self._cv_debug_available(),
            "record_captures_enabled": self._cv_debug_record_captures_enabled(),
            "last_status": last_status,
            "error": error,
            "id_capture": id_status,
            "vision_dispatch": dispatch_status,
            # Compatibility fields for the existing frontend shape.
            "request_id": id_status["request_id"],
            "captured_count": id_status["captured_count"],
            "capture_completed": id_status["capture_completed"],
            "card_capture_fps": id_status["card_capture_fps"],
            "last_capture": id_status["last_capture"],
            "capture_image_mode": id_status["capture_image_mode"],
        }

    def runtime_status(self) -> Dict[str, Any]:
        config = self.get_config()
        cv_debug = self.cv_debug_status()
        return {
            "service": self.get_status(),
            "running": self._state == ServiceState.RUNNING,
            "recording": self.recording_status(),
            "device": config.get("device", DEFAULT_DEVICE),
            "resolution": config.get("resolution", DEFAULT_RESOLUTION),
            "framerate": config.get("framerate", DEFAULT_FRAMERATE),
            "record_overlay_enabled": self._record_overlay_enabled(),
            "output_dir": str(self.output_dir),
            "actual_width": self._actual_width,
            "actual_height": self._actual_height,
            "actual_fps": self._actual_fps,
            "last_frame_at": self._last_frame_at,
            "cv_debug": cv_debug,
            "id_debug": self.id_debug_status(),
            "events": self.events_status(),
        }

    def _next_available_path(self, stem: str, suffix: str) -> Path:
        candidate = self.output_dir / f"{stem}{suffix}"
        if not candidate.exists():
            return candidate
        for index in range(2, 1000):
            candidate = self.output_dir / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
        raise RuntimeError("Unable to allocate an output filename")

    def _file_payload(self, path: Path) -> Dict[str, Any]:
        stat = path.stat()
        suffix = path.suffix.lower()
        file_id = path.name
        try:
            if self._id_debug_dir().resolve() in path.resolve().parents:
                file_id = f"id-debug--{path.name}"
            elif self._vision_dispatch_debug_dir().resolve() in path.resolve().parents:
                file_id = f"vision-dispatch--{path.name}"
        except OSError:
            pass
        metadata = self._read_sidecar_metadata(path)
        payload = {
            "id": file_id,
            "filename": path.name,
            "type": "video" if suffix == ".mp4" else "image",
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
        }
        if metadata:
            payload["metadata_id"] = self._sidecar_metadata_path(path).name
            if isinstance(metadata.get("recording_type"), str):
                payload["recording_type"] = metadata["recording_type"]
            if isinstance(metadata.get("cv_debug_mode"), str):
                payload["cv_debug_mode"] = metadata["cv_debug_mode"]
            if isinstance(metadata.get("content_variant"), str):
                payload["content_variant"] = metadata["content_variant"]
            if isinstance(metadata.get("image_mode"), str):
                payload["image_mode"] = metadata["image_mode"]
        elif suffix == ".mp4":
            payload["recording_type"] = "standard"
            payload["content_variant"] = "normal"
        return payload

    def _sidecar_metadata_path(self, path: Path) -> Path:
        return path.with_suffix(".json")

    def _read_sidecar_metadata(self, path: Path) -> Dict[str, Any]:
        metadata_path = self._sidecar_metadata_path(path)
        if not metadata_path.is_file():
            return {}
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def sidecar_metadata_for_file(self, file_id: str) -> Optional[Path]:
        path = self.resolve_file(file_id)
        metadata_path = self._sidecar_metadata_path(path)
        if metadata_path.is_file():
            return metadata_path
        return None

    def list_files(self) -> List[Dict[str, Any]]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for path in self.output_dir.iterdir():
            if not path.is_file():
                continue
            if path.name.startswith(".") or path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            files.append(self._file_payload(path))
        id_debug_dir = self._id_debug_dir()
        if id_debug_dir.exists():
            for path in id_debug_dir.iterdir():
                if not path.is_file() or path.name.startswith("."):
                    continue
                if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                payload = self._file_payload(path)
                files.append(payload)
        vision_dispatch_dir = self._vision_dispatch_debug_dir()
        if vision_dispatch_dir.exists():
            for path in vision_dispatch_dir.iterdir():
                if not path.is_file() or path.name.startswith("."):
                    continue
                if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                payload = self._file_payload(path)
                files.append(payload)
        files.sort(key=lambda item: item["modified_at"], reverse=True)
        return files

    def resolve_file(self, file_id: str) -> Path:
        if not self._is_safe_existing_filename(file_id):
            raise ValueError("Invalid file id")
        if file_id.startswith("id-debug--"):
            path = (self._id_debug_dir() / file_id.removeprefix("id-debug--")).resolve()
        elif file_id.startswith("vision-dispatch--"):
            path = (self._vision_dispatch_debug_dir() / file_id.removeprefix("vision-dispatch--")).resolve()
        else:
            path = (self.output_dir / file_id).resolve()
        root = self.output_dir.resolve()
        if root not in path.parents or not path.is_file():
            raise FileNotFoundError(file_id)
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError("Unsupported file type")
        return path

    def rename_file(self, file_id: str, new_name: str) -> Dict[str, Any]:
        source = self.resolve_file(file_id)
        target_stem = self._safe_target_stem(new_name)
        target = source.with_name(f"{target_stem}{source.suffix.lower()}")
        source_metadata = self._sidecar_metadata_path(source)
        target_metadata = self._sidecar_metadata_path(target)
        if target.exists():
            raise FileExistsError(target.name)
        if source_metadata.exists() and target_metadata.exists():
            raise FileExistsError(target_metadata.name)
        source.rename(target)
        if source_metadata.exists():
            source_metadata.rename(target_metadata)
            metadata = self._read_sidecar_metadata(target)
            if metadata:
                metadata["filename"] = target.name
                target_metadata.write_text(
                    json.dumps(metadata, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
        return self._file_payload(target)

    def delete_file(self, file_id: str) -> Dict[str, Any]:
        path = self.resolve_file(file_id)
        metadata_path = self._sidecar_metadata_path(path)
        path.unlink()
        if metadata_path.exists():
            metadata_path.unlink()
        return {"success": True, "deleted": file_id}

    def delete_all_files(self) -> Dict[str, Any]:
        deleted: list[str] = []
        for item in self.list_files():
            self.delete_file(item["id"])
            deleted.append(item["id"])
        return {"success": True, "deleted": deleted, "deleted_count": len(deleted)}

    def _is_safe_existing_filename(self, value: str) -> bool:
        if not value:
            return False
        if value.startswith("id-debug--"):
            return self._is_safe_existing_filename(value.removeprefix("id-debug--"))
        if value.startswith("vision-dispatch--"):
            return self._is_safe_existing_filename(value.removeprefix("vision-dispatch--"))
        if "/" in value or "\\" in value:
            return False
        if value in {".", ".."} or value.startswith("."):
            return False
        if Path(value).name != value:
            return False
        return Path(value).suffix.lower() in ALLOWED_EXTENSIONS

    def _safe_target_stem(self, value: str) -> str:
        name = value.strip()
        if not name or "/" in name or "\\" in name or Path(name).name != name:
            raise ValueError("Filename must be a safe basename")
        if name in {".", ".."} or name.startswith("."):
            raise ValueError("Filename must not be hidden or relative")
        stem = Path(name).stem if Path(name).suffix else name
        stem = stem.strip()
        if not SAFE_BASENAME_RE.fullmatch(stem):
            raise ValueError("Filename may contain letters, numbers, spaces, dots, dashes, and underscores")
        return stem
