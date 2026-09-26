"""Shared camera frame sources for OpenCV devices and ROS image topics."""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from livekit_shared.video_devices import resolve_camera_device

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - runtime dependency
    cv2 = None  # type: ignore


DEFAULT_ROS_DOMAIN_ID = "0"
DEFAULT_ROS_LOCALHOST_ONLY = "0"
DEFAULT_FASTDDS_PROFILE = "/agibot/software/entry/cfg/super_client.xml"


@dataclass(frozen=True)
class CameraSourceSettings:
    source_type: str = "opencv"
    device: str = "/dev/video0"
    ros_topic: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    framerate: Optional[float] = None
    fourcc: Optional[str] = None
    buffer_size: Optional[int] = 1
    ros_domain_id: Optional[str] = DEFAULT_ROS_DOMAIN_ID
    ros_localhost_only: Optional[str] = DEFAULT_ROS_LOCALHOST_ONLY
    ros_fastdds_profile: Optional[str] = DEFAULT_FASTDDS_PROFILE
    read_timeout_s: float = 2.0


def normalize_camera_source_type(value: object) -> str:
    normalized = str(value or "opencv").strip().lower()
    if normalized not in {"opencv", "ros"}:
        raise ValueError(f"Unsupported camera source type: {value}")
    return normalized


def create_frame_source(settings: CameraSourceSettings):
    source_type = normalize_camera_source_type(settings.source_type)
    if source_type == "ros":
        return RosImageFrameSource(settings)
    return OpenCvFrameSource(settings)


class OpenCvFrameSource:
    def __init__(self, settings: CameraSourceSettings) -> None:
        self._settings = settings
        self._capture = None
        self.actual_width: Optional[int] = None
        self.actual_height: Optional[int] = None
        self.actual_fps: Optional[float] = None

    def open(self) -> None:
        if cv2 is None:  # pragma: no cover - runtime dependency
            raise RuntimeError("OpenCV is required for opencv camera source")

        resolved_device = resolve_camera_device(str(self._settings.device))
        index = _device_to_index(resolved_device)
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else 0
        capture = cv2.VideoCapture(index if index is not None else resolved_device, backend)
        if not capture.isOpened():
            raise RuntimeError(
                f"Unable to open camera device {self._settings.device} "
                f"(resolved to {resolved_device})"
            )

        if self._settings.fourcc:
            if len(self._settings.fourcc) != 4:
                raise RuntimeError(f"Camera FOURCC must have four characters: {self._settings.fourcc}")
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self._settings.fourcc))
        if self._settings.width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._settings.width)
        if self._settings.height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._settings.height)
        if self._settings.framerate:
            capture.set(cv2.CAP_PROP_FPS, self._settings.framerate)
        if self._settings.buffer_size is not None and hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, self._settings.buffer_size)

        self._capture = capture
        self.actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
        self.actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
        fps = capture.get(cv2.CAP_PROP_FPS)
        self.actual_fps = float(fps) if fps else None

    def read(self):
        if self._capture is None:
            raise RuntimeError("Camera source is not open")
        return self._capture.read()

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class RosImageFrameSource:
    def __init__(self, settings: CameraSourceSettings) -> None:
        self._settings = settings
        self._topic = str(settings.ros_topic or "").strip()
        self._compressed = self._topic.endswith("/compressed")
        self._node = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._condition = threading.Condition()
        self._latest_frame = None
        self._latest_frame_id = 0
        self._last_read_frame_id = 0
        self._last_error: Optional[str] = None
        self.actual_width: Optional[int] = None
        self.actual_height: Optional[int] = None
        self.actual_fps: Optional[float] = settings.framerate

    def open(self) -> None:
        if not self._topic:
            raise RuntimeError("ROS camera source requires a ros_topic")

        _apply_ros_environment(self._settings)
        (
            rclpy,
            node_cls,
            image_cls,
            compressed_image_cls,
            qos_cls,
            reliability,
            durability,
            history,
        ) = _import_ros_modules()
        if not rclpy.ok():
            rclpy.init(args=None)

        source = self
        message_cls = compressed_image_cls if self._topic.endswith("/compressed") else image_cls

        class CameraSourceNode(node_cls):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:
                super().__init__("humanoid_camera_source")
                qos = qos_cls(
                    reliability=reliability.RELIABLE,
                    durability=durability.TRANSIENT_LOCAL,
                    history=history.KEEP_LAST,
                    depth=1,
                )
                self.create_subscription(message_cls, source._topic, self._on_image, qos)

            def _on_image(self, msg) -> None:
                try:
                    frame = msg if source._compressed else ros_image_to_bgr(msg)
                except Exception as exc:  # pragma: no cover - runtime data specific
                    source._last_error = str(exc)
                    return
                with source._condition:
                    source._latest_frame_id += 1
                    source._latest_frame = frame
                    source.actual_height, source.actual_width = frame.shape[:2]
                    source._condition.notify_all()

        self._node = CameraSourceNode()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._spin_loop,
            args=(rclpy,),
            name="ros-camera-source",
            daemon=True,
        )
        self._thread.start()

        deadline = time.monotonic() + max(self._settings.read_timeout_s, 0.1)
        with self._condition:
            while self._latest_frame is None and time.monotonic() < deadline:
                self._condition.wait(timeout=0.1)
        if self._latest_frame is None:
            detail = f": {self._last_error}" if self._last_error else ""
            self.release()
            raise RuntimeError(f"Timed out waiting for ROS image topic {self._topic}{detail}")

    def _spin_loop(self, rclpy) -> None:
        while not self._stop_event.is_set():
            node = self._node
            if node is None:
                return
            try:
                rclpy.spin_once(node, timeout_sec=0.1)
            except Exception as exc:  # pragma: no cover - ROS runtime specific
                self._last_error = str(exc)
                time.sleep(0.1)

    def read(self):
        deadline = time.monotonic() + max(self._settings.read_timeout_s, 0.1)
        with self._condition:
            while self._latest_frame_id == self._last_read_frame_id and not self._stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, None
                self._condition.wait(timeout=min(remaining, 0.1))

            if self._latest_frame is None:
                return False, None
            self._last_read_frame_id = self._latest_frame_id
            frame = self._latest_frame
        if self._compressed:
            decoded = ros_compressed_image_to_bgr(frame)
            self.actual_height, self.actual_width = decoded.shape[:2]
            return True, decoded
        return True, frame.copy()

    def release(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            self._node = None


def ros_image_to_bgr(msg):
    if cv2 is None:  # pragma: no cover - runtime dependency
        raise RuntimeError("OpenCV is required for ROS image conversion")

    height = int(msg.height)
    width = int(msg.width)
    encoding = str(msg.encoding).lower()
    data = np.frombuffer(msg.data, dtype=np.uint8)

    if encoding in {"rgb8", "rgb"}:
        rgb = _reshape_image(data, height, width, 3, msg.step)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if encoding in {"bgr8", "bgr"}:
        return _reshape_image(data, height, width, 3, msg.step).copy()
    if encoding in {"rgba8", "rgba"}:
        rgba = _reshape_image(data, height, width, 4, msg.step)
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
    if encoding in {"bgra8", "bgra"}:
        bgra = _reshape_image(data, height, width, 4, msg.step)
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
    if encoding == "mono8":
        gray = _reshape_image(data, height, width, 1, msg.step).reshape((height, width))
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if encoding in {"16uc1", "16sc1"}:
        dtype = np.uint16 if encoding == "16uc1" else np.int16
        depth = np.frombuffer(msg.data, dtype=dtype)
        depth = _reshape_depth(depth, height, width, msg.step, np.dtype(dtype).itemsize)
        depth_f = depth.astype(np.float32)
        max_value = float(depth_f.max()) if depth_f.size else 0.0
        gray = ((depth_f / max_value) * 255).astype(np.uint8) if max_value > 0 else np.zeros((height, width), dtype=np.uint8)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    channels = len(data) // (height * width) if height > 0 and width > 0 else 1
    if channels >= 3:
        return _reshape_image(data, height, width, channels, msg.step)[:, :, :3].copy()
    gray = data[: height * width].reshape((height, width))
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def ros_compressed_image_to_bgr(msg):
    if cv2 is None:  # pragma: no cover - runtime dependency
        raise RuntimeError("OpenCV is required for ROS image conversion")

    data = np.frombuffer(msg.data, dtype=np.uint8)
    frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"Failed to decode compressed ROS image ({msg.format})")
    return frame


def _reshape_image(data, height: int, width: int, channels: int, step: int):
    row_bytes = width * channels
    if step and step > row_bytes:
        rows = data.reshape((height, step))
        return rows[:, :row_bytes].reshape((height, width, channels))
    return data[: height * row_bytes].reshape((height, width, channels))


def _reshape_depth(data, height: int, width: int, step: int, itemsize: int):
    row_items = width
    step_items = int(step / itemsize) if step else row_items
    if step_items > row_items:
        rows = data.reshape((height, step_items))
        return rows[:, :row_items].reshape((height, width))
    return data[: height * row_items].reshape((height, width))


def _device_to_index(device: str) -> Optional[int]:
    if device.isdigit():
        return int(device)
    if device.startswith("/dev/video"):
        suffix = device.replace("/dev/video", "")
        if suffix.isdigit():
            return int(suffix)
    return None


def _apply_ros_environment(settings: CameraSourceSettings) -> None:
    if settings.ros_domain_id not in (None, ""):
        os.environ["ROS_DOMAIN_ID"] = str(settings.ros_domain_id)
    else:
        os.environ.setdefault("ROS_DOMAIN_ID", DEFAULT_ROS_DOMAIN_ID)
    if settings.ros_localhost_only not in (None, ""):
        os.environ["ROS_LOCALHOST_ONLY"] = str(settings.ros_localhost_only)
    else:
        os.environ.setdefault("ROS_LOCALHOST_ONLY", DEFAULT_ROS_LOCALHOST_ONLY)
    if settings.ros_fastdds_profile not in (None, ""):
        os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(settings.ros_fastdds_profile)
    else:
        os.environ.setdefault("FASTRTPS_DEFAULT_PROFILES_FILE", DEFAULT_FASTDDS_PROFILE)


def _import_ros_modules():
    try:
        import rclpy  # type: ignore
        from rclpy.node import Node  # type: ignore
        from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy  # type: ignore
        from sensor_msgs.msg import CompressedImage, Image  # type: ignore
    except ImportError as exc:  # pragma: no cover - ROS env specific
        raise RuntimeError(
            "ROS camera source requires rclpy and sensor_msgs in the active AimDK/ROS environment"
        ) from exc

    return (
        rclpy,
        Node,
        Image,
        CompressedImage,
        QoSProfile,
        ReliabilityPolicy,
        DurabilityPolicy,
        HistoryPolicy,
    )
