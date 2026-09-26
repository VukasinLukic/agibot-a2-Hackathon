"""Blocking, cv2.VideoCapture-like wrapper around an Agibot A2 ROS 2 image topic.

Mirrors the topic aliases and image decoding already proven by
robot_services/vision/camera_bridge.py, but exposes a synchronous read()
interface so it can drop into the vision detector's blocking main loop.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import numpy as np

LOG = logging.getLogger(__name__)

DEFAULT_AGIBOT_ROS_DOMAIN_ID = "232"
DEFAULT_AGIBOT_FASTDDS_PROFILE = "/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml"

A2_ROS2_TOPICS = {
    "CHEST_LEFT_FISHEYE": "/aima/hal/fish_eye_camera/chest_left/color",
    "CHEST_RIGHT_FISHEYE": "/aima/hal/fish_eye_camera/chest_right/color",
    # The chest-centre interactive camera has no raw sensor_msgs/Image topic at
    # all - it publishes an H.264 stream only. Pointing this alias at
    # ".../color" made the subscription succeed and then never deliver a frame,
    # because ROS 2 happily subscribes to topics nobody publishes.
    "INTERACTIVE_MAIN": "/aima/hal/camera/interactive/color/h264",
    "HEAD_FRONT_RGBD": "/aima/hal/rgbd_camera/head_front/color",
    "WAIST_FRONT_RGBD": "/aima/hal/rgbd_camera/waist_front/color",
}

# Downscale target for H.264 cameras, chosen to preserve the source aspect
# ratio. The interactive camera is natively 1920x1536 (5:4); squashing that into
# a 16:9 box distorts faces, which measurably hurts face-embedding matches.
# 1280x1024 is the same 5:4 and cuts per-frame copy cost by ~1.8x.
A2_ROS2_PREFERRED_SIZE = {
    "INTERACTIVE_MAIN": (1280, 1024),
}


def is_h264_topic(topic: str) -> bool:
    """True when a topic carries foxglove_msgs/CompressedVideo, not raw images."""
    return str(topic).rstrip("/").endswith("/h264")


def _make_h264_receiver(*, label: str, on_frame):
    """Import the shared decoder lazily, tolerating flat-module import layout.

    The detector is launched with the repo root on PYTHONPATH but its own
    modules are imported flat (``from face_detector import ...``), so the repo
    root is added here explicitly rather than assumed.
    """
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from robot_services.vision.h264_decoder import H264FrameReceiver

    return H264FrameReceiver(label=label, on_frame=on_frame)


def is_ros2_camera_id(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.lower().startswith("ros2:"):
        return True
    if text.startswith("/aima/") or text.startswith("/"):
        return True
    return text.upper() in A2_ROS2_TOPICS


def resolve_ros2_topic(device: str) -> str:
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


def configure_ros_environment_defaults() -> None:
    """Fill in the AIMA DDS settings, without overriding an explicit choice.

    setdefault, not assignment: the supervisor's vision-controller service sets
    ROS_DOMAIN_ID from config (`ros_domain_id`), and clobbering that would break
    any robot not on domain 232.

    The trap this guards against is that inheriting a *wrong* domain fails
    silently - discovery simply finds nothing, every camera topic looks absent,
    and subscriptions succeed while never delivering a frame. A stray
    ROS_DOMAIN_ID/ROS_LOCALHOST_ONLY in the launching shell is enough to do it,
    so the effective values are logged and a non-AIMA domain is called out.
    """
    os.environ.setdefault("ROS_DOMAIN_ID", DEFAULT_AGIBOT_ROS_DOMAIN_ID)
    os.environ.setdefault("ROS_LOCALHOST_ONLY", "0")
    os.environ.setdefault("FASTRTPS_DEFAULT_PROFILES_FILE", DEFAULT_AGIBOT_FASTDDS_PROFILE)

    domain = os.environ["ROS_DOMAIN_ID"]
    localhost_only = os.environ["ROS_LOCALHOST_ONLY"]
    LOG.info(
        "ROS 2 discovery env: ROS_DOMAIN_ID=%s ROS_LOCALHOST_ONLY=%s FASTRTPS_DEFAULT_PROFILES_FILE=%s",
        domain,
        localhost_only,
        os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"],
    )
    if domain != DEFAULT_AGIBOT_ROS_DOMAIN_ID or localhost_only not in ("0", ""):
        LOG.warning(
            "ROS_DOMAIN_ID=%s ROS_LOCALHOST_ONLY=%s does not match the AIMA stack "
            "(domain %s, localhost_only 0). AIMA camera topics will not be "
            "discoverable and every camera will time out waiting for a first frame.",
            domain,
            localhost_only,
            DEFAULT_AGIBOT_ROS_DOMAIN_ID,
        )


def _ros_image_rows(msg: object) -> np.ndarray:
    data = np.frombuffer(msg.data, dtype=np.uint8)
    expected = int(msg.height) * int(msg.step)
    if expected and data.size >= expected:
        return data[:expected].reshape((msg.height, msg.step))
    return data


def _header_stamp_ns(msg: object) -> Optional[int]:
    header = getattr(msg, "header", None)
    stamp = getattr(header, "stamp", None)
    if stamp is None:
        return None
    sec = int(getattr(stamp, "sec", 0) or 0)
    nanosec = int(getattr(stamp, "nanosec", 0) or 0)
    if sec == 0 and nanosec == 0:
        return None
    return sec * 1_000_000_000 + nanosec


def ros_image_to_bgr(msg: object) -> np.ndarray:
    import cv2

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


class Ros2VideoCapture:
    """Minimal cv2.VideoCapture-compatible surface backed by a ROS 2 subscription."""

    def __init__(self, device: str, *, startup_timeout: float = 10.0) -> None:
        configure_ros_environment_defaults()
        try:
            import rclpy
            from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
            from sensor_msgs.msg import Image
        except ImportError as exc:
            raise RuntimeError(
                "ROS 2 camera source requires rclpy and sensor_msgs. "
                "Source the Agibot/AIMA ROS 2 environment before starting the vision detector."
            ) from exc

        self._topic = resolve_ros2_topic(device)
        self._is_h264 = is_h264_topic(self._topic)
        self._target_size = A2_ROS2_PREFERRED_SIZE.get(str(device).strip().upper())
        self._decoder = None
        self._frame_lock = threading.Condition()
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_seq = 0
        self._stamp_ns = 0
        self._opened = False
        self._stop_event = threading.Event()

        if not rclpy.ok():
            rclpy.init()
        self._node = rclpy.create_node("vision_controller_ros2")
        if self._is_h264:
            from foxglove_msgs.msg import CompressedVideo

            self._decoder = _make_h264_receiver(label=str(device), on_frame=self._store_frame)
            self._decoder.start()
            # The tz_camera publisher offers BEST_EFFORT/TRANSIENT_LOCAL. Matching
            # BEST_EFFORT is required for a compatible match; VOLATILE is
            # satisfied by the offered TRANSIENT_LOCAL and avoids being handed a
            # stale latched frame on connect. depth=10 absorbs bursts while the
            # decode thread is busy - the callback itself only queues bytes.
            video_qos = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                durability=DurabilityPolicy.VOLATILE,
                history=HistoryPolicy.KEEP_LAST,
                depth=10,
            )
            self._node.create_subscription(
                CompressedVideo, self._topic, self._on_compressed_video, video_qos
            )
        else:
            sensor_qos = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                history=HistoryPolicy.KEEP_LAST,
                depth=1,
            )
            self._node.create_subscription(Image, self._topic, self._on_image, sensor_qos)

        self._spin_thread = threading.Thread(
            target=self._spin_loop, name="vision-ros2-spin", daemon=True
        )
        self._spin_thread.start()

        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            with self._frame_lock:
                if self._latest_frame is not None:
                    self._opened = True
                    break
            time.sleep(0.05)

        if not self._opened:
            self.release()
            raise RuntimeError(
                f"Timed out waiting for first ROS 2 camera frame from {self._topic} "
                f"after {startup_timeout:.1f}s"
            )

        LOG.info("Vision detector ROS 2 camera ready: %s -> %s", device, self._topic)

    def _on_image(self, msg: object) -> None:
        try:
            frame = ros_image_to_bgr(msg)
        except Exception:
            LOG.exception("Failed to decode ROS 2 image from %s", self._topic)
            return
        self._store_frame(frame, _header_stamp_ns(msg))

    def _on_compressed_video(self, msg: object) -> None:
        # Deliberately trivial: queue the bytes and return immediately. Decoding
        # here would stall the ROS executor and cost us UDP fragments of the next
        # keyframe. See H264FrameReceiver for the measurements.
        if self._decoder is not None:
            self._decoder.submit(msg.data)

    def _store_frame(self, frame: np.ndarray, stamp_ns: Optional[int] = None) -> None:
        if self._target_size is not None:
            width, height = self._target_size
            if frame.shape[1] != width or frame.shape[0] != height:
                import cv2

                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        with self._frame_lock:
            self._latest_frame = frame
            self._frame_seq += 1
            self._stamp_ns = stamp_ns if stamp_ns is not None else time.monotonic_ns()
            self._frame_lock.notify_all()

    def _spin_loop(self) -> None:
        import rclpy

        try:
            while not self._stop_event.is_set() and rclpy.ok():
                rclpy.spin_once(self._node, timeout_sec=0.1)
        except Exception:
            LOG.exception("ROS 2 camera spin failed")

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        with self._frame_lock:
            frame = self._latest_frame
        if frame is None:
            return False, None
        return True, frame.copy()

    def read_if_new(self, after_seq: int, timeout_s: float):
        """Block until a frame newer than ``after_seq``.

        ``read`` stays a non-blocking copy for the person detector. Table-tennis
        capture uses this so it does not spin on the same picture or compare bytes.
        Returns ``(ok, frame, seq, stamp_ns)``. ``stamp_ns`` is the image header
        when the message has one.
        """
        deadline = time.monotonic() + timeout_s
        with self._frame_lock:
            while self._frame_seq <= after_seq:
                if self._stop_event.is_set():
                    return False, None, self._frame_seq, self._stamp_ns
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, None, self._frame_seq, self._stamp_ns
                self._frame_lock.wait(remaining)
            frame = self._latest_frame
            seq = self._frame_seq
            stamp = self._stamp_ns
        if frame is None:
            return False, None, seq, stamp
        return True, frame.copy(), seq, stamp

    def set(self, _prop, _value) -> bool:
        return False

    def get(self, prop) -> float:
        import cv2

        with self._frame_lock:
            frame = self._latest_frame
        if frame is None:
            return 0.0
        height, width = frame.shape[:2]
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(height)
        return 0.0

    def release(self) -> None:
        self._stop_event.set()
        with self._frame_lock:
            self._frame_lock.notify_all()
        if self._spin_thread.is_alive():
            self._spin_thread.join(timeout=2.0)
        # Stop the GStreamer pipeline only after the spin thread is done, so the
        # decode callback cannot touch a torn-down pipeline.
        if self._decoder is not None:
            try:
                self._decoder.close()
            except Exception:
                LOG.debug("Failed to close H.264 decoder", exc_info=True)
            self._decoder = None
        try:
            self._node.destroy_node()
        except Exception:
            LOG.debug("Failed to destroy ROS 2 node", exc_info=True)
        try:
            import rclpy

            rclpy.try_shutdown()
        except Exception:
            LOG.debug("ROS 2 shutdown skipped", exc_info=True)
