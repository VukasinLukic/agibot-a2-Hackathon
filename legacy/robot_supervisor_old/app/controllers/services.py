from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

from ..config import ServiceDefinition, SupervisorConfig, load_services_map
from ..models import (
    DependencyStatus,
    ServiceCommandParameter,
    ServiceCommandParameterUpdate,
    ServiceConfigResponse,
    ServiceModeInfo,
    ServiceStatus,
)
from ..service_manager import (
    BaseServiceManager,
    ProcessServiceManager,
    ServiceManagerError,
    SystemdServiceManager,
    UnknownServiceError,
)

logger = logging.getLogger(__name__)


class ServiceController:
    def __init__(
        self,
        config: SupervisorConfig,
        before_stop_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._config = config
        self._base_services = load_services_map(config)
        self._services: Dict[str, ServiceDefinition] = {}
        self._service_order = [svc.name for svc in config.services]
        self._managers: Dict[str, BaseServiceManager] = {}
        self._dependencies: Dict[str, List[str]] = {
            svc.name: list(svc.depends_on or []) for svc in config.services
        }
        self._dependents: Dict[str, List[str]] = {}
        self._auto_started: Dict[str, Dict[str, float]] = {}
        self._current_modes: Dict[str, Optional[str]] = {}
        self._command_overrides: Dict[str, Dict[Optional[str], List[str]]] = {}
        self._before_stop_hook = before_stop_hook
        self._initialize_services()
        self._prepare_managers()
        self._validate_dependencies()

    def list_statuses(self) -> List[ServiceStatus]:
        return [self.status(name) for name in self._service_order]

    def status(self, name: str) -> ServiceStatus:
        status_obj = self._raw_status(name)
        status_obj.dependencies = self._dependency_statuses(name)
        mode = self._current_modes.get(name)
        status_obj.mode = mode
        status_obj.mode_display_name = self._mode_display_name(name, mode)
        status_obj.modes = self._mode_info(name)
        return status_obj

    def start(self, name: str) -> ServiceStatus:
        self._start_dependencies(name)
        return self._start_single(name)

    def stop(self, name: str) -> ServiceStatus:
        self._stop_dependents(name)
        return self._stop_single(name)

    def restart(self, name: str) -> ServiceStatus:
        dependencies = list(self._dependency_chain(name))
        restart_flags: Dict[str, bool] = {svc: True for svc in [name] + dependencies}
        stop_order: List[str] = []
        stopped: Set[str] = set()

        for svc in [name] + list(reversed(dependencies)):
            self._stop_for_restart(svc, restart_flags, stop_order, stopped)

        final_status: Optional[ServiceStatus] = None
        for svc in reversed(stop_order):
            if not restart_flags.get(svc):
                continue
            status = self.start(svc)
            if svc == name:
                final_status = status
        return final_status or self.status(name)

    def set_mode(self, name: str, mode: str) -> ServiceStatus:
        base = self._base_definition(name)
        if not base.modes:
            raise ServiceManagerError(f"Service '{name}' does not support modes")
        if not base.mode_definition(mode):
            raise ServiceManagerError(f"Mode '{mode}' not defined for service '{name}'")
        current = self._current_modes.get(name)
        if current == mode:
            logger.info("Service %s already in mode %s", name, mode)
            return self.status(name)
        status = self._raw_status(name)
        was_active = status.state == "active"
        if was_active:
            logger.info("Stopping %s before switching to mode %s", name, mode)
            self._stop_single(name)
        else:
            self._ensure_stopped(name)
        self._apply_mode(name, mode)
        if was_active:
            return self.start(name)
        return self.status(name)

    def config(self, name: str) -> ServiceConfigResponse:
        definition = self._get_definition(name)
        params = [entry.parameter for entry in self._command_parameter_entries(definition)]
        return ServiceConfigResponse(
            name=definition.name,
            display_name=definition.display_name,
            mode=self._current_modes.get(name),
            command=list(definition.command or []),
            parameters=params,
        )

    def update_config(self, name: str, updates: List[ServiceCommandParameterUpdate]) -> ServiceStatus:
        if not updates:
            return self.status(name)
        definition = self._get_definition(name)
        command = list(definition.command or [])
        if not command:
            raise ServiceManagerError(f"Service '{name}' has no command to update")
        lookup = self._command_parameter_lookup(definition)
        if not lookup:
            raise ServiceManagerError(f"Service '{name}' has no editable parameters")

        changed = False
        for item in updates:
            entry = lookup.get(item.id)
            if not entry:
                raise ServiceManagerError(f"Unknown parameter '{item.id}' for service '{name}'")
            if entry.index is None:
                raise ServiceManagerError(f"Parameter '{item.id}' cannot be updated")
            new_value = "" if item.value is None else str(item.value)
            if command[entry.index] == new_value:
                continue
            command[entry.index] = new_value
            changed = True

        if not changed:
            return self.status(name)

        definition.command = command
        self._services[name] = definition
        self._store_command_override(name, self._current_modes.get(name), command)

        status = self._raw_status(name)
        was_active = status.state == "active"
        active_dependents = self._active_dependents(name)
        if active_dependents:
            self._stop_dependents(name)
        if was_active:
            self._stop_single(name)
            result = self.start(name)
        else:
            result = self.status(name)

        for dependant in active_dependents:
            try:
                dependant_status = self._raw_status(dependant)
                if dependant_status.state != "active":
                    self.start(dependant)
            except ServiceManagerError as exc:
                logger.warning(
                    "Failed to restart dependant %s after %s config update: %s",
                    dependant,
                    name,
                    exc,
                )

        return result

    def stop_all(self) -> None:
        logger.info("Stopping all managed services")
        for name in reversed(self._service_order):
            try:
                self.stop(name)
            except ServiceManagerError as exc:
                logger.warning("Failed to stop %s during shutdown: %s", name, exc)

    def available_services(self) -> List[str]:
        return list(self._service_order)

    # internal helpers -------------------------------------------------

    def _initialize_services(self) -> None:
        for name, definition in self._base_services.items():
            mode = definition.initial_mode
            self._current_modes[name] = mode
            self._services[name] = self._definition_with_overrides(name, mode)

    def _apply_mode(self, name: str, mode: Optional[str]) -> None:
        self._current_modes[name] = mode
        self._services[name] = self._definition_with_overrides(name, mode)

    def _definition_with_overrides(self, name: str, mode: Optional[str]) -> ServiceDefinition:
        base = self._base_definition(name).with_mode(mode)
        overrides = self._command_overrides.get(name)
        if overrides:
            override = overrides.get(mode)
            if override:
                base.command = list(override)
        return base

    def _get_definition(self, name: str):
        if name not in self._services:
            raise UnknownServiceError(f"Unknown service '{name}'")
        return self._services[name]

    def _base_definition(self, name: str) -> ServiceDefinition:
        if name not in self._base_services:
            raise UnknownServiceError(f"Unknown service '{name}'")
        return self._base_services[name]

    def _backend_for(self, definition):
        backend = definition.backend or self._config.default_backend
        if backend == "systemd" and not definition.unit_name:
            definition.unit_name = f"{definition.name}.service"
        return backend

    def _manager_for(self, backend: str) -> BaseServiceManager:
        manager = self._managers.get(backend)
        if not manager:
            raise ServiceManagerError(f"Backend '{backend}' is not available on this host")
        return manager

    def _raw_status(self, name: str) -> ServiceStatus:
        definition = self._get_definition(name)
        backend = self._backend_for(definition)
        manager = self._manager_for(backend)
        return manager.status(definition)

    def _prepare_managers(self) -> None:
        requested_backends = {self._backend_for(svc) for svc in self._services.values()}

        if "process" in requested_backends:
            self._managers["process"] = ProcessServiceManager(log_dir=self._config.process_log_dir)

        if "systemd" in requested_backends:
            try:
                self._managers["systemd"] = SystemdServiceManager()
            except ServiceManagerError as exc:
                logger.warning("Systemd backend requested but unavailable: %s", exc)
        for service, deps in self._dependencies.items():
            for dep in deps:
                self._dependents.setdefault(dep, []).append(service)
        for service in self._services:
            self._dependents.setdefault(service, [])

    def set_before_stop_hook(self, hook: Optional[Callable[[str], None]]) -> None:
        self._before_stop_hook = hook

    def _start_dependencies(self, name: str) -> None:
        auto_map = self._auto_started.setdefault(name, {})
        auto_map.clear()
        for dependency in self._dependency_chain(name):
            status, started_at = self._ensure_started(dependency)
            self._wait_until_ready(dependency, status)
            if started_at is not None:
                auto_map[dependency] = started_at

    def _stop_dependents(self, name: str) -> None:
        for dependant in self._dependents_chain(name):
            self._ensure_stopped(dependant)

    def _start_single(self, name: str) -> ServiceStatus:
        current_status = self._raw_status(name)
        if current_status.state == "active":
            return self.status(name)

        definition = self._get_definition(name)
        backend = self._backend_for(definition)
        manager = self._manager_for(backend)
        manager.start(definition)
        status = self._raw_status(name)
        if status.state != "active":
            detail = status.info or "process exited"
            raise ServiceManagerError(f"{name} failed to start ({detail})")
        return self.status(name)

    def _stop_single(self, name: str) -> ServiceStatus:
        if self._before_stop_hook:
            try:
                self._before_stop_hook(name)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("before_stop_hook for %s raised: %s", name, exc)
        definition = self._get_definition(name)
        backend = self._backend_for(definition)
        manager = self._manager_for(backend)
        manager.stop(definition)
        self._auto_started.pop(name, None)
        return self.status(name)

    def _restart_single(self, name: str) -> ServiceStatus:
        definition = self._get_definition(name)
        backend = self._backend_for(definition)
        manager = self._manager_for(backend)
        manager.restart(definition)
        return self.status(name)

    def _ensure_started(self, name: str) -> Tuple[ServiceStatus, Optional[float]]:
        status = self._raw_status(name)
        if status.state != "active":
            logger.info("Dependency %s not active (state=%s); starting", name, status.state)
            started = self._start_single(name)
            return started, time.time()
        return status, None

    def _ensure_stopped(self, name: str) -> ServiceStatus:
        status = self._raw_status(name)
        if status.state == "active":
            logger.info("Stopping dependant %s before target stop", name)
            return self._stop_single(name)
        return status

    def _dependency_chain(self, name: str) -> List[str]:
        seen: Set[str] = set()
        ordered: List[str] = []
        self._walk_dependencies(name, seen, ordered)
        return ordered

    def _walk_dependencies(self, name: str, seen: Set[str], ordered: List[str]) -> None:
        for dep in self._dependencies.get(name, []):
            if dep in seen:
                continue
            seen.add(dep)
            self._walk_dependencies(dep, seen, ordered)
            ordered.append(dep)

    def _dependents_chain(self, name: str) -> List[str]:
        seen: Set[str] = set()
        ordered: List[str] = []
        self._walk_dependents(name, seen, ordered)
        return ordered

    def _walk_dependents(self, name: str, seen: Set[str], ordered: List[str]) -> None:
        for child in self._dependents.get(name, []):
            if child in seen:
                continue
            seen.add(child)
            self._walk_dependents(child, seen, ordered)
            ordered.append(child)

    def _dependency_statuses(self, name: str) -> List[DependencyStatus]:
        statuses: List[DependencyStatus] = []
        auto_map = self._auto_started.get(name, {})
        for dep in self._dependencies.get(name, []):
            dep_status = self._raw_status(dep)
            statuses.append(
                DependencyStatus(
                    name=dep_status.name,
                    display_name=dep_status.display_name,
                    state=dep_status.state,
                    auto_started_at=auto_map.get(dep),
                )
            )
        return statuses

    def _mode_info(self, name: str) -> List[ServiceModeInfo]:
        base = self._base_definition(name)
        info: List[ServiceModeInfo] = []
        for mode in base.modes:
            info.append(ServiceModeInfo(name=mode.name, display_name=mode.display_name))
        return info

    def _mode_display_name(self, name: str, mode: Optional[str]) -> Optional[str]:
        if not mode:
            return None
        base = self._base_definition(name)
        mode_def = base.mode_definition(mode)
        return mode_def.display_name if mode_def else mode

    def _wait_until_ready(self, name: str, status: ServiceStatus) -> None:
        definition = self._get_definition(name)
        required = definition.ready_after_seconds or 0.0
        if required <= 0:
            return
        if status.state != "active":
            return
        start_ts = status.started_at
        if not start_ts:
            logger.info("Ready delay set for %s but start time unknown; skipping delay", name)
            return
        remaining = required - max(0.0, time.time() - start_ts)
        while remaining > 0:
            logger.info("Waiting %.1fs for %s to be ready", remaining, name)
            time.sleep(min(0.5, remaining))
            status = self._raw_status(name)
            if status.state != "active":
                logger.info("%s left active state while waiting; restarting wait", name)
                status, _ = self._ensure_started(name)
                start_ts = status.started_at or time.time()
            remaining = required - max(0.0, time.time() - (status.started_at or start_ts))

    def _command_parameter_entries(self, definition: ServiceDefinition) -> List["_CommandParameterEntry"]:
        command = list(definition.command or [])
        entries: List[_CommandParameterEntry] = []
        counts: Dict[str, int] = {}
        i = 0
        while i < len(command):
            token = command[i]
            if token.startswith("-") and len(token) > 1:
                key = token.lstrip("-")
                counts[key] = counts.get(key, 0) + 1
                identifier = key if counts[key] == 1 else f"{key}_{counts[key]}"
                label = key.replace("-", " ").replace("_", " ").title()
                value_index = i + 1 if i + 1 < len(command) else None
                if value_index is None:
                    i += 1
                    continue
                value = command[value_index] if value_index is not None else None
                entries.append(
                    _CommandParameterEntry(
                        parameter=ServiceCommandParameter(
                            id=identifier,
                            label=label or identifier,
                            value=value,
                            flag=token,
                        ),
                        index=value_index,
                    )
                )
                i += 2 if value_index is not None else 1
            else:
                i += 1
        return entries

    def _command_parameter_lookup(self, definition: ServiceDefinition) -> Dict[str, "_CommandParameterEntry"]:
        return {entry.parameter.id: entry for entry in self._command_parameter_entries(definition)}

    def _active_dependents(self, name: str) -> List[str]:
        active: List[str] = []
        for dependant in self._dependents_chain(name):
            status = self._raw_status(dependant)
            if status.state == "active":
                active.append(dependant)
        return active

    def _store_command_override(self, name: str, mode: Optional[str], command: List[str]) -> None:
        overrides = self._command_overrides.setdefault(name, {})
        overrides[mode] = list(command)

    def _stop_for_restart(
        self,
        name: str,
        restart_flags: Dict[str, bool],
        stop_order: List[str],
        stopped: Set[str],
    ) -> None:
        if name in stopped:
            return
        status = self._raw_status(name)
        for dependant in self._dependents.get(name, []):
            self._stop_for_restart(dependant, restart_flags, stop_order, stopped)
        restart_flags.setdefault(name, status.state == "active")
        self._stop_single(name)
        stopped.add(name)
        stop_order.append(name)


    def _validate_dependencies(self) -> None:
        for service, deps in self._dependencies.items():
            for dep in deps:
                if dep not in self._services:
                    raise ServiceManagerError(f"Service '{service}' depends on unknown service '{dep}'")

        visiting: Set[str] = set()
        visited: Set[str] = set()

        def visit(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise ServiceManagerError(f"Circular dependency detected at '{node}'")
            visiting.add(node)
            for dep in self._dependencies.get(node, []):
                visit(dep)
            visiting.remove(node)
            visited.add(node)

        for service_name in self._services:
            visit(service_name)


class _CommandParameterEntry:
    def __init__(self, parameter: ServiceCommandParameter, index: Optional[int]):
        self.parameter = parameter
        self.index = index
