"""
Remote Audio Bridge supervisor service.

This service preserves the supervisor-facing audio bridge contract while
delegating lifecycle and device discovery to the PC3 audio bridge manager API.
It does not open devices, connect to LiveKit, or shell into PC3.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List
from urllib.parse import urljoin

from .base import BaseService, ConfigParameter, Device, DeviceType, ServiceState


logger = logging.getLogger(__name__)


class AudioBridgeRemoteService(BaseService):
    """Supervisor wrapper for the PC3 audio bridge manager."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "Audio Bridge"),
            config=config,
        )
        self._last_status_payload: dict[str, Any] | None = None

    def _manager_url(self) -> str:
        url = (
            self._config.get("manager_url")
            or self._config.get("control_url")
            or os.getenv("AUDIO_BRIDGE_MANAGER_URL")
        )
        if not url:
            raise RuntimeError(
                "Remote audio bridge requires manager_url config or AUDIO_BRIDGE_MANAGER_URL"
            )
        return str(url).rstrip("/") + "/"

    def _manager_token(self) -> str:
        token = self._config.get("manager_token")
        if token:
            return str(token)

        env_name = str(self._config.get("manager_token_env") or "AUDIO_BRIDGE_MANAGER_TOKEN")
        token = os.getenv(env_name)
        if token:
            return token

        raise RuntimeError(
            f"Remote audio bridge requires manager_token config or {env_name} environment variable"
        )

    def _manager_request(
        self,
        method: str,
        path: str,
        payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        data = None
        headers = {"Authorization": f"Bearer {self._manager_token()}"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        url = urljoin(self._manager_url(), path.lstrip("/"))
        request = urllib.request.Request(url, data=data, method=method, headers=headers)

        try:
            with urllib.request.urlopen(
                request,
                timeout=float(self._config.get("request_timeout_seconds", 3.0)),
            ) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail = body
            try:
                parsed = json.loads(body)
                detail = str(parsed.get("detail", body))
            except json.JSONDecodeError:
                pass
            raise RuntimeError(f"Remote audio bridge manager returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Remote audio bridge manager request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Remote audio bridge manager returned invalid JSON: {exc}") from exc

    def _normalize_devices(self, devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return manager-reported devices without supervisor-side fabrication."""
        normalized: List[Dict[str, Any]] = []
        for raw_device in devices:
            device = dict(raw_device)
            device.setdefault("connected", True)
            normalized.append(device)
        return normalized

    def _build_audio_device_payload(self, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        return {
            "input_devices": self._normalize_devices(device_info.get("input_devices", [])),
            "output_devices": self._normalize_devices(device_info.get("output_devices", [])),
        }

    def _query_devices_from_manager(self) -> Dict[str, Any]:
        return self._manager_request("GET", "/devices")

    def get_audio_devices(self) -> Dict[str, List[Dict[str, Any]]]:
        try:
            return self._build_audio_device_payload(self._query_devices_from_manager())
        except Exception as exc:
            logger.error("Failed to query remote audio devices: %s", exc)
            return {"input_devices": [], "output_devices": []}

    def get_input_devices(self) -> List[Dict[str, Any]]:
        return self.get_audio_devices()["input_devices"]

    def get_output_devices(self) -> List[Dict[str, Any]]:
        return self.get_audio_devices()["output_devices"]

    async def list_devices(self) -> List[Device]:
        devices = []
        device_info = self.get_audio_devices()

        for dev in device_info.get("input_devices", []):
            devices.append(
                Device(
                    id=f"input_{dev['index']}",
                    name=dev["name"],
                    type=DeviceType.AUDIO_INPUT,
                    is_default=dev.get("is_default", False),
                    metadata={
                        "index": dev["index"],
                        "channels": dev["channels"],
                        "samplerate": dev["sample_rate"],
                        "hostapi": dev.get("hostapi", "Unknown"),
                        "connected": dev.get("connected", True),
                        "alsa_device": dev.get("alsa_device"),
                        "alsa_card_id": dev.get("alsa_card_id"),
                        "alsa_device_index": dev.get("alsa_device_index"),
                        "id_path": dev.get("id_path"),
                        "id_path_tag": dev.get("id_path_tag"),
                    },
                )
            )

        for dev in device_info.get("output_devices", []):
            devices.append(
                Device(
                    id=f"output_{dev['index']}",
                    name=dev["name"],
                    type=DeviceType.AUDIO_OUTPUT,
                    is_default=dev.get("is_default", False),
                    metadata={
                        "index": dev["index"],
                        "channels": dev["channels"],
                        "samplerate": dev["sample_rate"],
                        "hostapi": dev.get("hostapi", "Unknown"),
                        "connected": dev.get("connected", True),
                        "alsa_device": dev.get("alsa_device"),
                        "alsa_card_id": dev.get("alsa_card_id"),
                        "alsa_device_index": dev.get("alsa_device_index"),
                        "id_path": dev.get("id_path"),
                        "id_path_tag": dev.get("id_path_tag"),
                    },
                )
            )

        return devices

    def _default_output_devices(self) -> list[str | int]:
        configured = self._config.get("default_speakers")
        if configured is None:
            configured = self._config.get("output_devices")
        if configured is None:
            return []
        if isinstance(configured, list):
            return configured
        return [configured]

    def _input_mix_channels(self) -> list[Any]:
        configured = self._config.get("input_mix_channels")
        if configured is None:
            return []
        if isinstance(configured, list):
            return configured
        if isinstance(configured, tuple):
            return list(configured)
        return [configured]

    def _start_payload(self) -> dict[str, Any]:
        return {
            "input_device": self._config.get("default_microphone"),
            "output_devices": self._default_output_devices(),
            "input_capture_channels": self._config.get("input_capture_channels"),
            "input_mix_channels": self._input_mix_channels(),
            "enable_aec": bool(self._config.get("enable_aec", True)),
            "enable_rnnoise": bool(self._config.get("enable_rnnoise", False)),
        }

    def _child_status_from_manager(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        child = payload.get("child")
        if not isinstance(child, dict):
            return {}
        status_payload = child.get("status")
        if isinstance(status_payload, dict):
            return status_payload
        return {"running": bool(child.get("running"))}

    def _child_reports_ready(self, child: Dict[str, Any]) -> bool:
        if child.get("running") is not True:
            return False
        status_payload = child.get("status")
        if isinstance(status_payload, dict) and "ready" in status_payload:
            return status_payload.get("ready") is True
        return True

    def _last_child_running(self) -> bool:
        if not isinstance(self._last_status_payload, dict):
            return False
        child = self._last_status_payload.get("child")
        return isinstance(child, dict) and child.get("running") is True

    async def start(self) -> None:
        """Start the PC3 child bridge through the remote manager."""
        if self._state != ServiceState.STOPPED:
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            payload = await asyncio.to_thread(self._manager_request, "POST", "/start", self._start_payload())
            child = payload.get("child") if isinstance(payload, dict) else None
            if not isinstance(child, dict) or not self._child_reports_ready(child):
                raise RuntimeError("Remote audio bridge manager did not report a ready child")
            self._last_status_payload = payload
            self._state = ServiceState.RUNNING
            self._mark_started()
        except Exception as exc:
            self._state = ServiceState.FAILED
            self._last_error = str(exc)
            raise

    async def stop(self) -> None:
        """Stop the PC3 child bridge through the remote manager."""
        if self._state == ServiceState.STOPPED:
            return
        if self._state == ServiceState.FAILED and not self._last_child_running():
            self._state = ServiceState.STOPPED
            self._start_time = None
            return

        self._state = ServiceState.STOPPING
        try:
            self._last_status_payload = await asyncio.to_thread(
                self._manager_request,
                "POST",
                "/stop",
                None,
            )
        except Exception as exc:
            self._state = ServiceState.FAILED
            self._last_error = str(exc)
            raise

        self._state = ServiceState.STOPPED
        self._start_time = None

    async def check_health(self) -> bool:
        if self._state != ServiceState.RUNNING:
            return False
        try:
            payload = await asyncio.to_thread(self._manager_request, "GET", "/status")
        except Exception as exc:
            self._last_error = str(exc)
            return False
        self._last_status_payload = payload
        child = payload.get("child") if isinstance(payload, dict) else None
        return isinstance(child, dict) and self._child_reports_ready(child)

    def get_log_path(self) -> None:
        """Remote bridge child logs live on PC3 under manager configuration."""
        return None

    async def tail_logs(self, lines: int = 100) -> str:
        lines = max(1, min(int(lines), 2000))
        payload = await asyncio.to_thread(self._manager_request, "GET", f"/logs?lines={lines}")
        return str(payload.get("content", ""))

    def get_backend_type(self) -> str:
        return "remote-manager"

    def get_current_mode(self) -> str:
        return "remote"

    def get_available_modes(self) -> List[str]:
        return ["remote"]

    def get_dependencies(self) -> List[str]:
        """Remote audio still needs the local LiveKit server before publishing."""
        return ["livekit"]

    async def get_bridge_status(self) -> Dict[str, Any]:
        if self._state != ServiceState.RUNNING:
            return {
                "running": False,
                "muted": False,
                "input_mic_gain_db": 0.0,
                "input_mic_gain_db_min": -40.0,
                "input_mic_gain_db_max": 12.0,
                "remote": True,
            }

        try:
            payload = await asyncio.to_thread(self._manager_request, "GET", "/status")
            self._last_status_payload = payload
            child_status = self._child_status_from_manager(payload)
            return {
                "running": True,
                "muted": False,
                "input_mic_gain_db": 0.0,
                "input_mic_gain_db_min": -40.0,
                "input_mic_gain_db_max": 12.0,
                "remote": True,
                **child_status,
            }
        except Exception as exc:
            logger.warning("Failed to query remote audio bridge status: %s", exc)
            return {
                "running": True,
                "muted": False,
                "control_available": False,
                "input_mic_gain_db": 0.0,
                "input_mic_gain_db_min": -40.0,
                "input_mic_gain_db_max": 12.0,
                "remote": True,
                "error": str(exc),
            }

    def _require_running(self) -> None:
        if self._state != ServiceState.RUNNING:
            raise RuntimeError("Audio bridge is not running")

    async def set_muted(self, muted: bool) -> Dict[str, Any]:
        self._require_running()
        return await asyncio.to_thread(
            self._manager_request,
            "POST",
            "/mute",
            {"muted": muted},
        )

    async def set_input_mic_gain_db(self, gain_db: float) -> Dict[str, Any]:
        self._require_running()
        return await asyncio.to_thread(
            self._manager_request,
            "POST",
            "/input-gain",
            {"input_mic_gain_db": gain_db},
        )

    async def set_output_speaker_gain_db(self, gain_db: float) -> Dict[str, Any]:
        self._require_running()
        return await asyncio.to_thread(
            self._manager_request,
            "POST",
            "/output-gain",
            {"output_speaker_gain_db": gain_db},
        )

    async def release_remote_playback(self) -> Dict[str, Any]:
        self._require_running()
        return await asyncio.to_thread(
            self._manager_request,
            "POST",
            "/remote-playback/release",
            {},
        )

    async def get_aima_status(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self._manager_request, "GET", "/aima/status")

    async def set_aima_audio_bridge_mode(self) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._manager_request,
            "POST",
            "/aima/mode/audio-bridge",
            {},
        )

    async def set_aima_agibot_mode(self) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._manager_request,
            "POST",
            "/aima/mode/agibot",
            {},
        )

    async def run_aima_doctor(self) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._manager_request,
            "POST",
            "/aima/doctor",
            {},
        )

    def get_config_parameters(self) -> List[ConfigParameter]:
        return [
            ConfigParameter(
                key="default_microphone",
                value=self._config.get("default_microphone"),
                type="string",
                description="Remote bridge input PortAudio index/name or Linux ALSA id",
                required=False,
            ),
            ConfigParameter(
                key="default_speakers",
                value=self._config.get("default_speakers"),
                type="string",
                description="Remote bridge output PortAudio index/name or Linux ALSA id",
                required=False,
            ),
            ConfigParameter(
                key="input_capture_channels",
                value=self._config.get("input_capture_channels"),
                type="int",
                description="Remote bridge input channels to open before mono mixing",
                required=False,
            ),
            ConfigParameter(
                key="input_mix_channels",
                value=self._config.get("input_mix_channels"),
                type="list",
                description="Zero-based captured input channels to average into mono",
                required=False,
            ),
            ConfigParameter(
                key="manager_url",
                value=self._config.get("manager_url"),
                type="string",
                description="PC3 audio bridge manager URL",
                required=True,
            ),
            ConfigParameter(
                key="enable_rnnoise",
                value=bool(self._config.get("enable_rnnoise", False)),
                type="bool",
                description="Enable RNNoise denoising in the remote bridge child",
                required=False,
            ),
        ]

    def get_config(self) -> Dict[str, Any]:
        config = super().get_config()
        if config.get("manager_token"):
            config["manager_token"] = "<redacted>"
        return config

    async def update_config(self, updates: Dict[str, Any]) -> None:
        self._config.update(updates)
        if self._state == ServiceState.RUNNING:
            logger.info("Remote audio bridge config changed, restarting service...")
            await self.restart()

    def get_status(self):
        status = super().get_status()
        if status.state == ServiceState.RUNNING and self._last_status_payload:
            child = self._last_status_payload.get("child")
            if isinstance(child, dict) and child.get("pid") is not None:
                status.pid = child.get("pid")
        if status.state == ServiceState.RUNNING and self._start_time:
            status.uptime_seconds = time.monotonic() - self._start_time
        return status
