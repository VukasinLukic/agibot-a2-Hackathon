"""
Base service abstraction for all supervised services.
Provides common functionality: lifecycle management, health checks, logs, device enumeration, and configuration.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from enum import Enum
from datetime import datetime
import time
import os


class ServiceState(str, Enum):
    """Service lifecycle states."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class DeviceType(str, Enum):
    """Device types for enumeration."""
    AUDIO_INPUT = "audio_input"
    AUDIO_OUTPUT = "audio_output"
    VIDEO = "video"


class Device(BaseModel):
    """Represents a hardware device (audio/video)."""
    id: str
    name: str
    type: DeviceType
    is_default: bool
    metadata: Dict[str, Any] = {}


class ConfigParameter(BaseModel):
    """Service configuration parameter definition."""
    key: str
    value: Any
    type: str  # "string", "int", "float", "bool", "choice"
    choices: Optional[List[str]] = None
    description: str = ""
    required: bool = True


class HealthCheckConfig(BaseModel):
    """Configuration for health checks."""
    type: str  # "http", "tcp", "process"
    endpoint: Optional[str] = None  # For HTTP checks
    port: Optional[int] = None  # For TCP checks
    timeout_seconds: float = 2.0
    interval_seconds: float = 5.0


class HealthStatus(BaseModel):
    """Health check result."""
    healthy: bool
    last_check: float
    last_error: Optional[str] = None


class ServiceStatus(BaseModel):
    """Current service status."""
    name: str
    display_name: str
    state: ServiceState
    backend: str  # "process", "systemd", etc.
    pid: Optional[int] = None
    uptime_seconds: Optional[float] = None
    current_mode: Optional[str] = None
    available_modes: List[str] = []
    health_status: Optional[HealthStatus] = None


class BaseService(ABC):
    """
    Base class for all supervised services.
    Handles common functionality: health checks, logs, device enumeration, configuration.
    """

    def __init__(self, name: str, display_name: str, config: Dict[str, Any]):
        self.name = name
        self.display_name = display_name
        self._config = config
        self._process = None
        self._state = ServiceState.STOPPED
        self._start_time = None
        self._last_error = None
        self._health_status: Optional[HealthStatus] = None
        self._optional = config.get("optional", False)  # Flag for optional services

    # === Core Lifecycle ===

    @abstractmethod
    async def start(self) -> None:
        """Start the service. Subclass implements actual start logic."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the service. Subclass implements actual stop logic."""
        pass

    async def restart(self) -> None:
        """Restart the service."""
        await self.stop()
        self._write_log_divider()
        await self.start()

    def _write_log_divider(self) -> None:
        """Write a divider line to the log file before restart."""
        log_path = self.get_log_path()
        if not log_path:
            return

        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(log_path), exist_ok=True)

            # Write divider with timestamp
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            divider = f"\n{'=' * 80}\n=== SERVICE RESTART at {timestamp} ===\n{'=' * 80}\n\n"

            with open(log_path, 'a') as f:
                f.write(divider)
        except Exception:
            # Silently ignore errors writing divider
            pass

    # === Dependencies ===

    def get_dependencies(self) -> List[str]:
        """
        Return list of service names this service depends on.
        Dependencies will be started before this service.
        Override in subclasses that have dependencies.

        Returns:
            List of service names (empty list if no dependencies)
        """
        return []

    def is_optional(self) -> bool:
        """
        Check if this service is optional.
        Optional services can fail without blocking system startup.

        Returns:
            True if service is optional, False if required
        """
        return self._optional

    def is_manual_only(self) -> bool:
        """
        Check if this service should be excluded from global start-all actions.
        """
        return bool(self._config.get("manual_only", False))

    # === Health Checks ===

    @abstractmethod
    async def check_health(self) -> bool:
        """Check if service is healthy. Subclass implements health check logic."""
        pass

    async def wait_for_ready(self, timeout: float = 30.0) -> bool:
        """Wait for service to become healthy."""
        import asyncio
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                if await self.check_health():
                    self._health_status = HealthStatus(
                        healthy=True,
                        last_check=time.time(),
                        last_error=None
                    )
                    return True
            except Exception as e:
                self._health_status = HealthStatus(
                    healthy=False,
                    last_check=time.time(),
                    last_error=str(e)
                )

            await asyncio.sleep(1.0)

        return False

    async def update_health_status(self) -> None:
        """Update health status (called periodically by manager)."""
        try:
            healthy = await self.check_health()
            self._health_status = HealthStatus(
                healthy=healthy,
                last_check=time.time(),
                last_error=None if healthy else "Health check failed"
            )
        except Exception as e:
            self._health_status = HealthStatus(
                healthy=False,
                last_check=time.time(),
                last_error=str(e)
            )

    def get_health_config(self) -> Optional[HealthCheckConfig]:
        """Return health check configuration. Override in subclasses."""
        return None

    # === Status ===

    def get_status(self) -> ServiceStatus:
        """Get current service status."""
        uptime = None
        if self._start_time and self._state == ServiceState.RUNNING:
            uptime = self._calculate_uptime_seconds()

        return ServiceStatus(
            name=self.name,
            display_name=self.display_name,
            state=self._state,
            backend=self.get_backend_type(),
            pid=self._process.pid if self._process else None,
            uptime_seconds=uptime,
            current_mode=self.get_current_mode(),
            available_modes=self.get_available_modes(),
            health_status=self._health_status
        )

    def _mark_started(self) -> None:
        """Record service start time for uptime calculations."""
        self._start_time = time.monotonic()

    def _calculate_uptime_seconds(self) -> float:
        """Calculate uptime using monotonic time, with wall-clock fallback."""
        uptime = time.monotonic() - self._start_time
        if uptime >= 0:
            return uptime

        # Older services stored epoch seconds in _start_time. Keep status sane
        # for already-running services after code reloads or partial updates.
        wall_clock_uptime = time.time() - self._start_time
        return max(0.0, wall_clock_uptime)

    def get_backend_type(self) -> str:
        """Return backend type (process, systemd, etc.). Override if needed."""
        return "process"

    def get_current_mode(self) -> Optional[str]:
        """Return current service mode. Override in subclasses with modes."""
        return None

    def get_available_modes(self) -> List[str]:
        """Return available service modes. Override in subclasses with modes."""
        return []

    # === Logs ===

    @abstractmethod
    def get_log_path(self) -> Optional[str]:
        """Return path to log file, or None if logs go to stdout."""
        pass

    async def tail_logs(self, lines: int = 100) -> str:
        """Get last N lines of logs."""
        log_path = self.get_log_path()
        if not log_path or not os.path.exists(log_path):
            return ""

        # Read last N lines efficiently
        with open(log_path, 'r') as f:
            all_lines = f.readlines()
            return ''.join(all_lines[-lines:])

    # === Device Enumeration ===

    async def list_devices(self) -> List[Device]:
        """
        List available devices for this service.
        Default: empty list. Override in subclasses that need device enumeration.
        """
        return []

    # === Configuration ===

    def get_config_parameters(self) -> List[ConfigParameter]:
        """
        Get configurable parameters for this service.
        Default: empty list. Override in subclasses with configurable parameters.
        """
        return []

    async def update_config(self, updates: Dict[str, Any]) -> None:
        """
        Update service configuration.
        Default: merge updates into self._config.
        Override if you need custom logic (e.g., restart on certain changes).
        """
        self._config.update(updates)

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return self._config.copy()

    # === Mode Management (for multi-mode services) ===

    async def set_mode(self, mode: str) -> None:
        """
        Set service mode (e.g., audio bridge external/robot/local).
        Default: no-op. Override in subclasses that support modes.
        """
        raise NotImplementedError(f"Service {self.name} does not support mode changes")
