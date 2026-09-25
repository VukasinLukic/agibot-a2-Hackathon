"""
Camera Bridge service implementation.
Manages camera streaming to LiveKit with device enumeration.
"""
import subprocess
import asyncio
import glob
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List
from .base import BaseService, ServiceState, Device, DeviceType, ConfigParameter
import json
import logging

logger = logging.getLogger(__name__)
MONITOR_SESSION_TTL_SECONDS = 15.0

A2_ROS2_CAMERA_ALIASES = {
    "CHEST_LEFT_FISHEYE",
    "CHEST_RIGHT_FISHEYE",
    "INTERACTIVE_MAIN",
    "HEAD_FRONT_RGBD",
    "WAIST_FRONT_RGBD",
}
A2_V4L_CAMERA_ALIASES = {
    "CHEST_MAIN",
    "CHEST_FISHEYE_L",
    "CHEST_FISHEYE_R",
    "ORBBEC_GROIN",
}

class CameraBridgeService(BaseService):
    """Camera bridge with video device enumeration."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "Camera Bridge"),
            config=config
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None
        self._device_cache: List[Dict[str, Any]] = []
        self._device_cache_at = 0.0
        self._device_cache_ttl = float(config.get("device_cache_ttl", 10.0))
        self._monitor_sessions: Dict[str, float] = {}

    @staticmethod
    def _default_device_id() -> str:
        return "/dev/video0" if sys.platform.startswith("linux") else "0"

    @property
    def python_bin(self) -> str:
        return str(self._config.get("python_bin") or sys.executable)

    @property
    def stream_identity(self) -> str:
        return str(self._config.get("identity", self.name))

    @property
    def stream_display_name(self) -> str:
        return str(self._config.get("stream_name", self.display_name))

    @property
    def stream_topic(self) -> str:
        return str(self._config.get("topic", "images"))

    @property
    def stream_track_name(self) -> str:
        return str(self._config.get("video_track_name", "camera"))

    def _working_dir(self) -> str:
        return str(self._config.get("working_dir", "."))

    def _device_list_timeout(self) -> float:
        configured = self._config.get("device_list_timeout")
        if configured not in (None, ""):
            return float(configured)
        source = self.infer_source_for_device(
            self._config.get("device"),
            self._config.get("source", "opencv"),
        )
        return 20.0 if source == "ros2" else 5.0

    def _pythonpath_entries(self) -> List[str]:
        configured = self._config.get("pythonpath", self._config.get("python_path", []))
        if isinstance(configured, str):
            entries = [item.strip() for item in configured.split(os.pathsep) if item.strip()]
        elif isinstance(configured, list):
            entries = [str(item).strip() for item in configured if str(item).strip()]
        else:
            entries = []

        source = self.infer_source_for_device(
            self._config.get("device"),
            self._config.get("source", "opencv"),
        )
        if source == "ros2" and not entries:
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

    @staticmethod
    def infer_source_for_device(device: Any, default: str = "opencv") -> str:
        value = str(device or "").strip()
        key = value.upper()
        if value.lower().startswith("ros2:") or value.startswith("/aima/") or key in A2_ROS2_CAMERA_ALIASES:
            return "ros2"
        if key in A2_V4L_CAMERA_ALIASES or value.startswith("/dev/video") or value.isdigit():
            return "opencv"
        return str(default or "opencv").strip().lower()

    async def list_devices(self) -> List[Device]:
        raw_devices = self._query_devices_from_bridge(allow_probe=True)
        devices: List[Device] = []

        for idx, dev in enumerate(raw_devices):
            path = dev.get("path")
            if not path:
                continue

            devices.append(Device(
                id=path,
                name=dev.get("name") or f"Camera {idx} ({os.path.basename(path)})",
                type=DeviceType.VIDEO,
                is_default=dev.get("is_default", idx == 0),
                metadata={"index": idx, **dev}
            ))

        return devices
    async def start(self) -> None:
        """Start the camera bridge process."""
        if self._state != ServiceState.STOPPED:
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            cmd = self._build_command()
            logger.info("Starting camera bridge with command: %s", " ".join(cmd))

            # Ensure log directory exists
            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)

            env = os.environ.copy()
            self._apply_camera_environment(env)

            # Start process
            self._log_handle = open(self._log_file, 'a')
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                cwd=self._working_dir(),
                env=env,
            )

            # Give it a moment to fail if device is invalid
            await asyncio.sleep(2)

            if self._process.poll() is not None:
                self._state = ServiceState.FAILED
                self._last_error = "Process exited immediately after start"
                raise RuntimeError(self._last_error)

            self._state = ServiceState.RUNNING
            self._mark_started()

        except (Exception, asyncio.CancelledError) as e:
            self._state = ServiceState.FAILED
            self._last_error = str(e)
            self._cleanup_failed_start()
            self.clear_monitor_sessions()
            raise

    def _build_command(self) -> List[str]:
        device_id = str(self._config.get("device", self._default_device_id()))
        cmd = [
            self.python_bin,
            self._config.get("script", "robot_services/vision/camera_bridge.py"),
            "--service-name", self.name,
            "--source", str(self._config.get("source", "opencv")),
            "--device", device_id,
            "--room", self._config.get("room", "default"),
            "--identity", self.stream_identity,
            "--name", self.stream_display_name,
            "--topic", self.stream_topic,
            "--video-track-name", self.stream_track_name,
        ]

        if "interval" in self._config:
            cmd.extend(["--interval", str(self._config["interval"])])
        if "resolution" in self._config:
            cmd.extend(["--resolution", self._config["resolution"]])
        if "framerate" in self._config:
            cmd.extend(["--framerate", str(self._config["framerate"])])
        publish_fps = self._config.get("publish_fps")
        if publish_fps not in (None, ""):
            cmd.extend(["--publish-fps", str(publish_fps)])
        video_max_bitrate = self._config.get("video_max_bitrate")
        if video_max_bitrate not in (None, ""):
            cmd.extend(["--video-max-bitrate", str(video_max_bitrate)])
        video_max_framerate = self._config.get("video_max_framerate")
        if video_max_framerate not in (None, ""):
            cmd.extend(["--video-max-framerate", str(video_max_framerate)])
        idle_fps = self._config.get("idle_fps")
        if idle_fps not in (None, ""):
            cmd.extend(["--idle-fps", str(idle_fps)])
        ros_startup_timeout = self._config.get("ros_startup_timeout")
        if ros_startup_timeout not in (None, ""):
            cmd.extend(["--ros-startup-timeout", str(ros_startup_timeout)])
        demand_poll_interval = self._config.get("demand_poll_interval_seconds")
        if demand_poll_interval not in (None, ""):
            cmd.extend(["--demand-poll-interval-seconds", str(demand_poll_interval)])
        supervisor_url = self._config.get("supervisor_url")
        if supervisor_url not in (None, ""):
            cmd.extend(["--supervisor-url", str(supervisor_url)])
        demand_url = self._config.get("demand_url")
        if demand_url not in (None, ""):
            cmd.extend(["--demand-url", str(demand_url)])
        if self._config.get("send_to_agent", False) is True:
            cmd.append("--send-to-agent")
        else:
            cmd.append("--no-send-to-agent")
        if self._config.get("react_to_visuals", False) is True:
            cmd.append("--react-to-visuals")
        else:
            cmd.append("--no-react-to-visuals")
        return cmd

    def _apply_camera_environment(self, env: Dict[str, str]) -> None:
        pythonpath_entries = self._pythonpath_entries()
        if pythonpath_entries:
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = os.pathsep.join(
                [*pythonpath_entries, *([existing_pythonpath] if existing_pythonpath else [])]
            )

        source = self.infer_source_for_device(
            self._config.get("device"),
            self._config.get("source", "opencv"),
        )
        if source != "ros2":
            return

        ros_log_dir = Path(str(self._config.get("ros_log_dir", "tmp/ros_logs"))).expanduser()
        if not ros_log_dir.is_absolute():
            ros_log_dir = Path(self._working_dir()).resolve() / ros_log_dir
        try:
            ros_log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Failed to create ROS log directory %s", ros_log_dir, exc_info=True)
        env["ROS_LOG_DIR"] = str(ros_log_dir)
        env["ROS_DOMAIN_ID"] = str(self._config.get("ros_domain_id", "232"))
        env["ROS_LOCALHOST_ONLY"] = str(self._config.get("ros_localhost_only", "0"))
        env["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(
            self._config.get(
                "fastdds_profile",
                "/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml",
            )
        )
        env["CAMERA_BRIDGE_ROS_ENV_READY"] = "1"

    def _cleanup_failed_start(self) -> None:
        process = self._process
        self._process = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

    async def stop(self) -> None:
        """Stop the camera bridge process."""
        if self._state == ServiceState.STOPPED:
            return

        self._state = ServiceState.STOPPING

        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None

        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

        self._state = ServiceState.STOPPED
        self._start_time = None
        self.clear_monitor_sessions()

    async def check_health(self) -> bool:
        """Check if process is alive."""
        return self._process is not None and self._process.poll() is None

    async def update_config(self, updates: Dict[str, Any]) -> None:
        logger.info("Updating camera bridge config: %s", updates)
        self._config.update(updates)
        logger.info(
            "New config: device=%s, interval=%s, send_to_agent=%s, react_to_visuals=%s",
            self._config.get("device"),
            self._config.get("interval"),
            self._config.get("send_to_agent", False),
            self._config.get("react_to_visuals", False),
        )
        if self._state == ServiceState.RUNNING:
            logger.info("Camera bridge config changed, restarting service...")
            await self.restart()

    def get_dependencies(self) -> List[str]:
        """Camera bridge requires LiveKit server to post to tracks."""
        return ["livekit"]

    def get_log_path(self) -> str:
        """Return path to log file."""
        return self._log_file

    def get_config_parameters(self) -> List[ConfigParameter]:
        """Return configurable parameters."""
        return [
            ConfigParameter(
                key="python_bin",
                value=self._config.get("python_bin"),
                type="string",
                description="Python interpreter for this camera bridge (optional; defaults to supervisor interpreter)",
                required=False
            ),
            ConfigParameter(
                key="source",
                value=self._config.get("source", "opencv"),
                type="choice",
                choices=["opencv", "ros2"],
                description="Camera source backend",
                required=True
            ),
            ConfigParameter(
                key="device",
                value=self._config.get("device", self._default_device_id()),
                type="string",
                description="Camera path/index/serial, gst-v4l2:/dev/videoX, argus:N, gst:<pipeline>, or ROS 2 alias/topic",
                required=True
            ),
            ConfigParameter(
                key="resolution",
                value=self._config.get("resolution", "960x540"),
                type="choice",
                choices=["640x480", "960x540", "1280x720", "1920x1080"],
                description="Video resolution",
                required=False
            ),
            ConfigParameter(
                key="framerate",
                value=self._config.get("framerate", 15),
                type="int",
                description="Requested camera capture frames per second",
                required=False
            ),
            ConfigParameter(
                key="publish_fps",
                value=self._config.get("publish_fps"),
                type="float",
                description="LiveKit video publish frames per second (optional)",
                required=False
            ),
            ConfigParameter(
                key="video_max_bitrate",
                value=self._config.get("video_max_bitrate", 1_500_000),
                type="int",
                description="LiveKit video encoding max bitrate in bits per second",
                required=False
            ),
            ConfigParameter(
                key="video_max_framerate",
                value=self._config.get("video_max_framerate"),
                type="float",
                description="LiveKit video encoding max framerate (optional)",
                required=False
            ),
            ConfigParameter(
                key="interval",
                value=self._config.get("interval", 3.0),
                type="float",
                description="Seconds between agent image snapshots",
                required=False
            ),
            ConfigParameter(
                key="send_to_agent",
                value=self._config.get("send_to_agent", False),
                type="bool",
                description="Send periodic JPEG snapshots to the agent byte stream",
                required=False
            ),
            ConfigParameter(
                key="react_to_visuals",
                value=self._config.get("react_to_visuals", False),
                type="bool",
                description="Allow conservative gesture reactions based on periodic visual snapshots",
                required=False
            ),
            ConfigParameter(
                key="room",
                value=self._config.get("room", "default"),
                type="string",
                description="LiveKit room name",
                required=True
            ),
            ConfigParameter(
                key="identity",
                value=self.stream_identity,
                type="string",
                description="LiveKit participant identity for this camera stream",
                required=True
            ),
            ConfigParameter(
                key="topic",
                value=self.stream_topic,
                type="string",
                description="LiveKit byte-stream topic used for JPEG snapshots",
                required=True
            ),
            ConfigParameter(
                key="video_track_name",
                value=self.stream_track_name,
                type="string",
                description="LiveKit video track name used by the browser monitor",
                required=True
            ),
            ConfigParameter(
                key="idle_fps",
                value=self._config.get("idle_fps", 2.0),
                type="float",
                description="Camera read/discard FPS while no monitor is watching",
                required=False
            ),
            ConfigParameter(
                key="demand_poll_interval_seconds",
                value=self._config.get("demand_poll_interval_seconds", 0.5),
                type="float",
                description="Seconds between bridge monitor-demand polls",
                required=False
            ),
            ConfigParameter(
                key="ros_startup_timeout",
                value=self._config.get("ros_startup_timeout", 10.0),
                type="float",
                description="Seconds to wait for the first ROS 2 camera frame when source=ros2",
                required=False
            )
        ]

    def _expire_monitor_sessions(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        expired = [
            session_id
            for session_id, last_seen in self._monitor_sessions.items()
            if now - last_seen > MONITOR_SESSION_TTL_SECONDS
        ]
        for session_id in expired:
            self._monitor_sessions.pop(session_id, None)

    def create_monitor_session(self) -> Dict[str, Any]:
        self._expire_monitor_sessions()
        session_id = uuid.uuid4().hex
        self._monitor_sessions[session_id] = time.monotonic()
        return self.get_monitor_demand(session_id=session_id)

    def heartbeat_monitor_session(self, session_id: str) -> Dict[str, Any]:
        self._expire_monitor_sessions()
        self._monitor_sessions[session_id] = time.monotonic()
        return self.get_monitor_demand(session_id=session_id)

    def delete_monitor_session(self, session_id: str) -> Dict[str, Any]:
        self._monitor_sessions.pop(session_id, None)
        return self.get_monitor_demand(session_id=session_id)

    def clear_monitor_sessions(self) -> None:
        self._monitor_sessions.clear()

    def get_monitor_demand(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        self._expire_monitor_sessions()
        return {
            "service_name": self.name,
            "session_id": session_id,
            "active": bool(self._monitor_sessions),
            "monitor_sessions": len(self._monitor_sessions),
        }

    def _query_devices_from_bridge(self, *, allow_probe: bool = True) -> List[Dict[str, Any]]:
        now = time.monotonic()
        if self._device_cache and (
            not allow_probe or (now - self._device_cache_at) < self._device_cache_ttl
        ):
            return self._device_cache
        if not allow_probe:
            return self._lightweight_video_devices()

        try:
            script = self._config.get("script", "robot_services/vision/camera_bridge.py")
            env = os.environ.copy()
            self._apply_camera_environment(env)
            result = subprocess.run(
                [self.python_bin, script, "--list-devices"],
                capture_output=True,
                text=True,
                timeout=self._device_list_timeout(),
                cwd=self._working_dir(),
                env=env,
            )

            if result.returncode == 0:
                payload = json.loads(result.stdout)
                self._device_cache = payload if isinstance(payload, list) else []
                self._device_cache_at = now
                return self._device_cache

            logger.error(f"Camera device listing failed (returncode={result.returncode}): {result.stderr}")
            logger.error(f"stdout: {result.stdout}")
            return self._device_cache
        except subprocess.TimeoutExpired:
            logger.error("Camera device listing timed out")
            return self._device_cache
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse camera device JSON: {e}")
            return self._device_cache
        except Exception as e:
            logger.error(f"Failed to query camera devices from bridge: {e}", exc_info=True)
            return self._device_cache

    def _lightweight_video_devices(self) -> List[Dict[str, Any]]:
        if sys.platform.startswith("linux"):
            raw_devices = [
                {
                    "path": alias,
                    "name": alias.replace("_", " ").title(),
                    "is_default": idx == 0,
                    "source": "ros2",
                    "backend": "ros2",
                    "width": None,
                    "height": None,
                    "resolution": None,
                    "has_valid_resolution": True,
                }
                for idx, alias in enumerate(sorted(A2_ROS2_CAMERA_ALIASES))
            ]
            raw_devices.extend(
                {
                    "path": dev,
                    "name": dev,
                    "is_default": False,
                    "source": "opencv",
                    "backend": "v4l2",
                    "width": None,
                    "height": None,
                    "resolution": None,
                    "has_valid_resolution": False,
                }
                for dev in sorted(glob.glob("/dev/video*"))
            )
            if raw_devices:
                return raw_devices

        configured_device = str(self._config.get("device", self._default_device_id()))
        return [
            {
                "path": configured_device,
                "name": configured_device,
                "is_default": True,
                "source": self.infer_source_for_device(configured_device, self._config.get("source", "opencv")),
                "width": None,
                "height": None,
                "resolution": None,
                "has_valid_resolution": False,
            }
        ]

    def get_video_devices(self, *, allow_probe: bool = True) -> List[Dict[str, Any]]:
        raw_devices = self._query_devices_from_bridge(allow_probe=allow_probe)
        devices: List[Dict[str, Any]] = []

        for idx, dev in enumerate(raw_devices):
            path = dev.get("path")
            if not path:
                continue

            devices.append({
                "index": idx,
                "path": path,
                "name": dev.get("name") or f"Camera {idx} ({os.path.basename(path)})",
                "is_default": dev.get("is_default", idx == 0),
                "width": dev.get("width"),
                "height": dev.get("height"),
                "resolution": dev.get("resolution"),
                "has_valid_resolution": bool(dev.get("has_valid_resolution")),
                "source": dev.get("source") or self.infer_source_for_device(path, self._config.get("source", "opencv")),
                "backend": dev.get("backend"),
                "device": dev.get("device", path),
            })

        return devices
