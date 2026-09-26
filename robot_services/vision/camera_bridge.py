#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

DEFAULT_AGIBOT_ROS_DOMAIN_ID = "232"
DEFAULT_AGIBOT_FASTDDS_PROFILE = "/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml"
ROS_ENV_REEXEC_MARKER = "CAMERA_BRIDGE_ROS_ENV_READY"


def _argv_value(flag: str) -> Optional[str]:
    prefix = f"{flag}="
    args = sys.argv[1:]
    for idx, arg in enumerate(args):
        if arg == flag and idx + 1 < len(args):
            return args[idx + 1]
        if arg.startswith(prefix):
            return arg.removeprefix(prefix)
    return None


def _requested_ros2_source() -> bool:
    source = _argv_value("--source") or os.getenv("LIVEKIT_CAMERA_SOURCE")
    device = _argv_value("--device") or os.getenv("LIVEKIT_CAMERA_DEVICE", "")
    normalized_device = str(device).strip().upper()
    return (
        str(source or "").strip().lower() == "ros2"
        or normalized_device.startswith("ROS2:")
        or str(device).strip().startswith("/aima/")
        or normalized_device in {
            "CHEST_LEFT_FISHEYE",
            "CHEST_RIGHT_FISHEYE",
            "INTERACTIVE_MAIN",
            "HEAD_FRONT_RGBD",
            "WAIST_FRONT_RGBD",
        }
    )


def _configure_ros_environment_defaults() -> None:
    os.environ["ROS_DOMAIN_ID"] = DEFAULT_AGIBOT_ROS_DOMAIN_ID
    os.environ["ROS_LOCALHOST_ONLY"] = "0"
    os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = DEFAULT_AGIBOT_FASTDDS_PROFILE


if (
    __name__ == "__main__"
    and _requested_ros2_source()
    and os.environ.get(ROS_ENV_REEXEC_MARKER) != "1"
):
    required_env = {
        "ROS_DOMAIN_ID": DEFAULT_AGIBOT_ROS_DOMAIN_ID,
        "ROS_LOCALHOST_ONLY": "0",
        "FASTRTPS_DEFAULT_PROFILES_FILE": DEFAULT_AGIBOT_FASTDDS_PROFILE,
    }
    if any(os.environ.get(key) != value for key, value in required_env.items()):
        env = os.environ.copy()
        env.update(required_env)
        env[ROS_ENV_REEXEC_MARKER] = "1"
        os.execvpe(sys.executable, [sys.executable, *sys.argv], env)

if _requested_ros2_source():
    _configure_ros_environment_defaults()

from dotenv import load_dotenv
from livekit import rtc

# Ensure repository root is on sys.path when running as a script
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from livekit_shared.auth import generate_token
from livekit_shared.video_devices import resolve_camera_device

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
DEFAULT_ROS_STARTUP_TIMEOUT_SECONDS = 10.0
DEMAND_POLL_TIMEOUT_SECONDS = 0.25

A2_ROS2_TOPICS = {
    "CHEST_LEFT_FISHEYE": "/aima/hal/fish_eye_camera/chest_left/color",
    "CHEST_RIGHT_FISHEYE": "/aima/hal/fish_eye_camera/chest_right/color",
    # Unlike every other camera on this robot, the chest-centre interactive
    # camera publishes no raw sensor_msgs/Image and no /compressed variant - only
    # an H.264 stream (foxglove_msgs/CompressedVideo). Subscribing to the
    # nonexistent ".../color" topic succeeded silently and then timed out waiting
    # for a frame, which is why this camera never worked.
    "INTERACTIVE_MAIN": "/aima/hal/camera/interactive/color/h264",
    "HEAD_FRONT_RGBD": "/aima/hal/rgbd_camera/head_front/color",
    "WAIST_FRONT_RGBD": "/aima/hal/rgbd_camera/waist_front/color",
}

# Native sensor geometry for cameras whose aspect ratio differs from the usual
# 16:9, plus the publish size that preserves it.
A2_ROS2_NATIVE_SIZE = {
    "INTERACTIVE_MAIN": (1920, 1536),   # 5:4
}
A2_ROS2_PREFERRED_SIZE = {
    "INTERACTIVE_MAIN": (1280, 1024),   # 5:4, same aspect as the sensor
}


def _is_h264_topic(topic: str) -> bool:
    """True when a topic carries foxglove_msgs/CompressedVideo, not raw images."""
    return str(topic).rstrip("/").endswith("/h264")

A2_CAMERA_ALIASES = {
    "CHEST_MAIN": "/dev/video0",
    "CHEST_FISHEYE_L": "/dev/video2",
    "CHEST_FISHEYE_R": "/dev/video4",
}


@dataclass
class BridgeConfig:
    url: str
    room: str
    service_name: str
    identity: str
    name: str
    topic: str
    send_to_agent: bool
    react_to_visuals: bool
    source: str
    device: str
    ros_startup_timeout: float
    width: Optional[int]
    height: Optional[int]
    framerate: Optional[float]
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
        self._capture: Optional[cv2.VideoCapture] = None
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

    def _open_capture(self) -> cv2.VideoCapture:
        capture_target, backend, resolved_device, apply_properties = _opencv_capture_target(
            self._device,
            width=self._width,
            height=self._height,
            framerate=self._framerate,
        )

        cap = cv2.VideoCapture(capture_target, backend)

        if not cap.isOpened():
            raise RuntimeError(
                f"Unable to open camera device {self._device} (resolved to {resolved_device})"
            )

        if apply_properties:
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
        cap = await loop.run_in_executor(None, self._open_capture)
        self._capture = cap

        self.actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
        self.actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.actual_fps = float(fps) if fps else None

        LOG.info(
            "Camera ready: %s (%sx%s @ %s fps)",
            self._device,
            self.actual_width or "auto",
            self.actual_height or "auto",
            f"{self.actual_fps:.2f}" if self.actual_fps else "auto",
        )

    def start_capture(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self._capture:
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
                return

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


class Ros2CameraCapture:
    def __init__(
        self,
        *,
        device: str,
        width: Optional[int],
        height: Optional[int],
        framerate: Optional[float],
        jpeg_quality: int,
        startup_timeout_s: float,
    ) -> None:
        self._device = device
        self._topic = _resolve_ros2_topic(device)
        self._is_h264 = _is_h264_topic(self._topic)
        self._decoder = None
        width, height = self._resolve_publish_size(device, width, height)
        self._width = width
        self._height = height
        self._framerate = framerate
        self._jpeg_quality = jpeg_quality
        self._startup_timeout_s = startup_timeout_s
        self.actual_width: Optional[int] = width
        self.actual_height: Optional[int] = height
        self.actual_fps: Optional[float] = framerate
        self._node = None
        self._subscription = None
        self._spin_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[CameraFrame] = None
        self._last_read_frame_id = 0
        self._next_frame_id = 0
        self._frames_captured = 0
        self._capture_failures = 0
        self._dropped_frames = 0
        self._capture_read_count = 0
        self._capture_read_total_s = 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._frame_event: Optional[asyncio.Event] = None

    @staticmethod
    def _resolve_publish_size(
        device: str,
        width: Optional[int],
        height: Optional[int],
    ) -> Tuple[Optional[int], Optional[int]]:
        """Keep a non-16:9 camera's aspect ratio instead of squashing it.

        The service-level `resolution` is usually 1280x720, but the interactive
        camera's sensor is 5:4. Resizing 1920x1536 into a 16:9 box stretches
        faces horizontally, which degrades face-recognition matches and looks
        wrong on the monitor. When the configured aspect ratio does not match the
        camera, fall back to a same-aspect size and say so. An operator who
        configures any 5:4 resolution keeps exactly what they asked for.
        """
        key = str(device).strip().upper()
        preferred = A2_ROS2_PREFERRED_SIZE.get(key)
        if preferred is None or not width or not height:
            return width, height

        preferred_aspect = preferred[0] / preferred[1]
        configured_aspect = width / height
        if abs(configured_aspect - preferred_aspect) <= 0.02 * preferred_aspect:
            return width, height

        native = A2_ROS2_NATIVE_SIZE.get(key)
        LOG.warning(
            "%s is natively %s (%.2f:1); configured %dx%d is %.2f:1 and would "
            "distort the image, so publishing at %dx%d instead. Configure a "
            "%.2f:1 resolution to override.",
            key,
            f"{native[0]}x{native[1]}" if native else "a non-16:9 format",
            preferred_aspect,
            width,
            height,
            configured_aspect,
            preferred[0],
            preferred[1],
            preferred_aspect,
        )
        return preferred

    async def open(self) -> None:
        _configure_ros_environment_defaults()
        self._loop = asyncio.get_running_loop()
        self._frame_event = asyncio.Event()
        self._start_ros_subscription(start_spin=False)
        LOG.info("ROS 2 camera subscribed: %s -> %s", self._device, self._topic)

        first_frame = await self._spin_until_first_frame(timeout_s=self._startup_timeout_s)
        if first_frame is None:
            self.close()
            raise RuntimeError(
                f"Timed out waiting for first ROS 2 camera frame from {self._topic} "
                f"after {self._startup_timeout_s:.1f}s"
            )
        self._start_spin_thread()

        frame = first_frame.frame
        if isinstance(frame, np.ndarray):
            height, width = frame.shape[:2]
            self.actual_width = int(width)
            self.actual_height = int(height)
        LOG.info(
            "ROS 2 camera ready: %s (%sx%s @ %s fps)%s",
            self._topic,
            self.actual_width or "auto",
            self.actual_height or "auto",
            f"{self.actual_fps:.2f}" if self.actual_fps else "auto",
            f" via {self._decoder.decoder_name}" if self._decoder is not None else "",
        )

    def _start_ros_subscription(self, *, start_spin: bool = True) -> None:
        try:
            import rclpy
            from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
            from sensor_msgs.msg import Image
        except ImportError as exc:
            raise RuntimeError(
                "ROS 2 camera source requires rclpy and sensor_msgs. "
                "Source the Agibot/AIMA ROS 2 environment before starting the supervisor."
            ) from exc

        if not rclpy.ok():
            rclpy.init()

        self._node = rclpy.create_node("camera_bridge_ros2")
        if self._is_h264:
            from foxglove_msgs.msg import CompressedVideo

            if self._decoder is None:
                from robot_services.vision.h264_decoder import H264FrameReceiver

                self._decoder = H264FrameReceiver(
                    label=str(self._device),
                    on_frame=self._on_decoded_frame,
                )
                self._decoder.start()
            # The tz_camera publisher offers BEST_EFFORT/TRANSIENT_LOCAL. Matching
            # BEST_EFFORT is required for a compatible match; VOLATILE is satisfied
            # by the offered TRANSIENT_LOCAL and avoids being handed a stale latched
            # frame on connect. depth=10 absorbs bursts while the decode thread is
            # busy - the callback itself only queues bytes.
            video_qos = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                durability=DurabilityPolicy.VOLATILE,
                history=HistoryPolicy.KEEP_LAST,
                depth=10,
            )
            self._subscription = self._node.create_subscription(
                CompressedVideo,
                self._topic,
                self._on_compressed_video,
                video_qos,
            )
        else:
            sensor_qos = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                history=HistoryPolicy.KEEP_LAST,
                depth=1,
            )
            self._subscription = self._node.create_subscription(
                Image,
                self._topic,
                self._on_image,
                sensor_qos,
            )
        self._stop_event.clear()
        if start_spin:
            self._start_spin_thread()

    def _start_spin_thread(self) -> None:
        if self._spin_thread and self._spin_thread.is_alive():
            return
        self._spin_thread = threading.Thread(
            target=self._spin_loop,
            name="camera-ros2-spin",
            daemon=True,
        )
        self._spin_thread.start()

    async def _spin_until_first_frame(self, *, timeout_s: float) -> Optional[CameraFrame]:
        if self._node is None:
            return None
        import rclpy
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and self._node is not None and rclpy.ok():
            with self._frame_lock:
                latest = self._latest_frame
                if latest is not None:
                    return latest
            rclpy.spin_once(self._node, timeout_sec=0.1)
            await asyncio.sleep(0)
        return None

    def _spin_loop(self) -> None:
        try:
            import rclpy
            while not self._stop_event.is_set() and self._node is not None and rclpy.ok():
                rclpy.spin_once(self._node, timeout_sec=0.1)
        except Exception:
            LOG.exception("ROS 2 camera spin failed")
            with self._frame_lock:
                self._capture_failures += 1
        finally:
            node = self._node
            self._node = None
            if node is not None:
                try:
                    node.destroy_node()
                except Exception:
                    LOG.exception("Failed to destroy ROS 2 camera node")

    def _on_compressed_video(self, msg: object) -> None:
        """Queue one H.264 access unit from a foxglove_msgs/CompressedVideo topic.

        Deliberately trivial: decoding here would stall the ROS executor and cost
        us UDP fragments of the next keyframe, which a BEST_EFFORT publisher never
        resends. See H264FrameReceiver for the measurements.
        """
        if self._decoder is not None:
            self._decoder.submit(msg.data)

    def _on_decoded_frame(self, frame: object) -> None:
        """Receive a decoded BGR frame from the H.264 decode thread."""
        self._store_bgr_frame(frame, time.monotonic())

    def _on_image(self, msg: object) -> None:
        started_at = time.monotonic()
        try:
            frame = _ros_image_to_bgr(msg)
        except Exception:
            LOG.exception("Failed to decode ROS 2 image from %s", self._topic)
            with self._frame_lock:
                self._capture_failures += 1
                self._capture_read_count += 1
                self._capture_read_total_s += time.monotonic() - started_at
            return
        self._store_bgr_frame(frame, started_at)

    def _store_bgr_frame(self, frame: object, started_at: float) -> None:
        try:
            if self._width and self._height and (
                frame.shape[1] != self._width or frame.shape[0] != self._height
            ):
                # INTER_AREA is the right filter for downscaling; the default
                # bilinear filter aliases noticeably going 1920x1536 -> 1280x1024.
                shrinking = frame.shape[1] > self._width and frame.shape[0] > self._height
                frame = cv2.resize(
                    frame,
                    (self._width, self._height),
                    interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR,
                )
            captured_at = time.monotonic()
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
                self._capture_read_total_s += captured_at - started_at
        except Exception:
            LOG.exception("Failed to store camera frame from %s", self._topic)
            with self._frame_lock:
                self._capture_failures += 1
                self._capture_read_count += 1
                self._capture_read_total_s += time.monotonic() - started_at
            return

        self._notify_frame_ready()

    def start_capture(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        if self._frame_event is None:
            self._frame_event = asyncio.Event()

    def set_target_capture_fps(self, fps: Optional[float]) -> None:
        return

    def stop_capture(self) -> None:
        self._stop_event.set()
        self._notify_frame_ready()
        if self._spin_thread and self._spin_thread.is_alive():
            self._spin_thread.join(timeout=2.0)
            if self._spin_thread.is_alive():
                LOG.warning("ROS 2 camera spin thread did not stop within timeout")
        self._spin_thread = None
        with self._frame_lock:
            self._latest_frame = None

    def _notify_frame_ready(self) -> None:
        if self._loop and self._frame_event and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._frame_event.set)

    async def read_latest(self, timeout_s: float) -> Optional[CameraFrame]:
        if not self._frame_event:
            raise RuntimeError("ROS 2 camera capture is not running")

        while True:
            if self._stop_event.is_set():
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
            raise RuntimeError("Timed out waiting for ROS 2 camera frame")
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
        # Tear the GStreamer pipeline down after stop_capture(), so the ROS
        # callback thread cannot push into a pipeline that is going away.
        if self._decoder is not None:
            try:
                self._decoder.close()
            except Exception:
                LOG.debug("Failed to close H.264 decoder", exc_info=True)
            self._decoder = None
        try:
            import rclpy
            rclpy.try_shutdown()
        except Exception:
            LOG.debug("ROS 2 shutdown skipped", exc_info=True)


def _resolve_ros2_topic(device: str) -> str:
    value = str(device).strip()
    if value.lower().startswith("ros2:"):
        value = value.split(":", 1)[1].strip()
    key = value.upper()
    if key in A2_ROS2_TOPICS:
        return A2_ROS2_TOPICS[key]
    if value.startswith("/"):
        return value
    aliases = ", ".join(sorted(A2_ROS2_TOPICS))
    raise ValueError(
        f"Unknown ROS 2 camera alias or topic: {device!r}. Known aliases: {aliases}. "
        "Raw ROS 2 topics should start with /."
    )


def _resolve_opencv_device(device: str) -> str:
    value = str(device).strip()
    key = value.upper()
    if key in A2_CAMERA_ALIASES:
        return A2_CAMERA_ALIASES[key]
    if key == "ORBBEC_GROIN":
        path = _find_orbbec_rgb_path()
        if path:
            return path
        raise ValueError("ORBBEC_GROIN was requested, but no Orbbec RGB video node was found.")
    return value


def _camera_source_for_device(source: str, device: str) -> str:
    normalized_source = str(source or "opencv").strip().lower()
    normalized_device = str(device or "").strip()
    key = normalized_device.upper()
    if normalized_source == "opencv" and (
        normalized_device.lower().startswith("ros2:")
        or key in A2_ROS2_TOPICS
        or normalized_device.startswith("/aima/")
    ):
        return "ros2"
    return normalized_source


def _ros_image_rows(msg: object) -> np.ndarray:
    data = np.frombuffer(msg.data, dtype=np.uint8)
    expected = int(msg.height) * int(msg.step)
    if expected and data.size >= expected:
        return data[:expected].reshape((msg.height, msg.step))
    return data


def _ros_image_to_bgr(msg: object) -> np.ndarray:
    height = int(msg.height)
    width = int(msg.width)
    encoding = str(msg.encoding).lower()
    rows = _ros_image_rows(msg)

    if encoding in ("bgr8", "bgr"):
        return rows[:, : width * 3].reshape((height, width, 3)).copy()
    if encoding in ("rgb8", "rgb"):
        rgb = rows[:, : width * 3].reshape((height, width, 3))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if encoding in ("bgra8", "bgra"):
        bgra = rows[:, : width * 4].reshape((height, width, 4))
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
    if encoding in ("rgba8", "rgba"):
        rgba = rows[:, : width * 4].reshape((height, width, 4))
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
    if encoding in ("mono8", "8uc1"):
        gray = rows[:, :width].reshape((height, width))
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if encoding in ("16uc1", "mono16"):
        data16 = np.frombuffer(msg.data, dtype=np.uint16).reshape((height, msg.step // 2))
        depth = data16[:, :width].astype(np.float32)
        max_value = float(depth.max())
        gray = (
            ((depth / max_value) * 255).astype(np.uint8)
            if max_value > 0
            else np.zeros((height, width), dtype=np.uint8)
        )
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if encoding in ("yuv422", "yuyv", "yuyv422"):
        yuyv = rows[:, : width * 2].reshape((height, width, 2))
        return cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUY2)

    channels = len(msg.data) // (height * width) if height and width else 0
    if channels >= 3:
        return np.frombuffer(msg.data, dtype=np.uint8).reshape((height, width, channels))[:, :, :3].copy()
    raise ValueError(f"unsupported encoding: {msg.encoding}")


def create_camera_capture(cfg: BridgeConfig):
    source = _camera_source_for_device(cfg.source, cfg.device)
    if source == "ros2":
        return Ros2CameraCapture(
            device=cfg.device,
            width=cfg.width,
            height=cfg.height,
            framerate=cfg.framerate,
            jpeg_quality=cfg.jpeg_quality,
            startup_timeout_s=cfg.ros_startup_timeout,
        )
    if source == "opencv":
        return CameraCapture(
            device=cfg.device,
            width=cfg.width,
            height=cfg.height,
            framerate=cfg.framerate,
            jpeg_quality=cfg.jpeg_quality,
        )
    raise ValueError(f"Unsupported camera source: {cfg.source}")


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


def _opencv_capture_target(
    device: str,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    framerate: Optional[float] = None,
) -> tuple[str | int, int, str, bool]:
    raw_device = _resolve_opencv_device(device)
    if raw_device.startswith("gst:"):
        return raw_device.removeprefix("gst:"), cv2.CAP_GSTREAMER, raw_device, False
    if raw_device.startswith("argus:"):
        sensor_id = int(raw_device.removeprefix("argus:"))
        pipeline = _argus_pipeline(sensor_id, width=width, height=height, framerate=framerate)
        return pipeline, cv2.CAP_GSTREAMER, raw_device, False
    if raw_device.startswith("gst-v4l2:"):
        resolved_device = resolve_camera_device(raw_device.removeprefix("gst-v4l2:"))
        pipeline = _gst_v4l2_pipeline(
            resolved_device,
            width=width,
            height=height,
            framerate=framerate,
        )
        return pipeline, cv2.CAP_GSTREAMER, resolved_device, False

    resolved_device = resolve_camera_device(raw_device)
    index = _device_to_index(str(resolved_device))
    return index if index is not None else resolved_device, _capture_backend(), resolved_device, True


def _gst_v4l2_pipeline(
    device: str,
    *,
    width: Optional[int],
    height: Optional[int],
    framerate: Optional[float],
) -> str:
    width = width or 1280
    height = height or 720
    fps = int(framerate or 30)
    return (
        f"v4l2src device={device} do-timestamp=true ! "
        f"video/x-raw,format=YUY2,width={width},height={height},framerate={fps}/1 ! "
        "videoconvert n-threads=2 ! video/x-raw,format=BGR ! "
        "appsink drop=true max-buffers=1 sync=false"
    )


def _argus_pipeline(
    sensor_id: int,
    *,
    width: Optional[int],
    height: Optional[int],
    framerate: Optional[float],
) -> str:
    width = width or 1280
    height = height or 720
    fps = int(framerate or 30)
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM),width={width},height={height},format=NV12,framerate={fps}/1 ! "
        "nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! video/x-raw,format=BGR ! "
        "appsink drop=true max-buffers=1 sync=false"
    )


def _capture_backend() -> int:
    if sys.platform.startswith("linux"):
        return cv2.CAP_V4L2
    if sys.platform == "darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
        return cv2.CAP_AVFOUNDATION
    return 0


def _probe_device_resolution(device: str) -> tuple[Optional[int], Optional[int]]:
    capture_target, backend, _resolved_device, _apply_properties = _opencv_capture_target(device)
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


def _video_sort_key(device: str) -> tuple[int, str]:
    suffix = device.replace("/dev/video", "")
    return (int(suffix), device) if suffix.isdigit() else (9999, device)


def _video_device_name(device: str) -> str:
    base = os.path.basename(device)
    sysfs_name = f"/sys/class/video4linux/{base}/name"
    try:
        return Path(sysfs_name).read_text(encoding="utf-8").strip()
    except OSError:
        return base


def _discover_video_devices() -> list[str]:
    return sorted(
        [device for device in glob.glob("/dev/video*") if os.path.exists(device)],
        key=_video_sort_key,
    )


def _find_orbbec_rgb_path() -> Optional[str]:
    for device in _discover_video_devices():
        name = _video_device_name(device).lower()
        if "orbbec" in name and "rgb" in name:
            return device
    return None


def _device_payload(
    *,
    path: str,
    name: str,
    source: str,
    idx: int,
    backend: Optional[str] = None,
    device: Optional[str] = None,
    probe_path: Optional[str] = None,
) -> dict[str, Any]:
    width: Optional[int] = None
    height: Optional[int] = None
    if probe_path:
        width, height = _probe_device_resolution(probe_path)
    return {
        "path": path,
        "name": name,
        "is_default": idx == 0,
        "source": source,
        "backend": backend or source,
        "device": device or path,
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else None,
        "has_valid_resolution": bool(width and height) or source == "ros2",
    }


def _list_devices() -> None:
    payload: list[dict[str, Any]] = []

    for alias, topic in A2_ROS2_TOPICS.items():
        payload.append(
            _device_payload(
                path=alias,
                name=alias.replace("_", " ").title(),
                source="ros2",
                idx=len(payload),
                backend="ros2",
                device=topic,
            )
        )

    if sys.platform.startswith("linux"):
        devices = _discover_video_devices()
        seen_paths = set()
        for alias, dev in A2_CAMERA_ALIASES.items():
            if not os.path.exists(dev):
                continue
            payload.append(
                _device_payload(
                    path=alias,
                    name=f"{alias.replace('_', ' ').title()} ({dev})",
                    source="opencv",
                    idx=len(payload),
                    backend="v4l2",
                    device=dev,
                    probe_path=dev,
                )
            )
            seen_paths.add(dev)

        groin = _find_orbbec_rgb_path()
        if groin:
            payload.append(
                _device_payload(
                    path="ORBBEC_GROIN",
                    name=f"Orbbec Groin RGB ({groin})",
                    source="opencv",
                    idx=len(payload),
                    backend="v4l2",
                    device=groin,
                    probe_path=groin,
                )
            )
            seen_paths.add(groin)

        for dev in devices:
            if dev in seen_paths:
                continue
            payload.append(
                _device_payload(
                    path=dev,
                    name=f"{_video_device_name(dev)} ({dev})",
                    source="opencv",
                    idx=len(payload),
                    backend="v4l2",
                    device=dev,
                    probe_path=dev,
                )
            )
    elif sys.platform == "darwin":
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
                    "source": "opencv",
                    "backend": "avfoundation",
                    "device": device,
                    "width": width,
                    "height": height,
                    "resolution": f"{width}x{height}",
                    "has_valid_resolution": True,
                }
            )

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
    parser.set_defaults(
        react_to_visuals=_parse_bool(os.getenv("LIVEKIT_CAMERA_REACT_TO_VISUALS"), False)
    )
    parser.add_argument(
        "--react-to-visuals",
        dest="react_to_visuals",
        action="store_true",
        help="Mark periodic snapshots as eligible for conservative visual gesture reactions",
    )
    parser.add_argument(
        "--no-react-to-visuals",
        dest="react_to_visuals",
        action="store_false",
        help="Disable visual gesture reaction hints",
    )
    parser.add_argument(
        "--source",
        choices=["opencv", "ros2"],
        default=os.getenv("LIVEKIT_CAMERA_SOURCE", "opencv"),
        help="Camera source backend: opencv for /dev/video/GStreamer, ros2 for Agibot AIMA image topics",
    )
    parser.add_argument(
        "--device",
        default=os.getenv("LIVEKIT_CAMERA_DEVICE", "/dev/video0"),
        help=(
            "Camera device path/index/serial, gst-v4l2:/dev/videoX, argus:N, gst:<pipeline>, "
            "or ROS 2 alias/topic when --source ros2"
        ),
    )
    parser.add_argument(
        "--ros-startup-timeout",
        type=float,
        default=os.getenv(
            "LIVEKIT_CAMERA_ROS_STARTUP_TIMEOUT",
            str(DEFAULT_ROS_STARTUP_TIMEOUT_SECONDS),
        ),
        help="Seconds to wait for the first ROS 2 camera frame before failing startup",
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
        ros_startup_timeout = _parse_optional_float(
            args.ros_startup_timeout,
            name="ROS startup timeout",
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
    if ros_startup_timeout is None:
        ros_startup_timeout = DEFAULT_ROS_STARTUP_TIMEOUT_SECONDS

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

    resolved_source = _camera_source_for_device(str(args.source).strip().lower(), args.device)

    return BridgeConfig(
        url=args.url,
        room=args.room,
        service_name=service_name,
        identity=args.identity,
        name=args.name or args.identity,
        topic=args.topic,
        send_to_agent=bool(args.send_to_agent),
        react_to_visuals=bool(args.react_to_visuals),
        source=resolved_source,
        device=args.device,
        ros_startup_timeout=ros_startup_timeout,
        width=width,
        height=height,
        framerate=framerate,
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
        "react_to_visuals": "true" if cfg.react_to_visuals else "false",
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

    camera = create_camera_capture(cfg)
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
        "Camera source=%s device=%s FPS: requested=%s actual=%s publish=%.2f encoding_max_fps=%.2f encoding_max_bitrate=%s idle=%.2f",
        cfg.source,
        cfg.device,
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
    if cfg.send_to_agent or cfg.react_to_visuals:
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
    if cfg.send_to_agent or cfg.react_to_visuals:
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
                        "Camera monitor demand %s; publishing at %.1f fps",
                        "active" if monitor_active else "inactive",
                        target_publish_fps if monitor_active else cfg.idle_fps,
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
                # Always feed the VideoSource, even with no browser watching.
                #
                # The track is published unconditionally at startup, so anything
                # that subscribes gets a stream regardless of demand. If we skip
                # capture_frame() while demand is inactive, libwebrtc keeps
                # encoding from a VideoSource buffer that was never written - an
                # all-zero I420 frame, which decodes to solid green
                # (Y=0,U=0,V=0 -> RGB 0,135,0). Every layer then reports success
                # while the viewer sees a green rectangle, which is exactly the
                # bug this replaced.
                #
                # Rate limiting still happens via next_frame_at below, which uses
                # idle_interval while demand is inactive. So this publishes at the
                # full rate when someone is watching and at cfg.idle_fps otherwise
                # - one BGR->RGB conversion every 0.5s, ~1ms of CPU, which keeps
                # essentially all of the savings the demand gate was added for.
                convert_started_at = time.monotonic()
                video_frame = publish_frame_builder.build(frame)
                telemetry.convert_total_s += time.monotonic() - convert_started_at
                # VideoSource.capture_frame is a synchronous method (returns None),
                # so do not await it. The previous code raised a TypeError when
                # awaiting a non-coroutine.
                video_source.capture_frame(video_frame)
                telemetry.publish_count += 1

                if (cfg.send_to_agent or cfg.react_to_visuals) and frame_started_at >= next_snapshot_at:
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
