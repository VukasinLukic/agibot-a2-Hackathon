"""
Service registry and manager for all supervised services.
"""

from typing import Dict, List, Optional, Any
import asyncio
from .base import BaseService, ServiceStatus, ServiceState
from .livekit_server import LiveKitServerService
from .voice_agent import VoiceAgentService
from .camera_bridge import CameraBridgeService
from .audio_bridge import AudioBridgeService
from .audio_bridge_remote import AudioBridgeRemoteService
from .gesture_bridge import GestureBridgeService
from .inspire_hands import InspireHandsService
from .rag_service import RAGService
from .teleimager_server import TeleimagerServerService
from .xr_teleop import XRTeleopService
from .conversation_camera_stream import ConversationCameraStreamService
from .robot_temperature_monitor import RobotTemperatureMonitorService
from .vision_controller import VisionControllerService
from .video_recording import VideoRecordingService
from .igra_service import IgraService
from ..supervisor_config import SERVICE_ROBOT_CONTEXT_KEY
from humanoid_platform import AudioBridgeMode, PlatformId, get_robot_model


class ServiceConflictError(RuntimeError):
    """Raised when a service conflicts with another active service."""


class ServiceRegistry:
    """Registry of all available service types."""

    _service_types: Dict[str, type] = {
        "livekit-server": LiveKitServerService,
        "voice-agent": VoiceAgentService,
        "camera-bridge": CameraBridgeService,
        "audio-bridge": AudioBridgeService,
        "gesture-bridge": GestureBridgeService,
        "inspire-hands": InspireHandsService,
        "rag-service": RAGService,
        "teleimager-server": TeleimagerServerService,
        "xr-teleop": XRTeleopService,
        "conversation-camera-stream": ConversationCameraStreamService,
        "robot-temperature-monitor": RobotTemperatureMonitorService,
        "vision-controller": VisionControllerService,
        "video-recording-service": VideoRecordingService,
        "igra": IgraService,
    }

    @classmethod
    def _resolve_audio_bridge_service_class(cls, config: Dict[str, Any]) -> type[BaseService]:
        robot_context = config.get(SERVICE_ROBOT_CONTEXT_KEY)
        if not isinstance(robot_context, dict):
            return AudioBridgeService

        raw_model = robot_context.get("model")
        if not raw_model:
            return AudioBridgeService

        robot_model = get_robot_model(str(raw_model))

        raw_platform = robot_context.get("platform")
        if raw_platform:
            configured_platform = PlatformId(str(raw_platform))
            if configured_platform != robot_model.platform:
                raise ValueError(
                    "Audio bridge config mismatch: "
                    f"robot.model '{robot_model.id.value}' belongs to platform "
                    f"'{robot_model.platform.value}', not '{configured_platform.value}'"
                )

        if robot_model.audio_bridge.mode == AudioBridgeMode.REMOTE:
            return AudioBridgeRemoteService
        return AudioBridgeService

    @classmethod
    def create_service(cls, service_type: str, name: str, config: Dict[str, Any]) -> BaseService:
        """
        Factory method to create service instances.

        Args:
            service_type: Type identifier (e.g., "livekit-server", "voice-agent")
            name: Unique name for this service instance
            config: Service-specific configuration

        Returns:
            BaseService instance

        Raises:
            ValueError: If service_type is not registered
        """
        service_class = cls._service_types.get(service_type)
        if not service_class:
            raise ValueError(
                f"Unknown service type: {service_type}. "
                f"Available: {list(cls._service_types.keys())}"
            )

        if service_type == "audio-bridge":
            service_class = cls._resolve_audio_bridge_service_class(config)

        return service_class(name, config)

    @classmethod
    def get_available_types(cls) -> List[str]:
        """Get list of registered service types."""
        return list(cls._service_types.keys())


class ServiceManager:
    """
    Manages all service instances.
    Provides unified interface for service lifecycle, status, and health monitoring.
    """

    def __init__(self):
        self._services: Dict[str, BaseService] = {}
        self._health_monitor_task: Optional[asyncio.Task] = None
        self._health_monitor_interval: float = 5.0  # seconds
        self._speech_service_names: tuple[str, ...] = (
            "livekit",
            "voice-agent",
            "audio-bridge",
            "gesture-bridge",
            "rag-service",
        )

    def register(self, service: BaseService):
        """
        Register a service instance.

        Args:
            service: Service instance to register

        Raises:
            ValueError: If service with same name already registered
        """
        if service.name in self._services:
            raise ValueError(f"Service {service.name} already registered")

        self._services[service.name] = service

    def unregister(self, name: str) -> bool:
        """
        Unregister a service.

        Args:
            name: Service name to unregister

        Returns:
            True if service was unregistered, False if not found
        """
        if name in self._services:
            del self._services[name]
            return True
        return False

    def get(self, name: str) -> Optional[BaseService]:
        """
        Get service by name.

        Args:
            name: Service name

        Returns:
            Service instance or None if not found
        """
        return self._services.get(name)

    def list_all(self) -> List[BaseService]:
        """Get list of all registered services."""
        return list(self._services.values())

    def get_all_status(self) -> List[ServiceStatus]:
        """Get status for all registered services."""
        return [service.get_status() for service in self._services.values()]

    # === Dependency Resolution ===

    def _compute_dependency_order(self) -> List[str]:
        """
        Compute service start order based on dependencies using topological sort.

        Returns:
            List of service names in start order

        Raises:
            ValueError: If circular dependency detected or missing dependency
        """
        # Build dependency graph
        graph = {}
        for service in self._services.values():
            graph[service.name] = service.get_dependencies()

        # Topological sort
        visited = set()
        order = []

        def visit(name: str, path: set):
            if name in path:
                raise ValueError(f"Circular dependency detected: {' -> '.join(path)} -> {name}")
            if name in visited:
                return

            path.add(name)
            for dep in graph.get(name, []):
                if dep not in self._services:
                    raise ValueError(f"Service '{name}' depends on unknown service '{dep}'")
                visit(dep, path)
            path.remove(name)

            visited.add(name)
            order.append(name)

        # Visit all services
        for service_name in graph.keys():
            visit(service_name, set())

        return order

    def _validate_dependencies(self):
        """Validate dependencies. Raises ValueError if invalid."""
        try:
            self._compute_dependency_order()
        except ValueError as e:
            raise ValueError(f"Invalid service dependencies: {e}")

    def _compute_dependency_levels(self) -> Dict[str, int]:
        """
        Compute dependency level for each service.
        Level 0 = no dependencies, Level 1 = depends on level 0, etc.

        Returns:
            Dict mapping service name to dependency level
        """
        levels = {}

        def get_level(name: str) -> int:
            if name in levels:
                return levels[name]

            service = self._services[name]
            deps = service.get_dependencies()

            if not deps:
                level = 0
            else:
                # Level is 1 + max level of dependencies
                level = 1 + max(get_level(dep) for dep in deps)

            levels[name] = level
            return level

        # Compute level for all services
        for service_name in self._services.keys():
            get_level(service_name)

        return levels

    # === Service Lifecycle Methods ===

    def _raise_if_service_running(self, blocker_name: str, message: str) -> None:
        blocker = self.get(blocker_name)
        if blocker and blocker.get_status().state == ServiceState.RUNNING:
            raise ServiceConflictError(message)

    def _is_camera_bridge_service(self, name: str) -> bool:
        service = self.get(name)
        return isinstance(service, CameraBridgeService)

    def _running_camera_bridge_services(self, *, exclude: Optional[str] = None) -> list[BaseService]:
        active: list[BaseService] = []
        for service in self._services.values():
            if exclude and service.name == exclude:
                continue
            if isinstance(service, CameraBridgeService) and service.get_status().state == ServiceState.RUNNING:
                active.append(service)
        return active

    def _get_service_exclusivity_conflict(self, name: str) -> Optional[str]:
        if name == "conversation-camera-stream":
            blocking_cameras = self._running_camera_bridge_services()
            if blocking_cameras:
                blockers = ", ".join(service.display_name for service in blocking_cameras)
                return (
                    "Conversation Camera Stream is exclusive with camera bridge services. "
                    f"Stop {blockers} first."
                )
        elif self._is_camera_bridge_service(name):
            blocker = self.get("conversation-camera-stream")
            if blocker and blocker.get_status().state == ServiceState.RUNNING:
                return (
                    "Camera bridge services are exclusive with Conversation Camera Stream. "
                    "Stop Conversation Camera Stream first."
                )
        elif name == "gesture-bridge":
            blocker = self.get("xr-teleop")
            if blocker and blocker.get_status().state == ServiceState.RUNNING:
                return "Gesture Bridge is exclusive with XR Teleoperation. Stop XR Teleoperation first."
        elif name == "xr-teleop":
            blocker = self.get("gesture-bridge")
            if blocker and blocker.get_status().state == ServiceState.RUNNING:
                return "XR Teleoperation is exclusive with Gesture Bridge. Stop Gesture Bridge first."

        return None

    def _assert_service_exclusivity(self, name: str) -> None:
        conflict = self._get_service_exclusivity_conflict(name)
        if conflict:
            raise ServiceConflictError(conflict)

    async def _wait_for_service_ready(self, service: BaseService, timeout: float = 30.0) -> ServiceStatus:
        """Wait for a concurrently-starting service to report running and healthy."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while loop.time() < deadline:
            status = service.get_status()
            if status.state == ServiceState.RUNNING:
                remaining = max(0.1, deadline - loop.time())
                if await service.wait_for_ready(timeout=remaining):
                    return service.get_status()
                break

            if status.state in (ServiceState.STOPPED, ServiceState.FAILED, ServiceState.STOPPING):
                raise RuntimeError(
                    f"Service {service.name} did not finish starting (current state: {status.state})"
                )

            await asyncio.sleep(0.25)

        raise TimeoutError(f"Timed out waiting for service {service.name} to start")

    async def start_service(self, name: str, start_dependencies: bool = True) -> ServiceStatus:
        """
        Start a specific service.

        Args:
            name: Service name
            start_dependencies: If True, automatically start dependencies first

        Returns:
            Updated service status

        Raises:
            ValueError: If service not found or dependency missing
            RuntimeError: If service fails to start
        """
        service = self.get(name)
        if not service:
            raise ValueError(f"Service {name} not found")

        self._assert_service_exclusivity(name)

        status = service.get_status()
        if status.state == ServiceState.RUNNING:
            return status
        if status.state == ServiceState.STARTING:
            print(f"  Waiting for {service.display_name} to finish starting")
            return await self._wait_for_service_ready(service, timeout=30)

        if start_dependencies:
            # Start dependencies first
            for dep_name in service.get_dependencies():
                dep_service = self.get(dep_name)
                if not dep_service:
                    raise ValueError(f"Dependency '{dep_name}' not found for service '{name}'")

                dep_status = dep_service.get_status()
                if dep_status.state == ServiceState.RUNNING:
                    continue
                if dep_status.state == ServiceState.STARTING:
                    print(f"  Waiting for dependency: {dep_service.display_name}")
                    await self._wait_for_service_ready(dep_service, timeout=30)
                else:
                    print(f"  Starting dependency: {dep_service.display_name}")
                    await self.start_service(dep_name, start_dependencies=True)

                # Wait for dependency to be ready
                await self._wait_for_service_ready(dep_service, timeout=30)

        # Now start the requested service
        await service.start()
        return service.get_status()

    async def stop_service(self, name: str) -> ServiceStatus:
        """
        Stop a specific service.

        Args:
            name: Service name

        Returns:
            Updated service status

        Raises:
            ValueError: If service not found
        """
        service = self.get(name)
        if not service:
            raise ValueError(f"Service {name} not found")

        await service.stop()
        return service.get_status()

    async def restart_service(self, name: str) -> ServiceStatus:
        """
        Restart a specific service.

        Args:
            name: Service name

        Returns:
            Updated service status

        Raises:
            ValueError: If service not found
            RuntimeError: If service fails to restart
        """
        service = self.get(name)
        if not service:
            raise ValueError(f"Service {name} not found")

        self._assert_service_exclusivity(name)
        await service.restart()
        return service.get_status()

    async def _start_service_safe(self, service: BaseService) -> None:
        """Start a service with error handling. Optional services don't block startup."""
        try:
            await self.start_service(service.name, start_dependencies=False)
            print(f"  ✓ Started {service.display_name}")
        except Exception as e:
            if service.is_optional():
                print(f"  ⚠ Optional service {service.display_name} failed (continuing): {e}")
                # Don't propagate - allow startup to continue
            else:
                print(f"  ✗ Failed to start {service.display_name}: {e}")
                raise  # Propagate to stop startup

    async def start_all(self) -> List[ServiceStatus]:
        """
        Start all services in parallel batches respecting dependencies.
        Services at the same dependency level start in parallel.

        Example:
            Level 0: [livekit, gesture-bridge] - start in parallel
            Level 1: [voice-agent, audio-bridge, camera-bridge] - start in parallel after level 0

        Returns:
            List of service statuses

        Raises:
            ValueError: If invalid dependencies detected
        """
        # Validate dependencies
        self._validate_dependencies()

        # Compute dependency levels
        levels = self._compute_dependency_levels()

        # Group services by level
        batches: Dict[int, List[str]] = {}
        for service_name, level in levels.items():
            if self._services[service_name].is_manual_only():
                continue
            if level not in batches:
                batches[level] = []
            batches[level].append(service_name)

        # Start services level by level
        for level in sorted(batches.keys()):
            service_names = batches[level]
            print(f"Starting level {level}: {', '.join(service_names)}")

            # Start all services at this level in parallel
            tasks = []
            for service_name in service_names:
                service = self._services[service_name]
                tasks.append(self._start_service_safe(service))

            # Wait for all to complete
            await asyncio.gather(*tasks, return_exceptions=False)

        return self.get_all_status()

    async def start_speech_services(self) -> Dict[str, Any]:
        """
        Start the speech stack services sequentially using the existing per-service
        startup behavior. Conflicting services are skipped, matching the previous
        frontend-only flow.
        """
        skipped_services: List[str] = []

        for service_name in self._speech_service_names:
            service = self.get(service_name)
            if not service:
                continue

            status = service.get_status()
            if status.state == ServiceState.RUNNING:
                continue

            conflict = self._get_service_exclusivity_conflict(service_name)
            if conflict:
                skipped_services.append(service_name)
                continue

            if status.state == ServiceState.FAILED:
                await self.restart_service(service_name)
            else:
                await self.start_service(service_name)

        services = [
            service.get_status()
            for service_name in self._speech_service_names
            if (service := self.get(service_name)) is not None
        ]
        return {
            "services": services,
            "skipped_services": skipped_services,
        }

    async def stop_all(self) -> List[ServiceStatus]:
        """
        Stop all services in reverse dependency order.
        Ensures dependents stop before their dependencies.

        Returns:
            List of service statuses
        """
        try:
            # Compute order and reverse it
            start_order = self._compute_dependency_order()
            stop_order = list(reversed(start_order))
        except ValueError:
            # If dependency validation fails, stop in any order
            stop_order = list(self._services.keys())

        # Stop services in reverse order
        for service_name in stop_order:
            service = self._services.get(service_name)
            if service:
                try:
                    await service.stop()
                    print(f"✓ Stopped {service.display_name}")
                except Exception as e:
                    print(f"✗ Error stopping {service.display_name}: {e}")

        return self.get_all_status()

    # === Health Monitoring ===

    async def _health_monitor_loop(self):
        """Background task to periodically check service health."""
        while True:
            try:
                await asyncio.sleep(self._health_monitor_interval)

                # Update health for all services in parallel
                tasks = [
                    service.update_health_status()
                    for service in self._services.values()
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in health monitor: {e}")

    def start_health_monitoring(self, interval: float = 5.0):
        """
        Start background health monitoring.

        Args:
            interval: Health check interval in seconds
        """
        if self._health_monitor_task and not self._health_monitor_task.done():
            return  # Already running

        self._health_monitor_interval = interval
        self._health_monitor_task = asyncio.create_task(self._health_monitor_loop())

    def stop_health_monitoring(self):
        """Stop background health monitoring."""
        if self._health_monitor_task:
            self._health_monitor_task.cancel()
            self._health_monitor_task = None

    # === Configuration Management ===

    async def update_service_config(self, name: str, updates: Dict[str, Any]) -> ServiceStatus:
        """
        Update service configuration.

        Args:
            name: Service name
            updates: Configuration updates

        Returns:
            Updated service status

        Raises:
            ValueError: If service not found
        """
        service = self.get(name)
        if not service:
            raise ValueError(f"Service {name} not found")

        await service.update_config(updates)
        return service.get_status()

    def get_service_config(self, name: str) -> Dict[str, Any]:
        """
        Get service configuration.

        Args:
            name: Service name

        Returns:
            Service configuration

        Raises:
            ValueError: If service not found
        """
        service = self.get(name)
        if not service:
            raise ValueError(f"Service {name} not found")

        return service.get_config()
