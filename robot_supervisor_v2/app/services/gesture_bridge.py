"""
Gesture Bridge service implementation.
Manages the gesture API service for robot control with HTTP health checks.
"""

import subprocess
import asyncio
import aiohttp
import logging
import os
import sys
from typing import Dict, Any, Optional, List
from humanoid_platform import (
    GestureBackend,
    GestureSpec,
    PlatformId,
    RobotModelSpec,
    gesture_supported_models,
    get_gesture_spec,
    get_robot_model,
)
from .base import BaseService, ServiceState, ConfigParameter, HealthCheckConfig
from robot_services.gestures import (
    DEFAULT_GESTURE_SAFETY_POOL,
    GESTURE_SAFETY_POOLS,
    normalize_gesture_safety_pool,
)
from ..supervisor_config import SERVICE_ROBOT_CONTEXT_KEY


logger = logging.getLogger(__name__)


class GestureBridgeUnsupportedError(RuntimeError):
    """Raised when the configured robot has no gesture backend."""


_IMPLEMENTED_GESTURE_BACKENDS = {
    GestureBackend.UNITREE_G1_ARM_ACTIONS,
    GestureBackend.AGIBOT_A2_MOTION_PLAYER,
}


class GestureBridgeService(BaseService):
    """Gesture bridge service with HTTP health checking."""

    def __init__(self, name: str, config: Dict[str, Any]):
        service_config = dict(config)
        self._robot_context = service_config.pop(SERVICE_ROBOT_CONTEXT_KEY, None)
        super().__init__(
            name=name,
            display_name=service_config.get("display_name", "Gesture Bridge"),
            config=service_config
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None
        self._gesture_spec: GestureSpec | None = None

    def _get_safety_pool(self) -> str:
        return normalize_gesture_safety_pool(
            self._config.get("safety_pool", DEFAULT_GESTURE_SAFETY_POOL)
        )

    def _resolve_robot_model(self) -> RobotModelSpec:
        if not isinstance(self._robot_context, dict):
            raise GestureBridgeUnsupportedError(
                "Gesture bridge requires supervisor robot config context"
            )

        raw_model = self._robot_context.get("model")
        if not raw_model:
            raise GestureBridgeUnsupportedError(
                "Gesture bridge requires robot.model in supervisor config"
            )

        try:
            robot_model = get_robot_model(str(raw_model))
        except ValueError as exc:
            raise GestureBridgeUnsupportedError(
                f"Unknown robot.model '{raw_model}' for gesture bridge"
            ) from exc

        raw_platform = self._robot_context.get("platform")
        if raw_platform:
            try:
                configured_platform = PlatformId(str(raw_platform))
            except ValueError as exc:
                raise GestureBridgeUnsupportedError(
                    f"Unknown robot.platform '{raw_platform}' for gesture bridge"
                ) from exc

            if configured_platform != robot_model.platform:
                raise GestureBridgeUnsupportedError(
                    "Gesture bridge config mismatch: "
                    f"robot.model '{robot_model.id.value}' belongs to platform "
                    f"'{robot_model.platform.value}', not '{configured_platform.value}'"
                )

        return robot_model

    def _require_gesture_spec(self) -> GestureSpec:
        robot_model = self._resolve_robot_model()
        spec = get_gesture_spec(robot_model)
        if spec is None:
            supported = ", ".join(model_id.value for model_id in gesture_supported_models())
            raise GestureBridgeUnsupportedError(
                "Gesture bridge is not supported for "
                f"robot.model '{robot_model.id.value}' "
                f"(platform '{robot_model.platform.value}'). "
                f"Supported models: {supported or 'none'}"
            )

        if spec.backend not in _IMPLEMENTED_GESTURE_BACKENDS:
            raise GestureBridgeUnsupportedError(
                f"Gesture backend '{spec.backend.value}' is not implemented by this service"
            )

        return spec

    def _support_state(self) -> tuple[bool, Optional[str]]:
        try:
            self._require_gesture_spec()
        except GestureBridgeUnsupportedError as exc:
            return False, str(exc)
        return True, None

    def is_optional(self) -> bool:
        supported, _ = self._support_state()
        return super().is_optional() or not supported

    async def start(self) -> None:
        """Start the gesture bridge process."""
        if self._state not in (ServiceState.STOPPED, ServiceState.FAILED):
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            self._gesture_spec = self._require_gesture_spec()

            # Build command
            # Use sys.executable to ensure we use the same Python interpreter (and venv)
            cmd = [
                sys.executable,
                self._config.get("script", "robot_services/gestures/gesture_api.py")
            ]

            # Add port parameter if specified
            if "port" in self._config:
                cmd.extend(["--port", str(self._config["port"])])

            env = os.environ.copy()
            env["GESTURE_SAFETY_POOL"] = self._get_safety_pool()
            env["GESTURE_BACKEND"] = self._gesture_spec.backend.value
            env["GESTURE_CATALOG_ID"] = self._gesture_spec.catalog_id

            # Ensure log directory exists
            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)

            # Start process
            self._log_handle = open(self._log_file, 'a')
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                cwd=self._config.get("working_dir", "."),
                env=env,
            )

            # Wait for ready with health checks
            if await self.wait_for_ready(timeout=self._config.get("startup_timeout", 10)):
                self._state = ServiceState.RUNNING
                self._mark_started()
            else:
                self._state = ServiceState.FAILED
                self._last_error = f"Failed health check after {self._config.get('startup_timeout', 10)}s"
                await self.stop()  # Clean up
                raise RuntimeError(self._last_error)

        except GestureBridgeUnsupportedError as e:
            self._state = ServiceState.FAILED
            self._last_error = str(e)
            logger.warning("%s", e)
            raise
        except Exception as e:
            self._state = ServiceState.FAILED
            self._last_error = str(e)
            raise

    async def stop(self) -> None:
        """Stop the gesture bridge process."""
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
        """HTTP health check to gesture API /health endpoint."""
        # First check if process is alive
        if not self._process or self._process.poll() is not None:
            return False

        # Then check HTTP endpoint
        port = self._config.get("port", 8090)
        url = f"http://localhost:{port}/health"

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
        return HealthCheckConfig(
            type="http",
            endpoint=f"http://localhost:{self._config.get('port', 8090)}/health",
            timeout_seconds=self._config.get("health_timeout", 2.0),
            interval_seconds=self._config.get("health_interval", 5.0)
        )

    def get_log_path(self) -> str:
        """Return path to log file."""
        return self._log_file

    def get_gesture_state(self) -> Dict[str, Any]:
        try:
            spec = self._require_gesture_spec()
            supported = True
            unsupported_reason = None
        except GestureBridgeUnsupportedError as exc:
            spec = None
            supported = False
            unsupported_reason = str(exc)

        return {
            "configured": True,
            "supported": supported,
            "unsupported_reason": unsupported_reason,
            "robot": dict(self._robot_context) if isinstance(self._robot_context, dict) else None,
            "service_state": self._state.value,
            "backend": spec.backend.value if spec else None,
            "catalog_id": spec.catalog_id if spec else None,
            "last_error": self._last_error,
        }

    def get_config_parameters(self) -> List[ConfigParameter]:
        """Return configurable parameters."""
        return [
            ConfigParameter(
                key="safety_pool",
                value=self._get_safety_pool(),
                type="choice",
                choices=list(GESTURE_SAFETY_POOLS),
                description="Active gesture safety pool",
                required=True
            ),
            ConfigParameter(
                key="port",
                value=self._config.get("port", 8090),
                type="int",
                description="HTTP port for gesture API",
                required=True
            ),
            ConfigParameter(
                key="script",
                value=self._config.get("script", "robot_services/gestures/gesture_api.py"),
                type="string",
                description="Path to gesture API script",
                required=True
            )
        ]

    async def update_config(self, updates: Dict[str, Any]) -> None:
        if "safety_pool" in updates:
            updates = updates.copy()
            updates["safety_pool"] = normalize_gesture_safety_pool(updates["safety_pool"])

        self._config.update(updates)

        if self._state == ServiceState.RUNNING:
            logger.info("Gesture bridge config changed, restarting service...")
            await self.restart()
