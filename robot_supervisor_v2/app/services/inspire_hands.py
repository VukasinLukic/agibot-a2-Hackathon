"""
Inspire hands service implementation.
Manages the headless Inspire SDK hand driver using the global system Python.
"""

import asyncio
import os
import signal
import subprocess
from typing import Any, Dict, List

from .base import BaseService, ConfigParameter, ServiceState, ServiceStatus


class InspireHandsService(BaseService):
    """Headless Inspire hands driver."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "Inspire Hands"),
            config=config,
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None

    def _sync_process_state(self) -> None:
        """Reconcile service state with the actual child process state."""
        if self._state in (ServiceState.STOPPED, ServiceState.STOPPING):
            return

        if not self._process:
            if self._state in (ServiceState.RUNNING, ServiceState.STARTING):
                self._state = ServiceState.FAILED
                self._last_error = "Process handle missing while service is expected to be active"
            return

        return_code = self._process.poll()
        if return_code is None:
            return

        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

        self._start_time = None
        if return_code == 0:
            self._state = ServiceState.STOPPED
            self._last_error = None
        else:
            self._state = ServiceState.FAILED
            self._last_error = f"Process exited with code {return_code}"

    async def start(self) -> None:
        """Start the Inspire hands process."""
        if self._state not in (ServiceState.STOPPED, ServiceState.FAILED):
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            python_path = self._config.get("python_path", "/usr/bin/python")
            script = self._config.get(
                "script",
                "inspire_hand_sdk/example/Headless_driver_double.py",
            )
            working_dir = self._config.get("working_dir", "/home/unitree/inspire_hand_ws")

            cmd = [python_path, script]

            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
            self._log_handle = open(self._log_file, "a")
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                cwd=working_dir,
                env=os.environ.copy(),
                start_new_session=True,
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
        """Stop the Inspire hands process group."""
        if self._state == ServiceState.STOPPED:
            return

        self._state = ServiceState.STOPPING

        if self._process:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self._process.wait()
            self._process = None

        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

        self._state = ServiceState.STOPPED
        self._start_time = None

    async def check_health(self) -> bool:
        """Check if the Inspire hands process is alive."""
        self._sync_process_state()
        return self._state == ServiceState.RUNNING

    def get_status(self) -> ServiceStatus:
        """Get current service status after reconciling child process state."""
        self._sync_process_state()
        return super().get_status()

    def get_log_path(self) -> str:
        """Return path to log file."""
        return self._log_file

    def get_config_parameters(self) -> List[ConfigParameter]:
        """Return configurable parameters."""
        return [
            ConfigParameter(
                key="python_path",
                value=self._config.get("python_path", "/usr/bin/python"),
                type="string",
                description="Global Python interpreter used for the Inspire hand driver",
                required=True,
            ),
            ConfigParameter(
                key="working_dir",
                value=self._config.get("working_dir", "/home/unitree/inspire_hand_ws"),
                type="string",
                description="Working directory for the Inspire hand driver",
                required=True,
            ),
            ConfigParameter(
                key="script",
                value=self._config.get(
                    "script",
                    "inspire_hand_sdk/example/Headless_driver_double.py",
                ),
                type="string",
                description="Headless Inspire hand driver script",
                required=True,
            ),
        ]
