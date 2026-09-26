from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Dict, IO, List, Optional

from .config import ServiceDefinition
from .models import ServiceState, ServiceStatus

logger = logging.getLogger(__name__)

PYTHON_OVERRIDE_ENV = "ROBOT_SUPERVISOR_PYTHON"
VENV_OVERRIDE_ENV = "ROBOT_SUPERVISOR_VENV"

class ServiceManagerError(RuntimeError):
    """Raised when the supervisor cannot control a service."""


class UnknownServiceError(ServiceManagerError):
    """Raised when a service name is not recognized."""


class BaseServiceManager:
    backend: str

    def status(self, definition: ServiceDefinition) -> ServiceStatus:  # pragma: no cover - interface
        raise NotImplementedError

    def start(self, definition: ServiceDefinition) -> ServiceStatus:  # pragma: no cover - interface
        raise NotImplementedError

    def stop(self, definition: ServiceDefinition) -> ServiceStatus:  # pragma: no cover - interface
        raise NotImplementedError

    def restart(self, definition: ServiceDefinition) -> ServiceStatus:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class ProcInfo:
    process: subprocess.Popen
    started_at: float
    log_handle: Optional[IO[str]] = None
    last_exit: Optional[int] = None
    group_id: Optional[int] = None


class ProcessServiceManager(BaseServiceManager):
    backend = "process"

    def __init__(self, log_dir: Optional[str] = None, python_bin: Optional[str] = None) -> None:
        self.log_dir = Path(log_dir or (Path.cwd() / "logs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir = self.log_dir / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._processes: Dict[str, ProcInfo] = {}
        self._lock = RLock()
        self._python_bin = python_bin or os.getenv(PYTHON_OVERRIDE_ENV) or self._auto_detect_python()
        if self._python_bin:
            logger.info("Using python interpreter override: %s", self._python_bin)
        self._sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)

    def status(self, definition: ServiceDefinition) -> ServiceStatus:
        with self._lock:
            info = self._processes.get(definition.name)
            if not info:
                return self._inactive_status(definition)

            ret = info.process.poll()
            if ret is None:
                uptime = time.time() - info.started_at
                return ServiceStatus(
                    name=definition.name,
                    display_name=definition.display_name,
                    backend=self.backend,
                    state="active",
                    pid=info.process.pid,
                    started_at=info.started_at,
                    uptime_s=uptime,
                )

            info.last_exit = ret
            self._cleanup_process(definition.name)
            state: ServiceState = "inactive" if ret == 0 else "failed"
            return ServiceStatus(
                name=definition.name,
                display_name=definition.display_name,
                backend=self.backend,
            state=state,
            pid=info.process.pid,
            info=f"exit_code={ret}",
        )

    def start(self, definition: ServiceDefinition) -> ServiceStatus:
        current = self.status(definition)
        if current.state == "active":
            return current
        with self._lock:
            self._start(definition)
            return self.status(definition)

    def stop(self, definition: ServiceDefinition) -> ServiceStatus:
        with self._lock:
            self._stop(definition.name)
            return self.status(definition)

    def restart(self, definition: ServiceDefinition) -> ServiceStatus:
        with self._lock:
            self._stop(definition.name)
            self._start(definition)
            return self.status(definition)

    def _inactive_status(self, definition: ServiceDefinition) -> ServiceStatus:
        return ServiceStatus(
            name=definition.name,
            display_name=definition.display_name,
            backend=self.backend,
            state="inactive",
        )

    def _start(self, definition: ServiceDefinition) -> None:
        if not definition.command:
            raise ServiceManagerError(f"No command configured for {definition.name}")

        cwd = Path(definition.working_dir or os.getcwd())
        if not cwd.exists():
            raise ServiceManagerError(f"Working directory {cwd} does not exist for {definition.name}")

        env = os.environ.copy()
        env.update({key: str(value) for key, value in definition.env.items()})

        command = self._command_with_overrides(definition.command)
        log_path = self.log_dir / f"{definition.name}.log"
        self._rotate_log(log_path, definition.name)
        log_file = open(log_path, "a", encoding="utf-8")

        logger.info("Starting %s via subprocess: %s", definition.name, command)
        popen_kwargs = dict(
            cwd=str(cwd),
            env=env,
            stdout=log_file,
            stderr=log_file,
            text=True,
        )
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except FileNotFoundError as exc:
            log_file.close()
            executable = command[0] if command else "<empty>"
            raise ServiceManagerError(
                f"Executable '{executable}' not found for service '{definition.name}'. "
                "Install it or update the service command to point at the correct path."
            ) from exc
        group_id = process.pid if os.name != "nt" else None

        self._processes[definition.name] = ProcInfo(
            process=process,
            started_at=time.time(),
            log_handle=log_file,
            group_id=group_id,
        )

    def _stop(self, name: str) -> None:
        info = self._processes.get(name)
        if not info:
            return

        if info.process.poll() is None:
            logger.info("Stopping process service %s", name)
            self._signal_process_group(info, signal.SIGTERM)
            try:
                info.process.terminate()
            except ProcessLookupError:
                pass
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Error terminating %s: %s", name, exc)

            try:
                info.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Force killing %s after timeout", name)
                self._signal_process_group(info, self._sigkill)
                info.process.kill()
                info.process.wait(timeout=2)

        self._cleanup_process(name)

    def _cleanup_process(self, name: str) -> None:
        info = self._processes.pop(name, None)
        if info and info.log_handle:
            info.log_handle.close()

    def _signal_process_group(self, info: ProcInfo, sig: int) -> None:
        if info.group_id is None:
            return
        killpg = getattr(os, "killpg", None)
        if not killpg:
            return
        try:
            killpg(info.group_id, sig)
        except ProcessLookupError:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to send signal %s to pgid %s: %s", sig, info.group_id, exc)

    def _command_with_overrides(self, command: List[str]) -> List[str]:
        cmd = list(command)
        if self._python_bin and cmd and cmd[0] in {"python", "python3"}:
            cmd[0] = self._python_bin
        return cmd

    def _rotate_log(self, log_path: Path, service: str) -> None:
        if not log_path.exists():
            return
        try:
            if log_path.stat().st_size == 0:
                log_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                return
        except FileNotFoundError:
            return

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        dest_dir = self.history_dir / service
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"{timestamp}.log"
        log_path.rename(dest_path)

    def _auto_detect_python(self) -> Optional[str]:
        candidates: List[Path] = []

        override_venv = os.getenv(VENV_OVERRIDE_ENV)
        if override_venv:
            candidates.append(Path(override_venv))

        repo_root = Path(__file__).resolve().parents[2]
        search_roots = {repo_root, Path.cwd()}
        for root in search_roots:
            for folder in (".venv", "venv"):
                candidates.append(root / folder)

        seen: set[Path] = set()
        for venv_root in candidates:
            venv_root = venv_root.resolve()
            if venv_root in seen:
                continue
            seen.add(venv_root)
            python_rel = "Scripts/python.exe" if os.name == "nt" else "bin/python"
            candidate = venv_root / python_rel
            if candidate.exists():
                return str(candidate)
        return None


class SystemdServiceManager(BaseServiceManager):
    backend = "systemd"

    def __init__(self) -> None:
        if shutil.which("systemctl") is None:
            raise ServiceManagerError("systemctl not available on this host")

    def status(self, definition: ServiceDefinition) -> ServiceStatus:
        unit = definition.unit_name or f"{definition.name}.service"
        props = self._systemctl_show(unit)
        active_state = props.get("ActiveState", "unknown")
        sub_state = props.get("SubState", "")
        pid_raw = props.get("MainPID", "0")
        started_raw = props.get("ActiveEnterTimestampUSec") or props.get("ExecMainStartTimestampUSec") or "0"

        state: ServiceState
        if active_state == "active":
            state = "active"
        elif active_state == "failed":
            state = "failed"
        elif active_state == "inactive":
            state = "inactive"
        else:
            state = "unknown"

        pid = int(pid_raw) if pid_raw and pid_raw.isdigit() and int(pid_raw) > 0 else None
        started_at: Optional[float] = None
        uptime: Optional[float] = None
        try:
            start_usec = int(started_raw)
            if start_usec > 0:
                started_at = start_usec / 1_000_000
                uptime = max(0.0, time.time() - started_at)
        except ValueError:
            pass

        return ServiceStatus(
            name=definition.name,
            display_name=definition.display_name,
            backend=self.backend,
            state=state,
            pid=pid,
            started_at=started_at,
            uptime_s=uptime,
            info=sub_state or None,
        )

    def start(self, definition: ServiceDefinition) -> ServiceStatus:
        unit = definition.unit_name or f"{definition.name}.service"
        logger.info("systemctl start %s", unit)
        try:
            subprocess.run(["systemctl", "start", unit], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise ServiceManagerError(exc.stderr or exc.stdout or str(exc)) from exc
        return self.status(definition)

    def stop(self, definition: ServiceDefinition) -> ServiceStatus:
        unit = definition.unit_name or f"{definition.name}.service"
        logger.info("systemctl stop %s", unit)
        try:
            subprocess.run(["systemctl", "stop", unit], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise ServiceManagerError(exc.stderr or exc.stdout or str(exc)) from exc
        return self.status(definition)

    def restart(self, definition: ServiceDefinition) -> ServiceStatus:
        unit = definition.unit_name or f"{definition.name}.service"
        logger.info("systemctl restart %s", unit)
        try:
            subprocess.run(["systemctl", "restart", unit], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise ServiceManagerError(exc.stderr or exc.stdout or str(exc)) from exc
        return self.status(definition)

    def _systemctl_show(self, unit: str) -> Dict[str, str]:
        cmd = [
            "systemctl",
            "show",
            unit,
            "--property=ActiveState,SubState,MainPID,ActiveEnterTimestampUSec,ExecMainStartTimestampUSec",
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise ServiceManagerError(exc.stderr or exc.stdout or str(exc)) from exc

        props: Dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
        return props
