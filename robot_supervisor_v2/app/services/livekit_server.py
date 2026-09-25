"""
LiveKit Server service implementation.
Manages the LiveKit media server process in non-dev mode.
"""

import asyncio
import os
import subprocess
from typing import Any, Dict, List

from .base import BaseService, ConfigParameter, ServiceState


class LiveKitServerService(BaseService):
    """LiveKit server process management."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "LiveKit Server"),
            config=config,
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None

    def _get_bind(self) -> str:
        return str(self._config.get("bind", "0.0.0.0"))

    def _get_keys_arg(self) -> str:
        api_key = os.getenv("LIVEKIT_API_KEY")
        api_secret = os.getenv("LIVEKIT_API_SECRET")

        if not api_key or not api_secret:
            raise RuntimeError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set in the environment")

        return f"{api_key}: {api_secret}"

    async def start(self) -> None:
        """Start the LiveKit server process."""
        if self._state != ServiceState.STOPPED:
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            cmd = [
                "livekit-server",
                "--bind",
                self._get_bind(),
                "--keys",
                self._get_keys_arg(),
            ]

            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)

            env = os.environ.copy()
            self._log_handle = open(self._log_file, "a")
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                env=env,
            )

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
        """Stop the LiveKit server process."""
        if self._state == ServiceState.STOPPED:
            return

        self._state = ServiceState.STOPPING

        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
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

    def get_config_parameters(self) -> List[ConfigParameter]:
        """Return configurable parameters."""
        return [
            ConfigParameter(
                key="bind",
                value=self._config.get("bind", "0.0.0.0"),
                type="string",
                description="Interface/address for LiveKit to listen on",
                required=False,
            )
        ]
