from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import AsyncIterator, Dict

from ..config import SupervisorConfig, load_services_map, ServiceDefinition


class LogProviderError(RuntimeError):
    """Raised when logs cannot be retrieved for a service."""


class BaseLogProvider:
    backend: str

    def tail(self, definition: ServiceDefinition, lines: int) -> str:
        raise NotImplementedError

    async def stream(self, definition: ServiceDefinition) -> AsyncIterator[str]:
        raise NotImplementedError


class ProcessLogProvider(BaseLogProvider):
    backend = "process"

    def __init__(self, log_dir: str | None) -> None:
        self.log_dir = Path(log_dir or (Path.cwd() / "logs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tail(self, definition: ServiceDefinition, lines: int) -> str:
        path = self._log_path(definition.name)
        if not path.exists():
            return ""
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
        return "".join(content[-lines:])

    async def stream(self, definition: ServiceDefinition) -> AsyncIterator[str]:
        path = self._log_path(definition.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                line = handle.readline()
                if line:
                    yield line.rstrip("\n")
                else:
                    await asyncio.sleep(0.5)

    def _log_path(self, service: str) -> Path:
        return self.log_dir / f"{service}.log"


class SystemdLogProvider(BaseLogProvider):
    backend = "systemd"

    def __init__(self) -> None:
        if shutil.which("journalctl") is None:
            raise LogProviderError("journalctl not available on this host")

    def tail(self, definition: ServiceDefinition, lines: int) -> str:
        unit = definition.unit_name or f"{definition.name}.service"
        cmd = ["journalctl", "-u", unit, "-n", str(lines), "--no-pager"]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise LogProviderError(exc.stderr or exc.stdout or str(exc)) from exc
        return result.stdout

    async def stream(self, definition: ServiceDefinition) -> AsyncIterator[str]:
        unit = definition.unit_name or f"{definition.name}.service"
        cmd = [
            "journalctl",
            "-u",
            unit,
            "-f",
            "-n",
            "0",
            "-o",
            "short",
            "--no-pager",
        ]
        if shutil.which("journalctl") is None:
            raise LogProviderError("journalctl not available on this host")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                yield line.decode("utf-8", errors="replace").rstrip("\n")
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()


class LogController:
    def __init__(self, config: SupervisorConfig) -> None:
        self._config = config
        self._services = load_services_map(config)
        self._providers: Dict[str, BaseLogProvider] = {}
        self._prepare_providers()

    def tail(self, service: str, lines: int) -> str:
        definition = self._get_definition(service)
        provider = self._provider_for(definition)
        return provider.tail(definition, lines)

    async def stream(self, service: str) -> AsyncIterator[str]:
        definition = self._get_definition(service)
        provider = self._provider_for(definition)
        async for line in provider.stream(definition):
            yield line

    def _get_definition(self, name: str) -> ServiceDefinition:
        if name not in self._services:
            raise LogProviderError(f"Unknown service '{name}'")
        return self._services[name]

    def _provider_for(self, definition: ServiceDefinition) -> BaseLogProvider:
        backend = definition.backend or self._config.default_backend
        provider = self._providers.get(backend)
        if not provider:
            raise LogProviderError(f"Log backend '{backend}' unavailable")
        return provider

    def _prepare_providers(self) -> None:
        backends = { (svc.backend or self._config.default_backend) for svc in self._services.values() }
        if "process" in backends:
            self._providers["process"] = ProcessLogProvider(self._config.process_log_dir)
        if "systemd" in backends:
            try:
                self._providers["systemd"] = SystemdLogProvider()
            except LogProviderError:
                # If systemd logging is unavailable we leave provider missing; calls will raise.
                pass
