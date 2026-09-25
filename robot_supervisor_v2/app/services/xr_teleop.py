"""
XR teleoperation service implementation.
Manages the main teleoperation process in IPC mode using the teleimager conda environment.
"""

import asyncio
import json
import os
import signal
import subprocess
import time
from typing import Any, Dict, List

from .base import BaseService, ConfigParameter, ServiceState, ServiceStatus


class XRTeleopService(BaseService):
    """Main XR teleoperation process."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "XR Teleoperation"),
            config=config,
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None
        self._ipc_online = False
        self._ipc_last_state: Dict[str, Any] = {}
        self._ipc_last_check = 0.0
        self._ipc_check_interval = float(config.get("ipc_check_interval", 2.0))

    def get_dependencies(self) -> List[str]:
        """Teleop depends on image streaming and Inspire hand tracking."""
        return ["teleimager-server", "inspire-hands"]

    def _probe_ipc_heartbeat(self, probe_timeout: float | None = None) -> bool:
        """Probe teleop IPC heartbeat using the teleimager environment's pyzmq."""
        python_path = self._config.get(
            "python_path",
            "/home/unitree/miniconda3/envs/teleimager/bin/python",
        )
        ipc_socket = self._config.get("ipc_hb_socket", "ipc://@xr_teleoperate_hb.ipc")
        if probe_timeout is None:
            probe_timeout = float(self._config.get("ipc_probe_timeout", 1.2))
        else:
            probe_timeout = float(probe_timeout)

        probe_script = (
            "import json, sys, time, zmq\n"
            f"sock_addr = {ipc_socket!r}\n"
            f"deadline = time.time() + {probe_timeout!r}\n"
            "ctx = zmq.Context.instance()\n"
            "sock = ctx.socket(zmq.SUB)\n"
            "sock.setsockopt(zmq.RCVHWM, 1)\n"
            "sock.connect(sock_addr)\n"
            "sock.setsockopt_string(zmq.SUBSCRIBE, '')\n"
            "count = 0\n"
            "latest = {}\n"
            "try:\n"
            "    while time.time() < deadline:\n"
            "        try:\n"
            "            latest = sock.recv_json(flags=zmq.NOBLOCK)\n"
            "            count += 1\n"
            "            if count >= 3:\n"
            "                break\n"
            "        except zmq.Again:\n"
            "            time.sleep(0.05)\n"
            "finally:\n"
            "    sock.close(0)\n"
            "    ctx.term()\n"
            "print(json.dumps({'online': count >= 3, 'state': latest}))\n"
        )

        result = subprocess.run(
            [python_path, "-c", probe_script],
            capture_output=True,
            text=True,
            timeout=max(2.0, probe_timeout + 1.0),
        )
        if result.returncode != 0:
            self._ipc_last_state = {}
            return False

        try:
            payload = json.loads(result.stdout.strip() or "{}")
        except json.JSONDecodeError:
            self._ipc_last_state = {}
            return False

        self._ipc_last_state = payload.get("state") or {}
        return bool(payload.get("online"))

    def _send_ipc_command(self, command: str) -> Dict[str, Any]:
        """Send a teleop IPC command using the teleimager environment's pyzmq."""
        python_path = self._config.get(
            "python_path",
            "/home/unitree/miniconda3/envs/teleimager/bin/python",
        )
        ipc_req_socket = self._config.get("ipc_req_socket", "ipc://@xr_teleoperate_data.ipc")
        command_timeout_ms = int(self._config.get("ipc_command_timeout_ms", 1500))
        command_script = (
            "import json, uuid, zmq\n"
            f"sock_addr = {ipc_req_socket!r}\n"
            f"timeout_ms = {command_timeout_ms!r}\n"
            f"cmd = {command!r}\n"
            "ctx = zmq.Context.instance()\n"
            "sock = ctx.socket(zmq.REQ)\n"
            "sock.connect(sock_addr)\n"
            "msg = {'reqid': str(uuid.uuid4()), 'cmd': cmd}\n"
            "try:\n"
            "    sock.send_json(msg)\n"
            "    if sock.poll(timeout_ms):\n"
            "        reply = sock.recv_json()\n"
            "    else:\n"
            "        reply = {'status': 'error', 'msg': 'timeout waiting for server reply'}\n"
            "finally:\n"
            "    sock.close(0)\n"
            "    ctx.term()\n"
            "print(json.dumps(reply))\n"
        )

        result = subprocess.run(
            [python_path, "-c", command_script],
            capture_output=True,
            text=True,
            timeout=max(3.0, command_timeout_ms / 1000.0 + 1.0),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "IPC command execution failed")

        try:
            return json.loads(result.stdout.strip() or "{}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to decode IPC command reply: {e}") from e

    def _sync_process_state(self) -> None:
        """Reconcile service state with child process and IPC heartbeat state."""
        if self._state in (ServiceState.STOPPED, ServiceState.STOPPING):
            return

        if not self._process:
            if self._state in (ServiceState.RUNNING, ServiceState.STARTING):
                self._state = ServiceState.FAILED
                self._last_error = "Process handle missing while service is expected to be active"
            return

        return_code = self._process.poll()
        if return_code is not None:
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None

            self._start_time = None
            self._ipc_online = False
            self._ipc_last_state = {}
            if return_code == 0:
                self._state = ServiceState.STOPPED
                self._last_error = None
            else:
                self._state = ServiceState.FAILED
                self._last_error = f"Process exited with code {return_code}"
            return

        now = time.time()
        if now - self._ipc_last_check >= self._ipc_check_interval:
            self._ipc_last_check = now
            try:
                self._ipc_online = self._probe_ipc_heartbeat()
            except Exception as e:
                self._ipc_online = False
                self._ipc_last_state = {}
                self._last_error = f"IPC heartbeat probe failed: {e}"

        if self._ipc_online:
            self._state = ServiceState.RUNNING
            if self._last_error and self._last_error.startswith("IPC heartbeat"):
                self._last_error = None
        else:
            self._state = ServiceState.FAILED
            self._last_error = "IPC heartbeat unavailable"

    async def start(self) -> None:
        """Start the XR teleoperation process in IPC mode."""
        if self._state not in (ServiceState.STOPPED, ServiceState.FAILED):
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None
        self._ipc_online = False
        self._ipc_last_state = {}
        self._ipc_last_check = 0.0

        try:
            python_path = self._config.get(
                "python_path",
                "/home/unitree/miniconda3/envs/teleimager/bin/python",
            )
            script = self._config.get("script", "teleop_hand_and_arm.py")
            working_dir = self._config.get("working_dir", "/home/unitree/xr_teleoperate/teleop")

            cmd = [
                python_path,
                script,
                "--arm",
                self._config.get("arm", "G1_29"),
                "--img-server-ip",
                self._config.get("img_server_ip", "127.0.0.1"),
                "--network-interface",
                self._config.get("network_interface", "eth0"),
                "--ee",
                self._config.get("ee", "inspire_ftp"),
                "--ipc",
                "--motion"
            ]

            #if self._config.get("motion", True):
            #    cmd.append("--motion")
            #if self._config.get("headless", False):
            #    cmd.append("--headless")
            #if self._config.get("sim", False):
            #    cmd.append("--sim")
            #if self._config.get("record", False):
            #    cmd.append("--record")

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

            startup_timeout = float(self._config.get("ipc_startup_timeout", 7.0))
            startup_poll_interval = float(self._config.get("ipc_startup_poll_interval", 0.25))
            startup_probe_timeout = float(self._config.get("ipc_startup_probe_timeout", 0.6))

            deadline = time.monotonic() + startup_timeout

            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    self._state = ServiceState.FAILED
                    self._last_error = "Process exited during startup"
                    raise RuntimeError(self._last_error)

                self._ipc_online = self._probe_ipc_heartbeat(probe_timeout=startup_probe_timeout)
                self._ipc_last_check = time.time()

                if self._ipc_online:
                    break

                await asyncio.sleep(startup_poll_interval)

            if not self._ipc_online:
                self._state = ServiceState.FAILED
                self._last_error = f"IPC heartbeat unavailable after {startup_timeout:.1f}s startup timeout"
                raise RuntimeError(self._last_error)


            self._state = ServiceState.RUNNING
            self._mark_started()
        except Exception as e:
            if self._process and self._process.poll() is None:
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
            self._state = ServiceState.FAILED
            self._last_error = str(e)
            raise

    async def stop(self) -> None:
        """Stop the XR teleoperation process group."""
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

        self._ipc_online = False
        self._ipc_last_state = {}
        self._state = ServiceState.STOPPED
        self._start_time = None
        self._last_error = None

    async def check_health(self) -> bool:
        """Check if the teleop process is alive and responding over IPC heartbeat."""
        self._sync_process_state()
        return self._state == ServiceState.RUNNING and self._ipc_online

    def get_status(self) -> ServiceStatus:
        """Get current service status after reconciling process and IPC heartbeat."""
        self._sync_process_state()
        return super().get_status()

    async def start_tracking(self) -> Dict[str, Any]:
        """Equivalent to pressing 'r' in teleop."""
        self._sync_process_state()
        if self._state != ServiceState.RUNNING or not self._ipc_online:
            raise RuntimeError("XR teleop service is not running or IPC heartbeat is offline")

        reply = self._send_ipc_command("CMD_START")
        self._sync_process_state()
        return {
            "reply": reply,
            "heartbeat": self._ipc_last_state,
        }

    async def stop_tracking(self) -> Dict[str, Any]:
        """Equivalent to pressing 'q' in teleop; teleop should then exit cleanly."""
        self._sync_process_state()
        if self._state != ServiceState.RUNNING or not self._ipc_online:
            raise RuntimeError("XR teleop service is not running or IPC heartbeat is offline")

        reply = self._send_ipc_command("CMD_STOP")
        # Give teleop a brief moment to exit and update its heartbeat/process state.
        await asyncio.sleep(2.5)
        self._sync_process_state()
        return {
            "reply": reply,
            "heartbeat": self._ipc_last_state,
            "service_state": self._state,
        }

    def get_runtime_state(self) -> Dict[str, Any]:
        """Return the latest teleop IPC/heartbeat state."""
        self._sync_process_state()
        return {
            "service_state": self._state,
            "ipc_online": self._ipc_online,
            "heartbeat": dict(self._ipc_last_state),
            "last_error": self._last_error,
        }

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
                description="Teleimager environment Python interpreter used for XR teleop",
                required=True,
            ),
            ConfigParameter(
                key="working_dir",
                value=self._config.get("working_dir", "/home/unitree/xr_teleoperate/teleop"),
                type="string",
                description="Working directory for XR teleop",
                required=True,
            ),
            ConfigParameter(
                key="img_server_ip",
                value=self._config.get("img_server_ip", "127.0.0.1"),
                type="string",
                description="Image server IP passed to teleop",
                required=True,
            ),
            ConfigParameter(
                key="network_interface",
                value=self._config.get("network_interface", "eth0"),
                type="string",
                description="DDS network interface for teleop",
                required=True,
            ),
            ConfigParameter(
                key="ee",
                value=self._config.get("ee", "inspire_ftp"),
                type="choice",
                choices=["dex1", "dex3", "inspire_ftp", "inspire_dfx", "brainco"],
                description="End effector controller for teleop",
                required=True,
            ),
        ]
