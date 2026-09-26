"""
IGRA (eldorado) supervisor service.

Starts eldorado/igra_api.py as a subprocess and health-checks /health.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

import aiohttp

from .base import BaseService, ConfigParameter, HealthCheckConfig, ServiceState

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8105
DEFAULT_SCRIPT = "eldorado/igra_api.py"


class IgraService(BaseService):
    """Hackathon IGRA game / hand-gesture detection service."""

    def __init__(self, name: str, config: Dict[str, Any]):
        service_config = dict(config)
        service_config.setdefault("port", DEFAULT_PORT)
        service_config.setdefault("script", DEFAULT_SCRIPT)
        service_config.setdefault("working_dir", ".")
        service_config.setdefault("optional", True)
        service_config.setdefault("manual_only", False)
        service_config.setdefault("startup_timeout", 20)
        service_config.setdefault("show_window", False)
        service_config.setdefault("announce_text", "RADI")
        service_config.setdefault("announce_mode", "both")
        service_config.setdefault("camera", "CHEST_RIGHT_FISHEYE")
        super().__init__(
            name=name,
            display_name=service_config.get("display_name", "IGRA"),
            config=service_config,
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None

    def _port(self) -> int:
        return int(self._config.get("port", DEFAULT_PORT))

    def _base_url(self) -> str:
        return f"http://127.0.0.1:{self._port()}"

    async def start(self) -> None:
        if self._state not in (ServiceState.STOPPED, ServiceState.FAILED):
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            script = self._config.get("script", DEFAULT_SCRIPT)
            cmd = [sys.executable, script, "--host", "0.0.0.0", "--port", str(self._port())]

            env = os.environ.copy()
            env["IGRA_PORT"] = str(self._port())
            env["IGRA_SHOW_WINDOW"] = "1" if self._config.get("show_window") else "0"
            env["IGRA_ANNOUNCE_TEXT"] = str(self._config.get("announce_text", "RADI"))
            env["IGRA_ANNOUNCE_MODE"] = str(self._config.get("announce_mode", "both"))
            env["IGRA_AUTO_START_DETECT"] = "1"
            if self._config.get("camera") is not None:
                env["IGRA_CAMERA"] = str(self._config.get("camera"))
            if self._config.get("gesture_db"):
                env["IGRA_GESTURE_DB"] = str(self._config.get("gesture_db"))
            if self._config.get("leaderboard_db"):
                env["IGRA_LEADERBOARD_DB"] = str(self._config.get("leaderboard_db"))
            if self._config.get("python_path"):
                cmd[0] = str(self._config.get("python_path"))

            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
            self._log_handle = open(self._log_file, "a")
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                cwd=self._config.get("working_dir", "."),
                env=env,
            )

            timeout = float(self._config.get("startup_timeout", 20))
            if await self.wait_for_ready(timeout=timeout):
                self._state = ServiceState.RUNNING
                self._mark_started()
            else:
                self._state = ServiceState.FAILED
                self._last_error = f"Failed health check after {timeout}s"
                await self.stop()
                raise RuntimeError(self._last_error)
        except Exception as exc:
            self._state = ServiceState.FAILED
            self._last_error = str(exc)
            logger.exception("Failed to start IGRA service")
            raise

    async def stop(self) -> None:
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
        if not self._process or self._process.poll() is not None:
            return False
        url = f"{self._base_url()}/health"
        try:
            timeout = aiohttp.ClientTimeout(total=float(self._config.get("health_timeout", 2.0)))
            connector = aiohttp.TCPConnector(force_close=True)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=timeout) as resp:
                    return resp.status == 200
        except Exception:
            return False

    def get_health_config(self) -> HealthCheckConfig:
        return HealthCheckConfig(
            type="http",
            endpoint=f"{self._base_url()}/health",
            timeout_seconds=float(self._config.get("health_timeout", 2.0)),
            interval_seconds=float(self._config.get("health_interval", 5.0)),
        )

    def get_log_path(self) -> str:
        return self._log_file

    async def fetch_json(self, path: str) -> Optional[Dict[str, Any]]:
        if self._state != ServiceState.RUNNING:
            return None
        url = f"{self._base_url()}{path}"
        try:
            timeout = aiohttp.ClientTimeout(total=2.0)
            connector = aiohttp.TCPConnector(force_close=True)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.json()
        except Exception as exc:
            logger.debug("IGRA fetch %s failed: %s", path, exc)
            return None

    def get_config_parameters(self) -> List[ConfigParameter]:
        return [
            ConfigParameter(
                key="port",
                value=self._port(),
                type="int",
                description="HTTP port for IGRA API",
                required=True,
            ),
            ConfigParameter(
                key="show_window",
                value=bool(self._config.get("show_window", False)),
                type="bool",
                description="Open OpenCV preview window",
                required=False,
            ),
            ConfigParameter(
                key="announce_text",
                value=str(self._config.get("announce_text", "RADI")),
                type="string",
                description="Phrase spoken on stable gesture detect",
                required=False,
            ),
            ConfigParameter(
                key="announce_mode",
                value=str(self._config.get("announce_mode", "both")),
                type="choice",
                choices=["local", "agent", "both"],
                description="How to speak on detect",
                required=False,
            ),
            ConfigParameter(
                key="camera",
                value=self._config.get("camera", "CHEST_RIGHT_FISHEYE"),
                type="string",
                description="Camera: CHEST_RIGHT_FISHEYE / 'Chest Right Fisheye' / /dev/videoX / index",
                required=False,
            ),
        ]

    async def update_config(self, updates: Dict[str, Any]) -> None:
        self._config.update(updates)
        if self._state == ServiceState.RUNNING:
            logger.info("IGRA config changed, restarting service...")
            await self.restart()
