"""
Open a camera for IGRA by friendly name, alias, device path, or index.

Primary robot target:
  Chest Right Fisheye → A2 ROS2 topic /aima/hal/fish_eye_camera/chest_right/color
  (alias CHEST_RIGHT_FISHEYE already used by robot_services.vision)

On a laptop without ROS2, falls back to OpenCV device 0 when the requested
camera cannot be opened as ROS2.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("igra.camera")

# Friendly display names (UI / operator) → canonical alias
NAME_ALIASES: dict[str, str] = {
    "chest right fisheye": "CHEST_RIGHT_FISHEYE",
    "chest_right_fisheye": "CHEST_RIGHT_FISHEYE",
    "chest-right-fisheye": "CHEST_RIGHT_FISHEYE",
    "chest right": "CHEST_RIGHT_FISHEYE",
    "right fisheye": "CHEST_RIGHT_FISHEYE",
    "fisheye right": "CHEST_RIGHT_FISHEYE",
    "chest left fisheye": "CHEST_LEFT_FISHEYE",
    "chest_left_fisheye": "CHEST_LEFT_FISHEYE",
    "interactive main": "INTERACTIVE_MAIN",
    "head front": "HEAD_FRONT_RGBD",
    "waist front": "WAIST_FRONT_RGBD",
}

# OpenCV fallbacks when ROS is unavailable (from camera_demo.py)
OPENCV_FALLBACKS: dict[str, str] = {
    "CHEST_RIGHT_FISHEYE": "/dev/video4",
    "CHEST_LEFT_FISHEYE": "/dev/video2",
    "CHEST_MAIN": "/dev/video0",
}


def normalize_camera_id(value: str | int | None) -> str:
    raw = str(value if value is not None else "0").strip()
    if not raw:
        return "0"
    key = re.sub(r"\s+", " ", raw).strip().lower()
    if key in NAME_ALIASES:
        return NAME_ALIASES[key]
    # already canonical alias
    upper = raw.replace(" ", "_").upper()
    if upper in {"CHEST_FISHEYE_R", "CHEST_RIGHT"}:
        return "CHEST_RIGHT_FISHEYE"
    if upper in {"CHEST_FISHEYE_L", "CHEST_LEFT"}:
        return "CHEST_LEFT_FISHEYE"
    if upper in {
        "CHEST_RIGHT_FISHEYE",
        "CHEST_LEFT_FISHEYE",
        "INTERACTIVE_MAIN",
        "HEAD_FRONT_RGBD",
        "WAIST_FRONT_RGBD",
        "CHEST_MAIN",
    }:
        return upper
    return raw


def _try_import_ros2():
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from robot_services.vision.detection.ros2_capture import (  # type: ignore
        Ros2VideoCapture,
        is_ros2_camera_id,
        resolve_ros2_topic,
    )

    return Ros2VideoCapture, is_ros2_camera_id, resolve_ros2_topic


def open_camera(device: str | int | None, *, startup_timeout: float = 10.0) -> tuple[Any, str, str]:
    """
    Open camera. Returns (capture, resolved_id, backend) where backend is
    'ros2' or 'opencv'.
    """
    import cv2

    requested = normalize_camera_id(device)
    logger.info("Opening IGRA camera request=%r resolved=%r", device, requested)

    # Prefer ROS2 for A2 aliases / topics.
    try:
        Ros2VideoCapture, is_ros2_camera_id, resolve_ros2_topic = _try_import_ros2()
        if is_ros2_camera_id(requested):
            topic = resolve_ros2_topic(requested)
            cap = Ros2VideoCapture(requested, startup_timeout=startup_timeout)
            if cap.isOpened():
                logger.info("IGRA camera ROS2 ready: %s -> %s", requested, topic)
                return cap, requested, "ros2"
    except Exception as exc:
        logger.warning("ROS2 camera open failed for %s: %s", requested, exc)

    # OpenCV path / index / by-id
    opencv_target = OPENCV_FALLBACKS.get(requested, requested)
    try:
        from livekit_shared.video_devices import resolve_camera_device

        opencv_target = resolve_camera_device(str(opencv_target))
    except Exception:
        pass

    if str(opencv_target).isdigit():
        target: int | str = int(opencv_target)
    else:
        target = str(opencv_target)

    backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else 0
    cap = cv2.VideoCapture(target, backend)
    if not cap.isOpened() and backend:
        cap = cv2.VideoCapture(target)
    if not cap.isOpened():
        # Last resort: default webcam (laptop).
        if str(requested) not in {"0", "1"}:
            logger.warning(
                "Could not open %s (%s); falling back to OpenCV index 0 (laptop)",
                requested,
                opencv_target,
            )
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                return cap, "0", "opencv"
        raise RuntimeError(
            f"Could not open camera {device!r} (resolved {requested!r} / {opencv_target!r})"
        )

    logger.info("IGRA camera OpenCV ready: %s -> %s", requested, opencv_target)
    return cap, str(opencv_target), "opencv"
