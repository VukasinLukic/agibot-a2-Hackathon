"""
Teleimager server service implementation.
Manages the standalone teleimager image server using the teleimager conda environment.
"""

import asyncio
import os
import signal
import subprocess
from typing import Any, Dict, List

from .base import BaseService, ConfigParameter, ServiceState, ServiceStatus


class TeleimagerServerService(BaseService):
    """Standalone teleimager image server."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "Teleimager Server"),
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
        """Start the teleimager image server process."""
        if self._state not in (ServiceState.STOPPED, ServiceState.FAILED):
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            python_path = self._config.get(
                "python_path",
                "/home/unitree/miniconda3/envs/teleimager/bin/python",
            )
            module = self._config.get("module", "teleimager.image_server")
            working_dir = self._config.get("working_dir", "/home/unitree/teleimager/src")

            cmd = [python_path, "-m", module]

            if self._config.get("realsense", True):
                cmd.append("--rs")

            solo_camera = self._config.get("solo_camera")
            if solo_camera:
                cmd.extend(["--solo-camera", str(solo_camera)])

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
        """Stop the teleimager image server process."""
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
        """Check if the teleimager process is alive."""
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
                value=self._config.get(
                    "python_path",
                    "/home/unitree/miniconda3/envs/teleimager/bin/python",
                ),
                type="string",
                description="Python interpreter used to run teleimager server",
                required=True,
            ),
            ConfigParameter(
                key="working_dir",
                value=self._config.get("working_dir", "/home/unitree/teleimager/src"),
                type="string",
                description="Working directory for teleimager server",
                required=True,
            ),
            ConfigParameter(
                key="solo_camera",
                value=self._config.get("solo_camera", "additional"),
                type="choice",
                choices=["builtin", "additional"],
                description="Camera source to expose as head_camera",
                required=False,
            ),
        ]
