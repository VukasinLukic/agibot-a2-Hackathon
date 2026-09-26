"""
Audio Bridge service implementation.
Manages audio streaming to LiveKit with separate input/output device enumeration.
"""

import subprocess
import asyncio
import os
import sys
import json
import logging
import urllib.request
from typing import Dict, Any, List

from humanoid_platform.types import RobotModelId
from robot_services.agibot.aima_em import AgibotAimaConfig, AgibotAimaManager

from .base import BaseService, ServiceState, Device, DeviceType, ConfigParameter
from ..supervisor_config import SERVICE_ROBOT_CONTEXT_KEY

logger = logging.getLogger(__name__)


class AudioBridgeService(BaseService):
    """Audio bridge with separate input/output device enumeration."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "Audio Bridge"),
            config=config
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None
        self.aima = AgibotAimaManager(self._aima_config())
        try:
            self.aima.reconcile_on_startup()
        except RuntimeError as exc:
            self._last_error = str(exc)

    def _aima_config(self) -> AgibotAimaConfig:
        configured = self._config.get("agibot_aima")
        if configured is not None:
            if isinstance(configured, AgibotAimaConfig):
                return configured
            if isinstance(configured, dict):
                return AgibotAimaConfig(**configured)
            raise ValueError("audio-bridge agibot_aima config must be a mapping")

        robot_context = self._config.get(SERVICE_ROBOT_CONTEXT_KEY)
        if isinstance(robot_context, dict) and robot_context.get("model") == RobotModelId.AGIBOT_A2_ULTRA.value:
            return AgibotAimaConfig(enabled=True)

        return AgibotAimaConfig()

    def _input_mix_channels(self) -> list[Any]:
        configured = self._config.get("input_mix_channels")
        if configured is None:
            return []
        if isinstance(configured, list):
            return configured
        if isinstance(configured, tuple):
            return list(configured)
        return [configured]

    def _query_devices_from_bridge(self) -> Dict[str, Any]:
        """
        Query bridge script for device information via CLI.

        Returns:
            Dict with input_devices and output_devices lists
        """
        try:
            script = self._config.get("script", "robot_services/audio/audio_bridge.py")
            result = subprocess.run(
                [sys.executable, script, "--list-devices"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=self._config.get("working_dir", ".")
            )

            if result.returncode == 0:
                return json.loads(result.stdout)
            else:
                logger.error(f"Device listing failed (returncode={result.returncode}): {result.stderr}")
                logger.error(f"stdout: {result.stdout}")
                return {"input_devices": [], "output_devices": []}

        except subprocess.TimeoutExpired:
            logger.error("Device listing timed out")
            return {"input_devices": [], "output_devices": []}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse device JSON: {e}")
            logger.error(f"Raw output was: {result.stdout if 'result' in locals() else 'N/A'}")
            return {"input_devices": [], "output_devices": []}
        except Exception as e:
            logger.error(f"Failed to query devices from bridge: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"input_devices": [], "output_devices": []}

    def _get_control_port(self) -> int:
        """Return the localhost control port for the bridge process."""
        return int(self._config.get("control_port", 8766))

    def _normalize_devices(self, devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return bridge-reported devices without supervisor-side fabrication."""
        normalized: List[Dict[str, Any]] = []
        for raw_device in devices:
            device = dict(raw_device)
            device.setdefault("connected", True)
            normalized.append(device)
        return normalized

    def _bridge_control_request(
        self,
        method: str,
        path: str,
        payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Send a localhost control request to the running bridge."""
        url = f"http://127.0.0.1:{self._get_control_port()}{path}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))

    async def list_devices(self) -> List[Device]:
        """Enumerate audio input and output devices separately."""
        devices = []

        # Query devices from bridge script
        device_info = self._query_devices_from_bridge()

        # Convert input devices
        for dev in self._normalize_devices(device_info.get("input_devices", [])):
            devices.append(Device(
                id=f"input_{dev['index']}",
                name=dev['name'],
                type=DeviceType.AUDIO_INPUT,
                is_default=dev.get('is_default', False),
                metadata={
                    "index": dev['index'],
                    "channels": dev['channels'],
                    "samplerate": dev['sample_rate'],
                    "hostapi": dev.get('hostapi', 'Unknown'),
                    "connected": dev.get("connected", True),
                    "always_present": dev.get("always_present", False),
                    "forced": dev.get("forced", False),
                    "alsa_device": dev.get("alsa_device"),
                    "alsa_card_id": dev.get("alsa_card_id"),
                    "alsa_device_index": dev.get("alsa_device_index"),
                    "id_path": dev.get("id_path"),
                    "id_path_tag": dev.get("id_path_tag"),
                }
            ))

        # Convert output devices
        for dev in self._normalize_devices(device_info.get("output_devices", [])):
            devices.append(Device(
                id=f"output_{dev['index']}",
                name=dev['name'],
                type=DeviceType.AUDIO_OUTPUT,
                is_default=dev.get('is_default', False),
                metadata={
                    "index": dev['index'],
                    "channels": dev['channels'],
                    "samplerate": dev['sample_rate'],
                    "hostapi": dev.get('hostapi', 'Unknown'),
                    "connected": dev.get("connected", True),
                    "always_present": dev.get("always_present", False),
                    "forced": dev.get("forced", False),
                    "alsa_device": dev.get("alsa_device"),
                    "alsa_card_id": dev.get("alsa_card_id"),
                    "alsa_device_index": dev.get("alsa_device_index"),
                    "id_path": dev.get("id_path"),
                    "id_path_tag": dev.get("id_path_tag"),
                }
            ))

        return devices

    def _build_audio_device_payload(self, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        """Normalize a single bridge device snapshot for the frontend."""

        return {
            "input_devices": self._normalize_devices(device_info.get("input_devices", [])),
            "output_devices": self._normalize_devices(device_info.get("output_devices", [])),
        }

    def get_audio_devices(self) -> Dict[str, List[Dict[str, Any]]]:
        """Query bridge script once and return both input and output devices."""
        device_info = self._query_devices_from_bridge()
        return self._build_audio_device_payload(device_info)

    def get_input_devices(self) -> List[Dict[str, Any]]:
        """
        Query bridge script for input devices.

        Returns:
            List of input device dicts
        """
        return self.get_audio_devices()["input_devices"]

    def get_output_devices(self) -> List[Dict[str, Any]]:
        """
        Query bridge script for output devices.

        Returns:
            List of output device dicts
        """
        return self.get_audio_devices()["output_devices"]

    async def get_bridge_status(self) -> Dict[str, Any]:
        """Get live mute status from the running bridge process."""
        if self._state != ServiceState.RUNNING or not self._process or self._process.poll() is not None:
            return {
                "running": False,
                "muted": False,
                "control_port": self._get_control_port(),
                "input_mic_gain_db": 0.0,
                "input_mic_gain_db_min": -40.0,
                "input_mic_gain_db_max": 12.0,
            }

        try:
            return await asyncio.to_thread(self._bridge_control_request, "GET", "/status")
        except Exception as e:
            logger.warning("Failed to query bridge status: %s", e)
            return {
                "running": True,
                "muted": False,
                "control_port": self._get_control_port(),
                "control_available": False,
                "input_mic_gain_db": -26.0,
                "input_mic_gain_db_min": -40.0,
                "input_mic_gain_db_max": 12.0,
                "error": str(e),
            }

    async def set_muted(self, muted: bool) -> Dict[str, Any]:
        """Set bridge microphone mute state without restarting the process."""
        if self._state != ServiceState.RUNNING or not self._process or self._process.poll() is not None:
            raise RuntimeError("Audio bridge is not running")

        return await asyncio.to_thread(
            self._bridge_control_request,
            "POST",
            "/mute",
            {"muted": muted},
        )

    async def set_input_mic_gain_db(self, gain_db: float) -> Dict[str, Any]:
        """Set bridge microphone input gain without restarting the process."""
        if self._state != ServiceState.RUNNING or not self._process or self._process.poll() is not None:
            raise RuntimeError("Audio bridge is not running")

        return await asyncio.to_thread(
            self._bridge_control_request,
            "POST",
            "/input-gain",
            {"input_mic_gain_db": gain_db},
        )

    async def set_output_speaker_gain_db(self, gain_db: float) -> Dict[str, Any]:
        if self._state != ServiceState.RUNNING or not self._process or self._process.poll() is not None:
            raise RuntimeError("Audio bridge is not running")
        return await asyncio.to_thread(
            self._bridge_control_request,
            "POST",
            "/output-gain",
            {"output_speaker_gain_db": gain_db},
        )

    async def release_remote_playback(self) -> Dict[str, Any]:
        """Clear the active remote audio playback track without restarting the bridge."""
        if self._state != ServiceState.RUNNING or not self._process or self._process.poll() is not None:
            raise RuntimeError("Audio bridge is not running")

        return await asyncio.to_thread(
            self._bridge_control_request,
            "POST",
            "/remote-playback/release",
            {},
        )

    async def get_aima_status(self) -> Dict[str, Any]:
        return self.aima.status()

    async def set_aima_audio_bridge_mode(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.aima.switch_audio_bridge)

    async def set_aima_agibot_mode(self) -> Dict[str, Any]:
        if self._state == ServiceState.RUNNING and self._process and self._process.poll() is None:
            raise RuntimeError("Cannot switch AIMA to agibot mode while audio bridge is running")
        return await asyncio.to_thread(self.aima.switch_agibot)

    async def run_aima_doctor(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.aima.doctor)

    async def start(self) -> None:
        """Start the audio bridge process."""
        if self._state != ServiceState.STOPPED:
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            self.aima.ensure_bridge_start_allowed()

            # Build command
            # Use sys.executable to ensure we use the same Python interpreter (and venv)
            cmd = [
                sys.executable,
                self._config.get("script", "robot_services/audio/audio_bridge.py")
            ]

            # Add device selections
            default_mic = self._config.get("default_microphone")
            if default_mic is not None:
                cmd.extend(["--input-device", str(default_mic)])

            input_capture_channels = self._config.get("input_capture_channels")
            if input_capture_channels is not None:
                cmd.extend(["--input-capture-channels", str(input_capture_channels)])

            for input_mix_channel in self._input_mix_channels():
                cmd.extend(["--input-mix-channel", str(input_mix_channel)])

            default_speaker = self._config.get("default_speakers")
            if default_speaker is not None:
                cmd.extend(["--output-device", str(default_speaker)])

            cmd.extend(["--control-port", str(self._get_control_port())])

            if bool(self._config.get("enable_rnnoise", False)):
                cmd.append("--enable-rnnoise")

            if bool(self._config.get("suppress_input_during_playback", False)):
                cmd.append("--suppress-input-during-playback")

            playback_echo_tail_ms = self._config.get("playback_echo_tail_ms")
            if playback_echo_tail_ms is not None:
                cmd.extend(["--playback-echo-tail-ms", str(playback_echo_tail_ms)])

            mic_track_name = self._config.get("mic_track_name")
            if mic_track_name is not None:
                cmd.extend(["--mic-track-name", str(mic_track_name)])

            # Ensure log directory exists
            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)

            # Copy environment to pass LiveKit credentials
            env = os.environ.copy()

            # Start process
            self._log_handle = open(self._log_file, 'a')
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=self._config.get("working_dir", ".")
            )

            # Give it a moment to fail if device is invalid
            await asyncio.sleep(2)

            if self._process.poll() is not None:
                self._state = ServiceState.FAILED
                self._last_error = "Process exited immediately after start"
                raise RuntimeError(self._last_error)

            self._state = ServiceState.RUNNING
            self._mark_started()

        except Exception as e:
            self._state = ServiceState.FAILED
            self._last_error = str(e)
            raise

    async def stop(self) -> None:
        """Stop the audio bridge process."""
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

    async def check_health(self) -> bool:
        """Check if process is alive."""
        return self._process is not None and self._process.poll() is None

    def get_log_path(self) -> str:
        """Return path to log file."""
        return self._log_file

    def get_dependencies(self) -> List[str]:
        """Audio bridge requires LiveKit server to post to tracks."""
        return ["livekit"]

    def get_config_parameters(self) -> List[ConfigParameter]:
        """Return configurable parameters."""
        return [
            ConfigParameter(
                key="default_microphone",
                value=self._config.get("default_microphone"),
                type="string",
                description="Default audio input PortAudio index/name or Linux ALSA id",
                required=False
            ),
            ConfigParameter(
                key="default_speakers",
                value=self._config.get("default_speakers"),
                type="string",
                description="Default audio output PortAudio index/name or Linux ALSA id",
                required=False
            ),
            ConfigParameter(
                key="input_capture_channels",
                value=self._config.get("input_capture_channels"),
                type="int",
                description="Number of input device channels to open before mono mixing",
                required=False
            ),
            ConfigParameter(
                key="input_mix_channels",
                value=self._config.get("input_mix_channels"),
                type="list",
                description="Zero-based captured input channels to average into mono",
                required=False
            ),
            ConfigParameter(
                key="control_port",
                value=self._get_control_port(),
                type="int",
                description="Localhost control API port for mute/status actions",
                required=False
            ),
            ConfigParameter(
                key="enable_rnnoise",
                value=bool(self._config.get("enable_rnnoise", False)),
                type="bool",
                description="Enable RNNoise denoising before publishing microphone audio",
                required=False
            ),
            ConfigParameter(
                key="suppress_input_during_playback",
                value=bool(self._config.get("suppress_input_during_playback", False)),
                type="bool",
                description="Suppress microphone publication while robot speaker audio can echo",
                required=False,
            ),
            ConfigParameter(
                key="playback_echo_tail_ms",
                value=int(self._config.get("playback_echo_tail_ms", 500)),
                type="int",
                description="Post-playback microphone suppression tail in milliseconds",
                required=False,
            ),
            ConfigParameter(
                key="mic_track_name",
                value=self._config.get("mic_track_name", "g1-mic"),
                type="string",
                description="LiveKit track name used when publishing the microphone",
                required=False
            )
        ]

    async def update_config(self, updates: Dict[str, Any]) -> None:
        """
        Update audio bridge configuration.
        Automatically restart service if running to apply device changes.
        """
        # Update config
        self._config.update(updates)

        # If service is running, restart to apply device changes
        if self._state == ServiceState.RUNNING:
            logger.info(f"Audio bridge config changed, restarting service...")
            await self.restart()
