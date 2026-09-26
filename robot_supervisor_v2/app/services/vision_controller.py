"""
Vision Controller service implementation.
Runs the local vision detection worker.
"""

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseService, ConfigParameter, HealthCheckConfig, ServiceState
from robot_services.vision.detection.defaults import (
    DEFAULT_CAMERA_FOURCC,
    DEFAULT_CAMERA_FPS,
    DEFAULT_CARD_CAPTURE_FPS,
    DEFAULT_DETECTION_FPS,
)

logger = logging.getLogger(__name__)
DEFAULT_CAMERA_ID = 6

# Agibot A2 ROS 2 image topic aliases. Mirrors camera_bridge.py's alias set so a
# camera_id like "HEAD_FRONT_RGBD" routes the vision detector through ROS 2
# instead of an OpenCV /dev/video* device.
A2_ROS2_CAMERA_ALIASES = {
    "CHEST_LEFT_FISHEYE",
    "CHEST_RIGHT_FISHEYE",
    "INTERACTIVE_MAIN",
    "HEAD_FRONT_RGBD",
    "WAIST_FRONT_RGBD",
}
DETECTOR_RESTART_CONFIG_KEYS = {
    "python_bin",
    "working_dir",
    "detector_script",
    "supervisor_url",
    "model_name",
    "detection_fps",
    "card_capture_fps",
    "camera_id",
    "camera_fps",
    "camera_width",
    "camera_height",
    "camera_resolution",
    "camera_fourcc",
    "camera_buffer_size",
    "yolo_image_size",
    "opencv_num_threads",
    "torch_num_threads",
    "stats_interval_seconds",
    "status_url",
    "status_poll_interval_seconds",
    "status_timeout_seconds",
    "idle_fps",
    "detector_startup_grace_seconds",
    "ros_startup_timeout",
    "pythonpath",
    "ros_domain_id",
    "ros_localhost_only",
    "fastrtps_profile",
    # Face tuning below is read from the environment once at detector startup,
    # so changing it needs a restart. `enable_face_recognition` is deliberately
    # NOT listed here: the detector live-polls it from /api/vision every
    # status_poll_interval_seconds, so operators can flip the feature on and off
    # mid-session without dropping the camera or restarting the process.
    "face_match_threshold",
    "face_dedupe_threshold",
    "face_store_path",
    # Person-lock tuning is also read from the environment at startup.
    "close_height_ratio",
    "lock_stability_frames",
    "lock_stability_tolerance",
    "lock_x_stability_tolerance",
    "max_lost_frames",
    "presence_log_interval_seconds",
}


class VisionControllerService(BaseService):
    """Supervisor-managed vision detection service."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "Vision Controller"),
            config=config,
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None
        self._detector_process: Optional[subprocess.Popen] = None
        self._vision_controller = None

    def set_vision_controller(self, vision_controller) -> None:
        """Attach the API-layer vision controller used for mode toggling."""
        self._vision_controller = vision_controller

    @property
    def python_bin(self) -> str:
        return str(self._config.get("python_bin") or sys.executable)

    def _working_dir(self) -> str:
        return str(self._config.get("working_dir", "."))

    def _detector_command(self) -> List[str]:
        return [
            self.python_bin,
            str(self._config.get("detector_script", "robot_services/vision/detection/main.py")),
        ]

    def _is_ros2_camera(self) -> bool:
        value = str(self._config.get("camera_id", DEFAULT_CAMERA_ID) or "").strip()
        if value.lower().startswith("ros2:") or value.startswith("/aima/"):
            return True
        return value.upper() in A2_ROS2_CAMERA_ALIASES

    def _pythonpath_entries(self) -> List[str]:
        configured = self._config.get("pythonpath", [])
        if isinstance(configured, str):
            entries = [item.strip() for item in configured.split(os.pathsep) if item.strip()]
        elif isinstance(configured, list):
            entries = [str(item).strip() for item in configured if str(item).strip()]
        else:
            entries = []

        if self._is_ros2_camera() and not entries:
            entries = [
                "jetson_deps",
                ".",
                "/opt/ros/humble/lib/python3.10/site-packages",
                "/opt/ros/humble/local/lib/python3.10/dist-packages",
            ]

        base = Path(self._working_dir()).resolve()
        resolved = []
        for entry in entries:
            path = Path(entry).expanduser()
            if not path.is_absolute():
                path = base / path
            resolved.append(str(path))
        return resolved

    def _build_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        pythonpath_entries = self._pythonpath_entries()
        if pythonpath_entries:
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = os.pathsep.join(
                [*pythonpath_entries, *([existing_pythonpath] if existing_pythonpath else [])]
            )

        if self._is_ros2_camera():
            env["ROS_DOMAIN_ID"] = str(self._config.get("ros_domain_id", "232"))
            env["ROS_LOCALHOST_ONLY"] = str(self._config.get("ros_localhost_only", "0"))
            env["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(
                self._config.get(
                    "fastrtps_profile",
                    "/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml",
                )
            )

        supervisor_url = str(self._config.get("supervisor_url", "http://127.0.0.1:8080")).rstrip("/")
        env["VISION_SUPERVISOR_URL"] = supervisor_url
        env["VISION_PERSON_DETECTED_URL"] = f"{supervisor_url}/api/vision/person_detected"
        env["VISION_PERSON_LEFT_URL"] = f"{supervisor_url}/api/vision/person_left"
        env["VISION_CARD_CAPTURE_RESULT_URL"] = f"{supervisor_url}/api/vision/card-capture/result"
        env["VISION_FACE_CAPTURE_RESULT_URL"] = f"{supervisor_url}/api/vision/face/result"
        env["VISION_FACE_FORGET_RESULT_URL"] = f"{supervisor_url}/api/vision/face/forget/result"

        optional_env = {
            "model_name": "VISION_MODEL_NAME",
            "detection_fps": "VISION_DETECTION_FPS",
            "card_capture_fps": "VISION_CARD_CAPTURE_FPS",
            "camera_id": "VISION_CAMERA_ID",
            "camera_fps": "VISION_CAMERA_FPS",
            "camera_width": "VISION_CAMERA_WIDTH",
            "camera_height": "VISION_CAMERA_HEIGHT",
            "camera_fourcc": "VISION_CAMERA_FOURCC",
            "camera_buffer_size": "VISION_CAMERA_BUFFER_SIZE",
            "yolo_image_size": "VISION_YOLO_IMAGE_SIZE",
            "opencv_num_threads": "VISION_OPENCV_NUM_THREADS",
            "torch_num_threads": "VISION_TORCH_NUM_THREADS",
            "stats_interval_seconds": "VISION_STATS_INTERVAL_SECONDS",
            "status_url": "VISION_STATUS_URL",
            "status_poll_interval_seconds": "VISION_STATUS_POLL_INTERVAL_SECONDS",
            "status_timeout_seconds": "VISION_STATUS_TIMEOUT_SECONDS",
            "idle_fps": "VISION_IDLE_FPS",
            "ros_startup_timeout": "VISION_ROS_STARTUP_TIMEOUT_S",
            "face_match_threshold": "VISION_FACE_MATCH_THRESHOLD",
            "face_dedupe_threshold": "VISION_FACE_DEDUPE_THRESHOLD",
            "face_store_path": "VISION_FACE_STORE_PATH",
            "close_height_ratio": "VISION_CLOSE_HEIGHT_RATIO",
            "lock_stability_frames": "VISION_LOCK_STABILITY_FRAMES",
            "lock_stability_tolerance": "VISION_LOCK_STABILITY_TOLERANCE",
            "lock_x_stability_tolerance": "VISION_LOCK_X_STABILITY_TOLERANCE",
            "max_lost_frames": "VISION_MAX_LOST_FRAMES",
            "presence_log_interval_seconds": "VISION_PRESENCE_LOG_INTERVAL_S",
        }
        for config_key, env_key in optional_env.items():
            value = self._config.get(config_key)
            if value not in (None, ""):
                env[env_key] = str(value)

        camera_resolution = self._config.get("camera_resolution")
        if camera_resolution and "x" in str(camera_resolution).lower():
            width, height = str(camera_resolution).lower().split("x", 1)
            env.setdefault("VISION_CAMERA_WIDTH", width)
            env.setdefault("VISION_CAMERA_HEIGHT", height)

        return env

    def _write_start_divider(self) -> None:
        os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        divider = (
            f"\n{'=' * 80}\n"
            f"=== VISION CONTROLLER START at {timestamp} ===\n"
            f"=== Detector: {' '.join(self._detector_command())} ===\n"
            f"{'=' * 80}\n\n"
        )
        with open(self._log_file, "a") as f:
            f.write(divider)

    def _set_vision_enabled(self, enabled: bool) -> None:
        if not self._vision_controller:
            logger.warning("Vision API controller is not attached; cannot set enabled=%s", enabled)
            return
        self._vision_controller.set_enabled(enabled)

    def _start_process(self, cmd: List[str], env: Dict[str, str]) -> subprocess.Popen:
        if not self._log_handle:
            raise RuntimeError("Log handle is not open")
        logger.info("Starting %s", " ".join(cmd))
        return subprocess.Popen(
            cmd,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            cwd=self._working_dir(),
            env=env,
        )

    async def start(self) -> None:
        if self._state != ServiceState.STOPPED:
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            self._write_start_divider()
            self._log_handle = open(self._log_file, "a")
            env = self._build_env()

            self._set_vision_enabled(True)

            self._detector_process = self._start_process(self._detector_command(), env)
            self._process = self._detector_process

            await asyncio.sleep(float(self._config.get("detector_startup_grace_seconds", 2)))
            if self._detector_process.poll() is not None:
                raise RuntimeError("Vision detector exited immediately after start")

            self._state = ServiceState.RUNNING
            self._mark_started()

        except Exception as e:
            self._last_error = str(e)
            self._set_vision_enabled(False)
            await self._terminate_processes()
            self._close_log()
            self._state = ServiceState.FAILED
            raise

    async def stop(self) -> None:
        if self._state == ServiceState.STOPPED:
            return

        self._state = ServiceState.STOPPING
        self._set_vision_enabled(False)
        await self._terminate_processes()
        self._close_log()

        self._state = ServiceState.STOPPED
        self._start_time = None

    async def _terminate_processes(self) -> None:
        for process_name, process in (
            ("vision detector", self._detector_process),
        ):
            if not process:
                continue
            if process.poll() is None:
                logger.info("Stopping %s", process_name)
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)

        self._detector_process = None
        self._process = None

    def _close_log(self) -> None:
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

    async def check_health(self) -> bool:
        detector_running = (
            self._detector_process is not None
            and self._detector_process.poll() is None
        )
        return detector_running

    def get_dependencies(self) -> List[str]:
        return list(self._config.get("dependencies", ["livekit", "voice-agent", "audio-bridge"]))

    def get_health_config(self) -> HealthCheckConfig:
        return HealthCheckConfig(
            type="process",
            timeout_seconds=float(self._config.get("health_timeout", 2.0)),
            interval_seconds=float(self._config.get("health_interval", 5.0)),
        )

    def get_log_path(self) -> str:
        return self._log_file

    async def update_config(self, updates: Dict[str, Any]) -> None:
        logger.info("Updating vision controller config: %s", updates)
        needs_restart = any(key in DETECTOR_RESTART_CONFIG_KEYS for key in updates)
        self._config.update(updates)
        logger.info(
            "New config: camera_id=%s camera_resolution=%s camera_fps=%s enable_id_scanning=%s",
            self._config.get("camera_id", DEFAULT_CAMERA_ID),
            self._config.get("camera_resolution"),
            self._config.get("camera_fps", DEFAULT_CAMERA_FPS),
            self._config.get("enable_id_scanning", False),
        )
        if self._state == ServiceState.RUNNING and needs_restart:
            logger.info("Vision controller config changed, restarting service...")
            await self.restart()

    def get_config_parameters(self) -> List[ConfigParameter]:
        return [
            ConfigParameter(
                key="python_bin",
                value=self._config.get("python_bin"),
                type="string",
                description="Python interpreter from the vision detection virtualenv",
                required=False,
            ),
            ConfigParameter(
                key="working_dir",
                value=self._config.get("working_dir", "."),
                type="string",
                description="Working directory for vision processes",
                required=False,
            ),
            ConfigParameter(
                key="detector_script",
                value=self._config.get("detector_script", "robot_services/vision/detection/main.py"),
                type="string",
                description="Vision detector script",
                required=True,
            ),
            ConfigParameter(
                key="supervisor_url",
                value=self._config.get("supervisor_url", "http://127.0.0.1:8080"),
                type="string",
                description="Robot Supervisor API base URL for vision events",
                required=True,
            ),
            ConfigParameter(
                key="detection_fps",
                value=self._config.get("detection_fps", DEFAULT_DETECTION_FPS),
                type="number",
                description="Maximum detector loop FPS; lower values reduce CPU/GPU load",
                required=False,
            ),
            ConfigParameter(
                key="card_capture_fps",
                value=self._config.get("card_capture_fps", DEFAULT_CARD_CAPTURE_FPS),
                type="number",
                description="Maximum ID/card capture FPS while an ID scan is active",
                required=False,
            ),
            ConfigParameter(
                key="idle_fps",
                value=self._config.get("idle_fps", 2.0),
                type="number",
                description="Frame read/discard FPS while dispatch tracking is disabled",
                required=False,
            ),
            ConfigParameter(
                key="status_poll_interval_seconds",
                value=self._config.get("status_poll_interval_seconds", 0.5),
                type="number",
                description="Seconds between supervisor vision toggle polls",
                required=False,
            ),
            ConfigParameter(
                key="status_timeout_seconds",
                value=self._config.get("status_timeout_seconds", 0.25),
                type="number",
                description="Timeout for supervisor vision toggle polls",
                required=False,
            ),
            ConfigParameter(
                key="camera_resolution",
                value=self._config.get("camera_resolution"),
                type="string",
                description="Optional camera resolution as WIDTHxHEIGHT",
                required=False,
            ),
            ConfigParameter(
                key="camera_id",
                value=self._config.get("camera_id", DEFAULT_CAMERA_ID),
                type="string",
                description=(
                    "Camera device path, index, or serial (e.g. /dev/video0, 0, or 01.00.00), "
                    "or an Agibot A2 ROS 2 alias (e.g. HEAD_FRONT_RGBD) / raw topic starting with /"
                ),
                required=False,
            ),
            ConfigParameter(
                key="ros_startup_timeout",
                value=self._config.get("ros_startup_timeout", 10.0),
                type="number",
                description="Seconds to wait for the first ROS 2 camera frame before failing startup",
                required=False,
            ),
            ConfigParameter(
                key="camera_fps",
                value=self._config.get("camera_fps", DEFAULT_CAMERA_FPS),
                type="number",
                description="Requested camera capture FPS",
                required=False,
            ),
            ConfigParameter(
                key="camera_fourcc",
                value=self._config.get("camera_fourcc", DEFAULT_CAMERA_FOURCC),
                type="string",
                description="Requested camera pixel format/FourCC",
                required=False,
            ),
            ConfigParameter(
                key="camera_buffer_size",
                value=self._config.get("camera_buffer_size"),
                type="number",
                description="Optional OpenCV capture buffer size; leave unset for driver default",
                required=False,
            ),
            ConfigParameter(
                key="enable_id_scanning",
                value=self._config.get("enable_id_scanning", False),
                type="boolean",
                description="Allow request-gated ID/card capture jobs from the vision controller",
                required=False,
            ),
            ConfigParameter(
                key="enable_face_recognition",
                value=self._config.get("enable_face_recognition", False),
                type="boolean",
                description=(
                    "Recognize enrolled faces and greet people by name, and allow voice-driven "
                    "face enrollment / 'forget me'. Separate from the general vision toggle and "
                    "applied live (no detector restart). Default off."
                ),
                required=False,
            ),
            ConfigParameter(
                key="face_match_threshold",
                value=self._config.get("face_match_threshold", 0.62),
                type="number",
                description=(
                    "Cosine-similarity threshold for treating a face as a known person "
                    "(higher = stricter). Requires a detector restart."
                ),
                required=False,
            ),
            ConfigParameter(
                key="face_dedupe_threshold",
                value=self._config.get("face_dedupe_threshold", 0.75),
                type="number",
                description=(
                    "Cosine-similarity threshold above which a new enrollment is treated as an "
                    "already-known face instead of a new row. Requires a detector restart."
                ),
                required=False,
            ),
            ConfigParameter(
                key="face_store_path",
                value=self._config.get("face_store_path"),
                type="string",
                description=(
                    "Optional override for the local face database path. Absolute paths are used "
                    "as-is; relative paths resolve against the repo root, never the launching "
                    "process's working directory. Requires a detector restart."
                ),
                required=False,
            ),
            ConfigParameter(
                key="yolo_image_size",
                value=self._config.get("yolo_image_size", 640),
                type="number",
                description="YOLO inference image size",
                required=False,
            ),
        ]
