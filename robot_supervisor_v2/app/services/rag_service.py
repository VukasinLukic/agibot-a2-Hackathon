"""
RAG Service implementation.
Manages the local RAG API (FastAPI) process with HTTP health checks.
"""

import subprocess
import asyncio
import aiohttp
import os
import sys
from typing import Dict, Any, List
from .base import BaseService, ServiceState, ConfigParameter, HealthCheckConfig


class RAGService(BaseService):
    """RAG service with HTTP health checking."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(
            name=name,
            display_name=config.get("display_name", "RAG Service"),
            config=config
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None

    def _build_command(self) -> List[str]:
        """Build the uvicorn command to run the RAG API."""
        python_bin = self._config.get("python_bin") or sys.executable
        app_module = self._config.get("app_module", "rag_service.app:app")
        host = self._config.get("host", "0.0.0.0")
        port = int(self._config.get("port", 8098))

        cmd = [
            python_bin,
            "-m",
            "uvicorn",
            app_module,
            "--host",
            host,
            "--port",
            str(port),
        ]

        if self._config.get("reload"):
            cmd.append("--reload")

        return cmd

    def _build_environment(self) -> Dict[str, str]:
        """Build the RAG process environment from explicit service settings."""
        env = os.environ.copy()
        device = str(self._config.get("device", "cpu")).strip().lower()
        if device not in {"cpu", "cuda"}:
            raise ValueError("RAG device must be 'cpu' or 'cuda'")

        env["RAG_DEVICE"] = device
        env["RAG_STRICT_DEVICE"] = (
            "true" if self._config.get("strict_device", False) else "false"
        )
        return env

    def _health_host(self) -> str:
        """Return host to use for health checks (avoid 0.0.0.0)."""
        host = self._config.get("health_host") or self._config.get("host", "127.0.0.1")
        if host in ("0.0.0.0", "", None):
            return "127.0.0.1"
        return host

    async def start(self) -> None:
        if self._state != ServiceState.STOPPED:
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            cmd = self._build_command()
            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)

            env = self._build_environment()
            self._log_handle = open(self._log_file, "a")
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=self._config.get("working_dir", "."),
            )

            if await self.wait_for_ready(timeout=self._config.get("startup_timeout", 15)):
                self._state = ServiceState.RUNNING
                self._mark_started()
            else:
                self._state = ServiceState.FAILED
                self._last_error = (
                    f"Failed health check after {self._config.get('startup_timeout', 15)}s"
                )
                await self.stop()
                raise RuntimeError(self._last_error)
            
        except Exception as e:
            self._state = ServiceState.FAILED
            self._last_error = str(e)
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
        """HTTP health check to RAG API /health endpoint."""
        if not self._process or self._process.poll() is not None:
            return False

        host = self._health_host()
        port = int(self._config.get("port", 8098))
        url = f"http://{host}:{port}/health"

        try:
            timeout = aiohttp.ClientTimeout(total=self._config.get("health_timeout", 2.0))
            connector = aiohttp.TCPConnector(force_close=True)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=timeout) as resp:
                    return resp.status == 200
        except Exception:
            return False
    
    def get_health_config(self) -> HealthCheckConfig:
        """Return health check configuration."""
        host = self._health_host()
        return HealthCheckConfig(
            type="http",
            endpoint=f"http://{host}:{self._config.get('port', 8098)}/health",
            timeout_seconds=self._config.get("health_timeout", 2.0),
            interval_seconds=self._config.get("health_interval", 5.0)
        )

    def get_log_path(self) -> str:
        """Return path to log file."""
        return self._log_file

    def get_config_parameters(self) -> List[ConfigParameter]:
        """Return configurable parameters."""
        return [
            ConfigParameter(
                key="host",
                value=self._config.get("host", "0.0.0.0"),
                type="string",
                description="HTTP bind host for RAG API",
                required=True
            ),
            ConfigParameter(
                key="port",
                value=self._config.get("port", 8098),
                type="int",
                description="HTTP bind port for RAG API",
                required=True
            ),
            ConfigParameter(
                key="python_bin",
                value=self._config.get("python_bin"),
                type="string",
                description="Python interpreter for RAG service (optional)",
                required=False
            ),
            ConfigParameter(
                key="device",
                value=self._config.get("device", "cpu"),
                type="string",
                description="Embedding device: cpu or cuda",
                required=True
            ),
            ConfigParameter(
                key="strict_device",
                value=self._config.get("strict_device", False),
                type="bool",
                description="Fail startup instead of falling back when CUDA is unavailable",
                required=False
            ),
            ConfigParameter(
                key="app_module",
                value=self._config.get("app_module", "rag_service.app:app"),
                type="string",
                description="Uvicorn app module path",
                required=True
            ),
            ConfigParameter(
                key="working_dir",
                value=self._config.get("working_dir", "."),
                type="string",
                description="Working directory for the RAG process",
                required=False
            ),
        ]
