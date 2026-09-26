#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import glob
import json
import logging
import os
import signal
import sys
import threading
import time
import warnings
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

from dotenv import load_dotenv
from livekit import rtc

# Ensure repository root is on sys.path when running as a script
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from livekit_shared.auth import generate_token
from robot_services.vision.camera_sources import (
    CameraSourceSettings,
    create_frame_source,
    normalize_camera_source_type,
)

try:
    import cv2  # type: ignore
    import numpy as np
except ImportError as exc:  # pragma: no cover - user env specific
    raise SystemExit(
        "camera_bridge.py requires OpenCV. "
        "Install it with `pip install opencv-python` inside your .venv."
    ) from exc


LOG = logging.getLogger("camera_bridge")
BYTE_STREAM_CHUNK_SIZE = 15_000
CAPTURE_STATS_INTERVAL_SECONDS = 5.0
DEFAULT_VIDEO_MAX_BITRATE = 1_500_000
DEFAULT_SUPERVISOR_URL = "http://127.0.0.1:8080"
DEFAULT_DEMAND_POLL_INTERVAL_SECONDS = 0.5
DEFAULT_IDLE_FPS = 2.0
DEMAND_POLL_TIMEOUT_SECONDS = 0.25
DEFAULT_CV_FACE_CROP_PATH = "robot_supervisor_v2/logs/latest-face.jpg"
DEFAULT_CV_YOLO_MODEL = "robot_services/vision/detection/yolo11n.pt"


@dataclass
class BridgeConfig:
    url: str
    room: str
    service_name: str
    identity: str
    name: str
    topic: str
    send_to_agent: bool
    source_type: str
    device: str
    ros_topic: Optional[str]
    ros_domain_id: Optional[str]
    ros_localhost_only: Optional[str]
    ros_fastdds_profile: Optional[str]
    width: Optional[int]
    height: Optional[int]
    framerate: Optional[float]
    fourcc: Optional[str]
    publish_fps: Optional[float]
    video_max_bitrate: int
    video_max_framerate: Optional[float]
    supervisor_url: str
    demand_url: str
    demand_poll_interval_seconds: float
    idle_fps: float
    interval: float
    jpeg_quality: int
    video_track_name: str
    cv_overlay_enabled: bool
    cv_detection_interval: int
    cv_face_crop_path: Optional[str]
    cv_detector_backend: str
    cv_yolo_model: str
    cv_yolo_confidence: float
    cv_yolo_image_size: int
    log_level: str


@dataclass
class CameraFrame:
    frame: object
    frame_id: int
    captured_at: float


@dataclass
class IntervalTelemetry:
    publish_count: int = 0
    convert_total_s: float = 0.0
    snapshot_queued: int = 0
    snapshot_sent: int = 0
    jpeg_total_s: float = 0.0
    stream_total_s: float = 0.0
    read_timeouts: int = 0
    late_frames: int = 0

    def reset(self) -> None:
        self.publish_count = 0
        self.convert_total_s = 0.0
        self.snapshot_queued = 0
        self.snapshot_sent = 0
        self.jpeg_total_s = 0.0
        self.stream_total_s = 0.0
        self.read_timeouts = 0
        self.late_frames = 0


class PublishFrameBuilder:
    def __init__(self, *, width: int, height: int) -> None:
        self._width = width
        self._height = height
        self._rgb_buffer: Optional[np.ndarray] = None
        self._rgb_view: Optional[memoryview] = None

    def build(self, frame: object) -> rtc.VideoFrame:
        if not isinstance(frame, np.ndarray):
            raise RuntimeError(f"Expected OpenCV frame ndarray, got {type(frame).__name__}")
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise RuntimeError(f"Expected BGR frame shape HxWx3, got {frame.shape}")
        if frame.dtype != np.uint8:
            raise RuntimeError(f"Expected uint8 BGR frame, got {frame.dtype}")

        height, width = frame.shape[:2]
        if width != self._width or height != self._height:
            LOG.warning(
                "Camera frame dimensions changed from %sx%s to %sx%s; resizing publish buffer",
                self._width,
                self._height,
                width,
                height,
            )
            self._width = width
            self._height = height
            self._rgb_buffer = None
            self._rgb_view = None

        if self._rgb_buffer is None:
            self._rgb_buffer = np.empty((self._height, self._width, 3), dtype=np.uint8)
            self._rgb_view = memoryview(self._rgb_buffer.reshape(-1))

        cv2.cvtColor(frame, cv2.COLOR_BGR2RGB, dst=self._rgb_buffer)
        if self._rgb_view is None:
            raise RuntimeError("RGB publish buffer was not initialized")

        return rtc.VideoFrame(
            self._width,
            self._height,
            rtc.VideoBufferType.RGB24,
            self._rgb_view,
        )


class CameraCvOverlay:
    def __init__(
        self,
        *,
        enabled: bool,
        detection_interval: int,
        face_crop_path: Optional[str],
        detector_backend: str,
        yolo_model: str,
        yolo_confidence: float,
        yolo_image_size: int,
    ) -> None:
        self.enabled = enabled
        self.detection_interval = max(int(detection_interval or 1), 1)
        self.face_crop_path = face_crop_path
        self.detector_backend = str(detector_backend or "auto").strip().lower()
        self.yolo_model = yolo_model
        self.yolo_confidence = float(yolo_confidence)
        self.yolo_image_size = int(yolo_image_size)
        self._frame_counter = 0
        self._last_faces: list[tuple[int, int, int, int]] = []
        self._last_persons: list[tuple[int, int, int, int, float]] = []
        self._last_crop_saved_at = 0.0
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._pending_detection: Optional[concurrent.futures.Future] = None
        self._yolo = None
        self._yolo_device = "cpu"
        self._yolo_load_attempted = False
        self._face_cascades: list[cv2.CascadeClassifier] = []
        self._profile_cascade = None
        if enabled:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="camera-cv-overlay",
            )
            for cascade_name in (
                "haarcascade_frontalface_default.xml",
                "haarcascade_frontalface_alt2.xml",
            ):
                cascade_path = os.path.join(cv2.data.haarcascades, cascade_name)
                cascade = cv2.CascadeClassifier(cascade_path)
                if cascade.empty():
                    LOG.warning("CV overlay could not load %s", cascade_path)
                    continue
                self._face_cascades.append(cascade)
            profile_path = os.path.join(cv2.data.haarcascades, "haarcascade_profileface.xml")
            self._profile_cascade = cv2.CascadeClassifier(profile_path)
            if self._profile_cascade.empty():
                LOG.warning("CV overlay could not load %s", profile_path)
                self._profile_cascade = None
            if not self._face_cascades and self._profile_cascade is None:
                LOG.warning("CV overlay disabled: no face cascades loaded")
                self.enabled = False
            if self.enabled and self.detector_backend in {"auto", "yolo"}:
                LOG.info(
                    "YOLO CV overlay will load asynchronously: model=%s imgsz=%s conf=%.2f",
                    self.yolo_model,
                    self.yolo_image_size,
                    self.yolo_confidence,
                )

    def _load_yolo(self) -> None:
        if self._yolo_load_attempted:
            return
        self._yolo_load_attempted = True
        try:
            from ultralytics import YOLO  # type: ignore
            import torch  # type: ignore
        except Exception as exc:
            LOG.warning("YOLO CV overlay unavailable: %s", exc)
            return
        model_path = str(Path(self.yolo_model))
        if not os.path.isabs(model_path):
            model_path = str(REPO_ROOT / model_path)
        try:
            self._yolo = YOLO(model_path)
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="CUDA initialization: The NVIDIA driver on your system is too old.*",
                )
                cuda_available = torch.cuda.is_available()
            self._yolo_device = "cuda" if cuda_available else "cpu"
            self._yolo.to(self._yolo_device)
            LOG.info(
                "YOLO CV overlay loaded: model=%s device=%s imgsz=%s conf=%.2f",
                model_path,
                self._yolo_device,
                self.yolo_image_size,
                self.yolo_confidence,
            )
        except Exception as exc:
            LOG.warning("Failed to load YOLO CV overlay model %s: %s", model_path, exc)
            self._yolo = None

    def process(self, frame: object) -> object:
        if not self.enabled or not isinstance(frame, np.ndarray):
            return frame

        output = frame.copy()
        self._frame_counter += 1
        if self._pending_detection is not None and self._pending_detection.done():
            try:
                detections = self._pending_detection.result()
                self._last_persons = detections.get("persons", [])
                self._last_faces = detections.get("faces", [])
                if self._last_faces:
                    self._save_largest_face(output, self._last_faces)
            except Exception as exc:
                LOG.debug("CV overlay detection failed: %s", exc)
            finally:
                self._pending_detection = None

        if (
            self._executor is not None
            and self._pending_detection is None
            and self._frame_counter % self.detection_interval == 0
        ):
            self._pending_detection = self._executor.submit(self._detect, output.copy())

        for person in self._last_persons:
            px1, py1, px2, py2, confidence = person
            cv2.rectangle(output, (px1, py1), (px2, py2), (56, 176, 255), 3)
            cv2.putText(
                output,
                f"person {confidence:.2f}",
                (px1, max(24, py1 - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (56, 176, 255),
                2,
                cv2.LINE_AA,
            )
        for face in self._last_faces:
            x, y, w, h = face
            if not self._last_persons:
                person_box = self._estimate_person_box(output.shape, face)
                px1, py1, px2, py2 = person_box
                cv2.rectangle(output, (px1, py1), (px2, py2), (56, 176, 255), 3)
                cv2.putText(
                    output,
                    "person",
                    (px1, max(24, py1 - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (56, 176, 255),
                    2,
                    cv2.LINE_AA,
                )
            cv2.rectangle(output, (x, y), (x + w, y + h), (60, 230, 90), 3)
            cv2.putText(
                output,
                "face",
                (x, max(24, y - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (60, 230, 90),
                2,
                cv2.LINE_AA,
            )
        return output

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _detect(self, frame: np.ndarray) -> dict[str, list]:
        persons = self._detect_persons_yolo(frame) if self.detector_backend in {"auto", "yolo"} else []
        faces = self._detect_faces(frame, persons=persons)
        return {
            "persons": persons,
            "faces": faces,
        }

    def _detect_persons_yolo(self, frame: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        if self._yolo is None:
            self._load_yolo()
            if self._yolo is None:
                if self.detector_backend == "yolo":
                    LOG.warning("CV overlay requested YOLO but it is unavailable; using face-only fallback")
                return []
        try:
            predict_kwargs = {
                "source": frame,
                "classes": [0],
                "conf": self.yolo_confidence,
                "imgsz": self.yolo_image_size,
                "verbose": False,
                "device": self._yolo_device,
            }
            if self._yolo_device == "cuda":
                predict_kwargs["half"] = True
            results = self._yolo.predict(**predict_kwargs)
        except Exception as exc:
            LOG.debug("YOLO person detection failed: %s", exc)
            return []
        if not results or results[0].boxes is None:
            return []
        frame_h, frame_w = frame.shape[:2]
        persons: list[tuple[int, int, int, int, float]] = []
        for box in results[0].boxes:
            conf = float(box.conf[0]) if box.conf is not None else 0.0
            x1, y1, x2, y2 = [int(value) for value in box.xyxy[0].tolist()]
            x1 = max(0, min(frame_w - 1, x1))
            y1 = max(0, min(frame_h - 1, y1))
            x2 = max(0, min(frame_w - 1, x2))
            y2 = max(0, min(frame_h - 1, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            persons.append((x1, y1, x2, y2, conf))
        return persons[:4]

    def _detect_faces(
        self,
        frame: np.ndarray,
        *,
        persons: list[tuple[int, int, int, int, float]],
    ) -> list[tuple[int, int, int, int]]:
        if not self._face_cascades and self._profile_cascade is None:
            return []
        if persons:
            detected: list[tuple[int, int, int, int]] = []
            for x1, y1, x2, y2, _confidence in persons:
                roi_h = max(1, int((y2 - y1) * 0.45))
                roi = frame[y1 : min(frame.shape[0], y1 + roi_h), x1:x2]
                if roi.size == 0:
                    continue
                for fx, fy, fw, fh in self._detect_faces_in_region(roi):
                    detected.append((x1 + fx, y1 + fy, fw, fh))
            return self._merge_faces(detected)
        return self._detect_faces_in_region(frame)

    def _detect_faces_in_region(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        height, width = frame.shape[:2]
        target_width = 800
        scale = min(1.0, target_width / max(float(width), 1.0))
        if scale < 1.0:
            resized = cv2.resize(frame, (int(width * scale), int(height * scale)))
        else:
            resized = frame
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        detected: list[tuple[int, int, int, int]] = []
        for cascade in self._face_cascades:
            detected.extend(
                self._detect_with_cascade(
                    cascade,
                    gray,
                    min_neighbors=4,
                    min_size=(32, 32),
                )
            )
        if self._profile_cascade is not None:
            detected.extend(
                self._detect_with_cascade(
                    self._profile_cascade,
                    gray,
                    min_neighbors=4,
                    min_size=(32, 32),
                )
            )
            flipped = cv2.flip(gray, 1)
            flipped_faces = self._detect_with_cascade(
                self._profile_cascade,
                flipped,
                min_neighbors=4,
                min_size=(32, 32),
            )
            flipped_width = gray.shape[1]
            detected.extend(
                (flipped_width - x - w, y, w, h)
                for x, y, w, h in flipped_faces
            )

        if scale < 1.0:
            inv = 1.0 / scale
            detected = [
                (int(x * inv), int(y * inv), int(w * inv), int(h * inv))
                for x, y, w, h in detected
            ]
        return self._merge_faces(detected)

    def _detect_with_cascade(
        self,
        cascade: cv2.CascadeClassifier,
        gray: np.ndarray,
        *,
        min_neighbors: int,
        min_size: tuple[int, int],
    ) -> list[tuple[int, int, int, int]]:
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.08,
            minNeighbors=min_neighbors,
            minSize=min_size,
            flags=cv2.CASCADE_SCALE_IMAGE,
        )
        return [(int(x), int(y), int(w), int(h)) for x, y, w, h in faces]

    def _merge_faces(
        self,
        faces: list[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]]:
        merged: list[tuple[int, int, int, int]] = []
        for face in sorted(faces, key=lambda item: item[2] * item[3], reverse=True):
            if any(self._iou(face, existing) > 0.35 for existing in merged):
                continue
            merged.append(face)
        return merged[:4]

    def _iou(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
    ) -> float:
        ax1, ay1, aw, ah = a
        bx1, by1, bw, bh = b
        ax2, ay2 = ax1 + aw, ay1 + ah
        bx2, by2 = bx1 + bw, by1 + bh
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        intersection = iw * ih
        union = aw * ah + bw * bh - intersection
        return intersection / union if union > 0 else 0.0

    def _estimate_person_box(
        self,
        frame_shape: tuple[int, ...],
        face: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        frame_h, frame_w = frame_shape[:2]
        x, y, w, h = face
        cx = x + w / 2
        person_w = max(w * 3.0, 120.0)
        person_h = max(h * 5.2, 220.0)
        px1 = max(0, int(cx - person_w / 2))
        py1 = max(0, int(y - h * 0.45))
        px2 = min(frame_w - 1, int(cx + person_w / 2))
        py2 = min(frame_h - 1, int(py1 + person_h))
        return px1, py1, px2, py2

    def _save_largest_face(
        self,
        frame: np.ndarray,
        faces: list[tuple[int, int, int, int]],
    ) -> None:
        if not self.face_crop_path:
            return
        now = time.monotonic()
        if now - self._last_crop_saved_at < 1.0:
            return
        x, y, w, h = max(faces, key=lambda face: face[2] * face[3])
        pad = int(max(w, h) * 0.25)
        frame_h, frame_w = frame.shape[:2]
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(frame_w, x + w + pad)
        y2 = min(frame_h, y + h + pad)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return
        try:
            path = Path(self.face_crop_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(path), crop)
            self._last_crop_saved_at = now
        except Exception as exc:
            LOG.debug("Failed to save face crop to %s: %s", self.face_crop_path, exc)

class CameraCapture:
    def __init__(
        self,
        *,
        source_type: str,
        device: str,
        ros_topic: Optional[str],
        ros_domain_id: Optional[str],
        ros_localhost_only: Optional[str],
        ros_fastdds_profile: Optional[str],
        width: Optional[int],
        height: Optional[int],
        framerate: Optional[float],
        fourcc: Optional[str],
        jpeg_quality: int,
    ) -> None:
        self._source_type = normalize_camera_source_type(source_type)
        self._device = device
        self._ros_topic = ros_topic
        self._ros_domain_id = ros_domain_id
        self._ros_localhost_only = ros_localhost_only
        self._ros_fastdds_profile = ros_fastdds_profile
        self._width = width
        self._height = height
        self._framerate = framerate
        self._fourcc = fourcc
        self._jpeg_quality = jpeg_quality
        self._capture = None
        self.actual_width: Optional[int] = None
        self.actual_height: Optional[int] = None
        self.actual_fps: Optional[float] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_capture_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[CameraFrame] = None
        self._last_read_frame_id = 0
        self._next_frame_id = 0
        self._frames_captured = 0
        self._capture_failures = 0
        self._dropped_frames = 0
        self._capture_read_count = 0
        self._capture_read_total_s = 0.0
        self._last_failure_log_at = 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._frame_event: Optional[asyncio.Event] = None
        self._target_capture_interval_s = 0.0
        self._capture_interval_lock = threading.Lock()

    def _open_capture(self):
        source = create_frame_source(
            CameraSourceSettings(
                source_type=self._source_type,
                device=self._device,
                ros_topic=self._ros_topic,
                width=self._width,
                height=self._height,
                framerate=self._framerate,
                fourcc=self._fourcc,
                buffer_size=None if self._source_type == "opencv" else 1,
                ros_domain_id=self._ros_domain_id,
                ros_localhost_only=self._ros_localhost_only,
                ros_fastdds_profile=self._ros_fastdds_profile,
                read_timeout_s=6.0 if self._source_type == "ros" else 2.0,
            )
        )
        source.open()
        return source

    async def open(self) -> None:
        if self._source_type == "opencv":
            # Avoid probing V4L2 devices before the capture thread starts. Some
            # Orbbec nodes open successfully but then stall after a quick
            # open/release/reopen cycle.
            self.actual_width = self._width
            self.actual_height = self._height
            self.actual_fps = self._framerate
            return

        loop = asyncio.get_running_loop()
        cap = await loop.run_in_executor(None, self._open_capture)

        self.actual_width = cap.actual_width
        self.actual_height = cap.actual_height
        self.actual_fps = cap.actual_fps
        self._capture = cap
        if self._width and self._height:
            self.actual_width = self._width
            self.actual_height = self._height

        LOG.info(
            "Camera ready: source=%s target=%s (%sx%s @ %s fps)",
            self._source_type,
            self._ros_topic if self._source_type == "ros" else self._device,
            self.actual_width or "auto",
            self.actual_height or "auto",
            f"{self.actual_fps:.2f}" if self.actual_fps else "auto",
        )

    def start_capture(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self._capture and self._source_type != "opencv":
            raise RuntimeError("Camera not opened")
        if self._capture_thread and self._capture_thread.is_alive():
            return

        self._loop = loop
        self._frame_event = asyncio.Event()
        self._stop_capture_event.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="camera-capture",
            daemon=True,
        )
        self._capture_thread.start()

    def set_target_capture_fps(self, fps: Optional[float]) -> None:
        interval = 0.0
        if fps is not None and fps > 0:
            interval = 1.0 / fps
        with self._capture_interval_lock:
            self._target_capture_interval_s = interval

    def _target_capture_interval(self) -> float:
        with self._capture_interval_lock:
            return self._target_capture_interval_s

    def stop_capture(self) -> None:
        self._stop_capture_event.set()
        if self._loop and self._frame_event and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._frame_event.set)

        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2.0)
            if self._capture_thread.is_alive():
                LOG.warning("Camera capture thread did not stop within timeout")
        self._capture_thread = None
        with self._frame_lock:
            self._latest_frame = None

    def _notify_frame_ready(self) -> None:
        if self._loop and self._frame_event and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._frame_event.set)

    def _capture_loop(self) -> None:
        while not self._stop_capture_event.is_set():
            cap = self._capture
            if not cap:
                try:
                    cap = self._open_capture()
                    self._capture = cap
                    self.actual_width = cap.actual_width
                    self.actual_height = cap.actual_height
                    self.actual_fps = cap.actual_fps
                except Exception as exc:
                    if time.monotonic() - self._last_failure_log_at >= 5.0:
                        LOG.warning("Failed to open camera in capture thread: %s", exc)
                        self._last_failure_log_at = time.monotonic()
                    time.sleep(0.5)
                    continue

            read_started_at = time.monotonic()
            ok, frame = cap.read()
            captured_at = time.monotonic()
            read_elapsed_s = captured_at - read_started_at

            if not ok or frame is None:
                with self._frame_lock:
                    self._capture_failures += 1
                    self._capture_read_count += 1
                    self._capture_read_total_s += read_elapsed_s
                if captured_at - self._last_failure_log_at >= 5.0:
                    LOG.warning("Failed to grab frame from camera")
                    self._last_failure_log_at = captured_at
                time.sleep(0.05)
                continue

            if (
                self._width
                and self._height
                and hasattr(frame, "shape")
                and (frame.shape[1] != self._width or frame.shape[0] != self._height)
            ):
                frame = cv2.resize(frame, (self._width, self._height), interpolation=cv2.INTER_AREA)

            with self._frame_lock:
                self._next_frame_id += 1
                frame_id = self._next_frame_id
                if (
                    self._latest_frame is not None
                    and self._latest_frame.frame_id > self._last_read_frame_id
                ):
                    self._dropped_frames += 1
                self._latest_frame = CameraFrame(
                    frame=frame,
                    frame_id=frame_id,
                    captured_at=captured_at,
                )
                self._frames_captured += 1
                self._capture_read_count += 1
                self._capture_read_total_s += read_elapsed_s

            self._notify_frame_ready()
            target_interval_s = self._target_capture_interval()
            if target_interval_s > 0:
                sleep_for = max(0.0, target_interval_s - read_elapsed_s)
                if sleep_for > 0:
                    self._stop_capture_event.wait(sleep_for)

    async def read_latest(self, timeout_s: float) -> Optional[CameraFrame]:
        if not self._frame_event:
            raise RuntimeError("Camera capture thread is not running")

        while True:
            if self._stop_capture_event.is_set():
                return None

            with self._frame_lock:
                latest = self._latest_frame
                if latest is not None and latest.frame_id != self._last_read_frame_id:
                    self._last_read_frame_id = latest.frame_id
                    self._frame_event.clear()
                    return latest

            try:
                await asyncio.wait_for(self._frame_event.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                return None

    def capture_stats(self) -> dict[str, float]:
        with self._frame_lock:
            return {
                "frames_captured": self._frames_captured,
                "capture_failures": self._capture_failures,
                "dropped_frames": self._dropped_frames,
                "latest_frame_id": self._latest_frame.frame_id if self._latest_frame else 0,
                "latest_captured_at": (
                    self._latest_frame.captured_at if self._latest_frame else 0.0
                ),
                "capture_read_count": self._capture_read_count,
                "capture_read_total_s": self._capture_read_total_s,
            }

    async def capture_jpeg(self) -> bytes:
        frame = await self.capture_frame()
        return await self.encode_jpeg(frame)

    async def capture_frame(self):
        camera_frame = await self.read_latest(timeout_s=2.0)
        if camera_frame is None:
            raise RuntimeError("Timed out waiting for camera frame")
        return camera_frame.frame

    async def encode_jpeg(self, frame) -> bytes:
        return await asyncio.to_thread(self._encode_jpeg_sync, frame)

    def _encode_jpeg_sync(self, frame) -> bytes:
        success, buffer = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality],
        )
        if not success:
            raise RuntimeError("Failed to encode frame as JPEG")
        return buffer.tobytes()

    def close(self) -> None:
        self.stop_capture()
        if self._capture:
            self._capture.release()
            self._capture = None


@dataclass
class SnapshotCandidate:
    frame: object
    frame_idx: int
    captured_at: float


class LatestSnapshotBuffer:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._candidate: Optional[SnapshotCandidate] = None
        self._closed = False
        self.replaced_count = 0

    async def put(self, candidate: SnapshotCandidate) -> None:
        async with self._condition:
            if self._closed:
                return
            if self._candidate is not None:
                self.replaced_count += 1
            self._candidate = candidate
            self._condition.notify()

    async def get(self) -> Optional[SnapshotCandidate]:
        async with self._condition:
            while self._candidate is None and not self._closed:
                await self._condition.wait()
            if self._candidate is None:
                return None
            candidate = self._candidate
            self._candidate = None
            return candidate

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()


class CameraDemandState:
    def __init__(
        self,
        *,
        demand_url: str,
        poll_interval_seconds: float,
        default_active: bool = True,
    ) -> None:
        self.demand_url = demand_url
        self.poll_interval_seconds = max(float(poll_interval_seconds or 0.0), 0.0)
        self.active = bool(default_active)
        self._next_poll_at = 0.0
        self._last_error_log_at = 0.0

    async def refresh_if_due(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        if now < self._next_poll_at:
            return self.active

        self._next_poll_at = now + self.poll_interval_seconds
        try:
            payload = await asyncio.to_thread(_fetch_json, self.demand_url)
            if isinstance(payload, dict) and "active" in payload:
                self.active = bool(payload["active"])
        except Exception as exc:
            if now - self._last_error_log_at >= 5.0:
                LOG.warning(
                    "Failed to poll camera demand from %s: %s; keeping active=%s",
                    self.demand_url,
                    exc,
                    self.active,
                )
                self._last_error_log_at = now

        return self.active


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=DEMAND_POLL_TIMEOUT_SECONDS) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8"))


def _device_to_index(device: str) -> Optional[int]:
    if device.isdigit():
        return int(device)
    if device.startswith("/dev/video"):
        suffix = device.replace("/dev/video", "")
        if suffix.isdigit():
            return int(suffix)
    return None


def _opencv_capture_target(device: str) -> str | int:
    index = _device_to_index(str(device))
    return index if index is not None else device


def _capture_backend() -> int:
    if sys.platform.startswith("linux"):
        return cv2.CAP_V4L2
    if sys.platform == "darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
        return cv2.CAP_AVFOUNDATION
    return 0


def _probe_device_resolution(device: str) -> tuple[Optional[int], Optional[int]]:
    backend = _capture_backend()
    capture_target = _opencv_capture_target(device)
    cap = cv2.VideoCapture(capture_target, backend)
    try:
        if not cap.isOpened():
            return None, None

        ok, frame = cap.read()
        if not ok or frame is None:
            return None, None

        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            return None, None
        return int(width), int(height)
    finally:
        cap.release()


def _list_devices() -> None:
    if sys.platform.startswith("linux"):
        devices = sorted(glob.glob("/dev/video*"))
        payload = []
        for idx, dev in enumerate(devices):
            width, height = _probe_device_resolution(dev)
            payload.append(
                {
                    "path": dev,
                    "name": dev,
                    "is_default": idx == 0,
                    "width": width,
                    "height": height,
                    "resolution": f"{width}x{height}" if width and height else None,
                    "has_valid_resolution": bool(width and height),
                }
            )
    elif sys.platform == "darwin":
        payload = []
        for idx in range(10):
            device = str(idx)
            width, height = _probe_device_resolution(device)
            if not width or not height:
                continue
            payload.append(
                {
                    "path": device,
                    "name": f"Camera {idx}",
                    "is_default": idx == 0,
                    "width": width,
                    "height": height,
                    "resolution": f"{width}x{height}",
                    "has_valid_resolution": True,
                }
            )
    else:
        payload = []

    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _parse_resolution(value: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    if not value:
        return None, None
    if "x" not in value.lower():
        raise ValueError("Resolution must be formatted as WIDTHxHEIGHT (e.g. 1280x720)")
    width_str, height_str = value.lower().split("x", 1)
    return int(width_str), int(height_str)


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _parse_optional_float(value: object, *, name: str, positive: bool = False) -> Optional[float]:
    if value in (None, ""):
        return None
    parsed = float(value)
    if positive and parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


def _parse_optional_int(value: object, *, name: str, positive: bool = False) -> Optional[int]:
    if value in (None, ""):
        return None
    parsed = int(value)
    if positive and parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


def parse_args() -> BridgeConfig:
    parser = argparse.ArgumentParser(
        description="Publish a LiveKit camera track and optional agent JPEG snapshots."
    )
    parser.add_argument("--list-devices", action="store_true", help="List camera devices and exit")
    parser.add_argument("--url", default=os.getenv("LIVEKIT_URL"), help="LiveKit server URL")
    parser.add_argument(
        "--room",
        default=os.getenv("LIVEKIT_ROOM"),
        help="Room name to join (LIVEKIT_ROOM)",
    )
    parser.add_argument(
        "--identity",
        default=os.getenv("LIVEKIT_CAMERA_IDENTITY", "camera-bridge"),
        help="LiveKit identity to use",
    )
    parser.add_argument(
        "--service-name",
        default=os.getenv("CAMERA_BRIDGE_SERVICE_NAME"),
        help="Supervisor service name for demand polling (defaults to identity)",
    )
    parser.add_argument(
        "--name",
        default=os.getenv("LIVEKIT_CAMERA_NAME"),
        help="Display name (defaults to identity)",
    )
    parser.add_argument(
        "--topic",
        default=os.getenv("LIVEKIT_CAMERA_TOPIC", "images"),
        help="Byte stream topic to publish frames to",
    )
    parser.set_defaults(
        send_to_agent=_parse_bool(os.getenv("LIVEKIT_CAMERA_SEND_TO_AGENT"), False)
    )
    parser.add_argument(
        "--send-to-agent",
        dest="send_to_agent",
        action="store_true",
        help="Enable periodic JPEG snapshots to the agent byte stream",
    )
    parser.add_argument(
        "--no-send-to-agent",
        dest="send_to_agent",
        action="store_false",
        help="Disable periodic JPEG snapshots to the agent byte stream",
    )
    parser.add_argument(
        "--source-type",
        choices=["opencv", "ros"],
        default=os.getenv("CAMERA_BRIDGE_SOURCE_TYPE", "opencv"),
        help="Camera frame source type",
    )
    parser.add_argument(
        "--device",
        default=os.getenv("LIVEKIT_CAMERA_DEVICE", "/dev/video0"),
        help="Camera device path or index",
    )
    parser.add_argument(
        "--ros-topic",
        default=os.getenv("LIVEKIT_CAMERA_ROS_TOPIC"),
        help="ROS sensor_msgs/Image topic to subscribe to when --source-type=ros",
    )
    parser.add_argument(
        "--ros-domain-id",
        default="0",
        help="ROS_DOMAIN_ID for ROS camera source",
    )
    parser.add_argument(
        "--ros-localhost-only",
        default=os.getenv("ROS_LOCALHOST_ONLY", "0"),
        help="ROS_LOCALHOST_ONLY for ROS camera source",
    )
    parser.add_argument(
        "--ros-fastdds-profile",
        default="/agibot/software/entry/cfg/super_client.xml",
        help="FASTRTPS_DEFAULT_PROFILES_FILE for ROS camera source",
    )
    parser.add_argument(
        "--resolution",
        default=os.getenv("LIVEKIT_CAMERA_RESOLUTION", "960x540"),
        help="Requested resolution as WIDTHxHEIGHT (e.g. 1280x720)",
    )
    parser.add_argument(
        "--framerate",
        type=float,
        default=os.getenv("LIVEKIT_CAMERA_FRAMERATE", "15"),
        help="Requested framerate (frames per second)",
    )
    parser.add_argument(
        "--fourcc",
        default=os.getenv("LIVEKIT_CAMERA_FOURCC"),
        help="Optional four-character camera pixel format, for example MJPG",
    )
    parser.add_argument(
        "--publish-fps",
        type=float,
        default=os.getenv("LIVEKIT_CAMERA_PUBLISH_FPS"),
        help=(
            "LiveKit video publish framerate. Defaults to requested camera "
            "framerate, then actual camera FPS, then 30 FPS."
        ),
    )
    parser.add_argument(
        "--video-max-bitrate",
        type=int,
        default=os.getenv("LIVEKIT_CAMERA_VIDEO_MAX_BITRATE", str(DEFAULT_VIDEO_MAX_BITRATE)),
        help="LiveKit video encoding max bitrate in bits per second",
    )
    parser.add_argument(
        "--video-max-framerate",
        type=float,
        default=os.getenv("LIVEKIT_CAMERA_VIDEO_MAX_FRAMERATE"),
        help="LiveKit video encoding max framerate. Defaults to resolved publish FPS.",
    )
    parser.add_argument(
        "--supervisor-url",
        default=os.getenv("CAMERA_BRIDGE_SUPERVISOR_URL", DEFAULT_SUPERVISOR_URL),
        help="Robot Supervisor API base URL for camera demand polling",
    )
    parser.add_argument(
        "--demand-url",
        default=os.getenv("CAMERA_BRIDGE_DEMAND_URL"),
        help=(
            "Camera demand status URL. Defaults to "
            "${CAMERA_BRIDGE_SUPERVISOR_URL}/api/camera-bridge/{service_name}/demand"
        ),
    )
    parser.add_argument(
        "--demand-poll-interval-seconds",
        type=float,
        default=os.getenv(
            "CAMERA_BRIDGE_DEMAND_POLL_INTERVAL_SECONDS",
            str(DEFAULT_DEMAND_POLL_INTERVAL_SECONDS),
        ),
        help="Seconds between camera demand polls",
    )
    parser.add_argument(
        "--idle-fps",
        type=float,
        default=os.getenv("CAMERA_BRIDGE_IDLE_FPS", str(DEFAULT_IDLE_FPS)),
        help="Camera read/discard FPS while no monitor is active",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=os.getenv("LIVEKIT_CAMERA_INTERVAL", "3.0"),
        help="Seconds between JPEG snapshots sent to the agent byte stream",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=int(os.getenv("LIVEKIT_CAMERA_JPEG_QUALITY", "85")),
        help="JPEG quality (0-100)",
    )
    parser.add_argument(
        "--video-track-name",
        default=os.getenv("LIVEKIT_CAMERA_TRACK_NAME", "camera"),
        help="LiveKit video track name for the browser monitor",
    )
    parser.add_argument(
        "--cv-overlay-enabled",
        default=os.getenv("CAMERA_BRIDGE_CV_OVERLAY_ENABLED", "true"),
        help="Draw lightweight person/face detection boxes on the published camera stream",
    )
    parser.add_argument(
        "--cv-detection-interval",
        type=int,
        default=os.getenv("CAMERA_BRIDGE_CV_DETECTION_INTERVAL", "8"),
        help="Run CV detection every N published frames",
    )
    parser.add_argument(
        "--cv-face-crop-path",
        default=os.getenv("CAMERA_BRIDGE_CV_FACE_CROP_PATH", DEFAULT_CV_FACE_CROP_PATH),
        help="Path where the latest detected face crop JPEG is written",
    )
    parser.add_argument(
        "--cv-detector-backend",
        choices=["auto", "haar", "yolo"],
        default=os.getenv("CAMERA_BRIDGE_CV_DETECTOR_BACKEND", "auto"),
        help="CV detector backend: yolo for person boxes, haar for face-only fallback",
    )
    parser.add_argument(
        "--cv-yolo-model",
        default=os.getenv("CAMERA_BRIDGE_CV_YOLO_MODEL", DEFAULT_CV_YOLO_MODEL),
        help="YOLO model path for person detection",
    )
    parser.add_argument(
        "--cv-yolo-confidence",
        type=float,
        default=os.getenv("CAMERA_BRIDGE_CV_YOLO_CONFIDENCE", "0.35"),
        help="YOLO person confidence threshold",
    )
    parser.add_argument(
        "--cv-yolo-image-size",
        type=int,
        default=os.getenv("CAMERA_BRIDGE_CV_YOLO_IMAGE_SIZE", "416"),
        help="YOLO inference image size",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("CAMERA_BRIDGE_LOG", "INFO"),
        help="Logging level",
    )

    args = parser.parse_args()

    if args.list_devices:
        _list_devices()
        raise SystemExit(0)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.url:
        parser.error("LiveKit URL missing. Set LIVEKIT_URL or pass --url.")
    if not args.room:
        parser.error("LiveKit room missing. Set LIVEKIT_ROOM or pass --room.")

    width, height = _parse_resolution(args.resolution)

    try:
        framerate = _parse_optional_float(args.framerate, name="Camera framerate")
        publish_fps = _parse_optional_float(
            args.publish_fps,
            name="Publish FPS",
            positive=True,
        )
        video_max_bitrate = _parse_optional_int(
            args.video_max_bitrate,
            name="Video max bitrate",
            positive=True,
        )
        video_max_framerate = _parse_optional_float(
            args.video_max_framerate,
            name="Video max framerate",
            positive=True,
        )
        demand_poll_interval_seconds = _parse_optional_float(
            args.demand_poll_interval_seconds,
            name="Demand poll interval",
            positive=True,
        )
        idle_fps = _parse_optional_float(
            args.idle_fps,
            name="Idle FPS",
            positive=True,
        )
        cv_detection_interval = _parse_optional_int(
            args.cv_detection_interval,
            name="CV detection interval",
            positive=True,
        )
        cv_yolo_confidence = _parse_optional_float(
            args.cv_yolo_confidence,
            name="CV YOLO confidence",
            positive=True,
        )
        cv_yolo_image_size = _parse_optional_int(
            args.cv_yolo_image_size,
            name="CV YOLO image size",
            positive=True,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if video_max_bitrate is None:
        video_max_bitrate = DEFAULT_VIDEO_MAX_BITRATE
    if demand_poll_interval_seconds is None:
        demand_poll_interval_seconds = DEFAULT_DEMAND_POLL_INTERVAL_SECONDS
    if idle_fps is None:
        idle_fps = DEFAULT_IDLE_FPS

    interval = args.interval
    if isinstance(interval, str):
        interval = float(interval)
    if interval <= 0:
        parser.error("Snapshot interval must be greater than 0 seconds.")

    service_name = args.service_name or args.identity
    supervisor_url = str(args.supervisor_url or DEFAULT_SUPERVISOR_URL).rstrip("/")
    if args.demand_url:
        demand_url = str(args.demand_url).format(
            service_name=urllib.parse.quote(service_name, safe="")
        )
    else:
        quoted_service_name = urllib.parse.quote(service_name, safe="")
        demand_url = f"{supervisor_url}/api/camera-bridge/{quoted_service_name}/demand"

    return BridgeConfig(
        url=args.url,
        room=args.room,
        service_name=service_name,
        identity=args.identity,
        name=args.name or args.identity,
        topic=args.topic,
        send_to_agent=bool(args.send_to_agent),
        source_type=normalize_camera_source_type(args.source_type),
        device=args.device,
        ros_topic=args.ros_topic,
        ros_domain_id=args.ros_domain_id,
        ros_localhost_only=args.ros_localhost_only,
        ros_fastdds_profile=args.ros_fastdds_profile,
        width=width,
        height=height,
        framerate=framerate,
        fourcc=args.fourcc or None,
        publish_fps=publish_fps,
        video_max_bitrate=video_max_bitrate,
        video_max_framerate=video_max_framerate,
        supervisor_url=supervisor_url,
        demand_url=demand_url,
        demand_poll_interval_seconds=demand_poll_interval_seconds,
        idle_fps=idle_fps,
        interval=interval,
        jpeg_quality=args.jpeg_quality,
        video_track_name=args.video_track_name,
        cv_overlay_enabled=_parse_bool(str(args.cv_overlay_enabled), False),
        cv_detection_interval=cv_detection_interval or 8,
        cv_face_crop_path=args.cv_face_crop_path or None,
        cv_detector_backend=args.cv_detector_backend,
        cv_yolo_model=str(args.cv_yolo_model),
        cv_yolo_confidence=cv_yolo_confidence or 0.35,
        cv_yolo_image_size=cv_yolo_image_size or 416,
        log_level=str(args.log_level),
    )


async def _send_frame(
    room: rtc.Room,
    *,
    payload: bytes,
    cfg: BridgeConfig,
    camera: CameraCapture,
    frame_idx: int,
) -> None:
    timestamp_ms = int(time.time() * 1000)
    name = f"{cfg.identity}-{timestamp_ms}.jpg"
    attributes = {
        "source": cfg.identity,
        "width": str(camera.actual_width) if camera.actual_width else "",
        "height": str(camera.actual_height) if camera.actual_height else "",
        "fps": f"{camera.actual_fps:.2f}" if camera.actual_fps else "",
        "frame": str(frame_idx),
    }
    writer = await room.local_participant.stream_bytes(
        name=name,
        total_size=len(payload),
        mime_type="image/jpeg",
        topic=cfg.topic,
        attributes=attributes,
    )
    for offset in range(0, len(payload), BYTE_STREAM_CHUNK_SIZE):
        await writer.write(payload[offset : offset + BYTE_STREAM_CHUNK_SIZE])
    await writer.aclose()
    LOG.debug(
        "Published frame %s (%d bytes) on topic '%s'",
        name,
        len(payload),
        cfg.topic,
    )


async def _snapshot_worker(
    room: rtc.Room,
    *,
    cfg: BridgeConfig,
    camera: CameraCapture,
    snapshots: LatestSnapshotBuffer,
    telemetry: IntervalTelemetry,
) -> None:
    try:
        while True:
            candidate = await snapshots.get()
            if candidate is None:
                return

            encode_started_at = time.monotonic()
            payload = await camera.encode_jpeg(candidate.frame)
            encode_elapsed_s = time.monotonic() - encode_started_at

            send_started_at = time.monotonic()
            await _send_frame(
                room,
                payload=payload,
                cfg=cfg,
                camera=camera,
                frame_idx=candidate.frame_idx,
            )
            send_elapsed_s = time.monotonic() - send_started_at
            telemetry.snapshot_sent += 1
            telemetry.jpeg_total_s += encode_elapsed_s
            telemetry.stream_total_s += send_elapsed_s
    except asyncio.CancelledError:
        raise
    except Exception:
        LOG.exception("Snapshot worker failed")


async def run(cfg: BridgeConfig) -> None:
    room = rtc.Room()
    token = generate_token(cfg.room, identity=cfg.identity, name=cfg.name)
    await room.connect(cfg.url, token)
    LOG.info("Connected to %s as %s", cfg.room, cfg.identity)

    camera = CameraCapture(
        source_type=cfg.source_type,
        device=cfg.device,
        ros_topic=cfg.ros_topic,
        ros_domain_id=cfg.ros_domain_id,
        ros_localhost_only=cfg.ros_localhost_only,
        ros_fastdds_profile=cfg.ros_fastdds_profile,
        width=cfg.width,
        height=cfg.height,
        framerate=cfg.framerate,
        fourcc=cfg.fourcc,
        jpeg_quality=cfg.jpeg_quality,
    )
    await camera.open()

    if not camera.actual_width or not camera.actual_height:
        raise RuntimeError("Camera opened without reporting a valid frame size")

    target_publish_fps = cfg.publish_fps or cfg.framerate or camera.actual_fps or 30.0
    encoding_max_framerate = cfg.video_max_framerate or target_publish_fps

    video_source = rtc.VideoSource(camera.actual_width, camera.actual_height)
    publish_frame_builder = PublishFrameBuilder(
        width=camera.actual_width,
        height=camera.actual_height,
    )
    cv_overlay = CameraCvOverlay(
        enabled=cfg.cv_overlay_enabled,
        detection_interval=cfg.cv_detection_interval,
        face_crop_path=cfg.cv_face_crop_path,
        detector_backend=cfg.cv_detector_backend,
        yolo_model=cfg.cv_yolo_model,
        yolo_confidence=cfg.cv_yolo_confidence,
        yolo_image_size=cfg.cv_yolo_image_size,
    )
    video_track = rtc.LocalVideoTrack.create_video_track(cfg.video_track_name, video_source)
    publish_opts = rtc.TrackPublishOptions(
        source=rtc.TrackSource.SOURCE_CAMERA,
        video_encoding=rtc.VideoEncoding(
            max_framerate=encoding_max_framerate,
            max_bitrate=cfg.video_max_bitrate,
        ),
    )
    publication = await room.local_participant.publish_track(video_track, publish_opts)
    LOG.info("Published video track %s (%s)", cfg.video_track_name, publication.sid)
    camera.set_target_capture_fps(target_publish_fps)
    camera.start_capture(asyncio.get_running_loop())

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _shutdown(*_: object) -> None:
        if not stop_event.is_set():
            LOG.info("Shutdown signal received, stopping camera bridge...")
            stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(sig, lambda *_: _shutdown())

    video_interval = max(1.0 / target_publish_fps, 0.01)
    idle_interval = max(1.0 / cfg.idle_fps, 0.01)
    snapshot_interval = cfg.interval
    demand_state = CameraDemandState(
        demand_url=cfg.demand_url,
        poll_interval_seconds=cfg.demand_poll_interval_seconds,
        default_active=True,
    )
    monitor_active = demand_state.active
    camera.set_target_capture_fps(None if monitor_active else cfg.idle_fps)
    LOG.info(
        "Camera FPS: requested=%s actual=%s publish=%.2f encoding_max_fps=%.2f encoding_max_bitrate=%s idle=%.2f",
        f"{cfg.framerate:.2f}" if cfg.framerate else "auto",
        f"{camera.actual_fps:.2f}" if camera.actual_fps else "auto",
        target_publish_fps,
        encoding_max_framerate,
        cfg.video_max_bitrate,
        cfg.idle_fps,
    )
    LOG.info(
        "Camera demand polling: service=%s url=%s poll_s=%.2f initial=%s",
        cfg.service_name,
        cfg.demand_url,
        cfg.demand_poll_interval_seconds,
        "active" if monitor_active else "inactive",
    )
    if cfg.send_to_agent:
        LOG.info(
            "Streaming camera video at %.2f fps with agent snapshots every %.2f seconds",
            target_publish_fps,
            snapshot_interval,
        )
    else:
        LOG.info(
            "Streaming camera video at %.2f fps with agent snapshots disabled",
            target_publish_fps,
        )
    if cfg.cv_overlay_enabled:
        LOG.info(
            "Camera CV overlay enabled: detection_interval=%s face_crop_path=%s",
            cfg.cv_detection_interval,
            cfg.cv_face_crop_path or "disabled",
        )

    frame_idx = 0
    next_frame_at = time.monotonic()
    next_snapshot_at = next_frame_at
    next_telemetry_at = next_frame_at + CAPTURE_STATS_INTERVAL_SECONDS
    telemetry_interval_started_at = next_frame_at
    last_capture_warning_at = 0.0
    telemetry = IntervalTelemetry()
    last_capture_stats = camera.capture_stats()
    last_snapshot_replaced = 0
    snapshots: Optional[LatestSnapshotBuffer] = None
    snapshot_task: Optional[asyncio.Task] = None
    if cfg.send_to_agent:
        snapshots = LatestSnapshotBuffer()
        snapshot_task = asyncio.create_task(
            _snapshot_worker(
                room,
                cfg=cfg,
                camera=camera,
                snapshots=snapshots,
                telemetry=telemetry,
            ),
            name="camera-snapshot-worker",
        )

    def _avg_ms(total_s: float, count: int) -> float:
        return (total_s / count) * 1000 if count else 0.0

    def _delta(stats: dict[str, float], previous: dict[str, float], key: str) -> float:
        return max(0.0, float(stats[key]) - float(previous[key]))

    def _log_telemetry(now: float) -> None:
        nonlocal last_capture_stats
        nonlocal last_snapshot_replaced
        nonlocal next_telemetry_at
        nonlocal telemetry_interval_started_at

        if now < next_telemetry_at:
            return

        stats = camera.capture_stats()
        interval_elapsed_s = max(now - telemetry_interval_started_at, 0.001)
        frames_captured_delta = _delta(stats, last_capture_stats, "frames_captured")
        capture_failures_delta = int(_delta(stats, last_capture_stats, "capture_failures"))
        dropped_frames_delta = int(_delta(stats, last_capture_stats, "dropped_frames"))
        capture_read_count_delta = int(
            _delta(stats, last_capture_stats, "capture_read_count")
        )
        capture_read_total_delta_s = _delta(
            stats,
            last_capture_stats,
            "capture_read_total_s",
        )
        latest_captured_at = float(stats["latest_captured_at"])
        latest_age_ms = (now - latest_captured_at) * 1000 if latest_captured_at else 0.0
        snapshot_replaced_total = (
            snapshots.replaced_count if snapshots is not None else last_snapshot_replaced
        )
        snapshot_replaced_delta = max(0, snapshot_replaced_total - last_snapshot_replaced)

        LOG.info(
            "Camera telemetry: capture_fps=%.1f publish_fps=%.1f "
            "snapshot_queued=%s snapshot_sent=%s snapshot_replaced=%s "
            "capture_ms=%.1f convert_ms=%.1f jpeg_ms=%.1f stream_ms=%.1f "
            "dropped=%s late=%s read_timeouts=%s failures=%s latest_age=%.1fms",
            frames_captured_delta / interval_elapsed_s,
            telemetry.publish_count / interval_elapsed_s,
            telemetry.snapshot_queued,
            telemetry.snapshot_sent,
            snapshot_replaced_delta,
            _avg_ms(capture_read_total_delta_s, capture_read_count_delta),
            _avg_ms(telemetry.convert_total_s, telemetry.publish_count),
            _avg_ms(telemetry.jpeg_total_s, telemetry.snapshot_sent),
            _avg_ms(telemetry.stream_total_s, telemetry.snapshot_sent),
            dropped_frames_delta,
            telemetry.late_frames,
            telemetry.read_timeouts,
            capture_failures_delta,
            latest_age_ms,
        )

        last_capture_stats = stats
        last_snapshot_replaced = snapshot_replaced_total
        telemetry.reset()
        telemetry_interval_started_at = now
        next_telemetry_at = now + CAPTURE_STATS_INTERVAL_SECONDS

    try:
        while not stop_event.is_set():
            try:
                now = time.monotonic()
                active_now = await demand_state.refresh_if_due(now)
                if active_now != monitor_active:
                    monitor_active = active_now
                    camera.set_target_capture_fps(None if monitor_active else cfg.idle_fps)
                    LOG.info(
                        "Camera monitor demand %s",
                        "active" if monitor_active else "inactive",
                    )
                    next_frame_at = now

                current_interval = video_interval if monitor_active else idle_interval
                frame_read_timeout_s = max(current_interval * 2, 0.5)
                camera_frame = await camera.read_latest(timeout_s=frame_read_timeout_s)
                if camera_frame is None:
                    telemetry.read_timeouts += 1
                    now = time.monotonic()
                    if now - last_capture_warning_at >= CAPTURE_STATS_INTERVAL_SECONDS:
                        LOG.warning(
                            "Timed out waiting for fresh camera frame after %.2fs",
                            frame_read_timeout_s,
                        )
                        last_capture_warning_at = now
                    _log_telemetry(now)
                    next_frame_at = now
                    continue

                frame_started_at = camera_frame.captured_at
                frame = camera_frame.frame
                if monitor_active:
                    convert_started_at = time.monotonic()
                    video_frame = publish_frame_builder.build(cv_overlay.process(frame))
                    telemetry.convert_total_s += time.monotonic() - convert_started_at
                    # VideoSource.capture_frame is a synchronous method (returns None),
                    # so do not await it. The previous code raised a TypeError when
                    # awaiting a non-coroutine.
                    video_source.capture_frame(video_frame)
                    telemetry.publish_count += 1

                if cfg.send_to_agent and frame_started_at >= next_snapshot_at:
                    if snapshots is not None:
                        await snapshots.put(
                            SnapshotCandidate(
                                frame=frame.copy(),
                                frame_idx=frame_idx,
                                captured_at=frame_started_at,
                            )
                        )
                        telemetry.snapshot_queued += 1
                    frame_idx += 1
                    next_snapshot_at = frame_started_at + snapshot_interval

                if snapshot_task and snapshot_task.done():
                    if snapshot_task.cancelled():
                        LOG.warning("Agent snapshots disabled because snapshot worker was cancelled")
                    elif exc := snapshot_task.exception():
                        LOG.error("Snapshot worker exited with error: %s", exc)
                        LOG.warning("Agent snapshots disabled because snapshot worker stopped")
                    else:
                        LOG.warning("Agent snapshots disabled because snapshot worker stopped")
                    snapshot_task = None
                    snapshots = None

                now = time.monotonic()
                _log_telemetry(now)
            except Exception as exc:
                LOG.error("Failed to capture/send frame: %s", exc, exc_info=True)
                await asyncio.sleep(max(video_interval if monitor_active else idle_interval, 0.5))
                continue

            next_frame_at += video_interval if monitor_active else idle_interval
            sleep_for = next_frame_at - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                telemetry.late_frames += 1
                next_frame_at = time.monotonic()
    finally:
        if snapshots is not None:
            await snapshots.close()
        if snapshot_task is not None:
            try:
                await asyncio.wait_for(snapshot_task, timeout=2.0)
            except asyncio.TimeoutError:
                snapshot_task.cancel()
                try:
                    await snapshot_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Snapshot worker shutdown failed")
        await room.disconnect()
        cv_overlay.close()
        camera.close()
        LOG.info("Camera bridge shut down cleanly.")


def main() -> None:
    load_dotenv()
    cfg = parse_args()
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        LOG.info("Interrupted by user, exiting.")


if __name__ == "__main__":
    main()
