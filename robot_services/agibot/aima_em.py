"""AIMA EM service-resource helper for Agibot runtimes."""

from __future__ import annotations

import subprocess
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgibotAimaConfig(BaseModel):
    """Optional AIMA EM resource-management configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    command: str = "aima"
    default_mode: str = "audio_bridge"
    reconcile_on_manager_start: bool = True
    auto_stop_before_bridge_start: bool = False
    bridge_mode_stop_apps: list[str] = Field(default_factory=lambda: ["agent", "hal_audio"])
    agibot_mode_start_apps: list[str] = Field(default_factory=lambda: ["hal_audio", "agent"])
    command_timeout_seconds: float = 8.0


class AgibotAimaManager:
    """Small subprocess wrapper around ``aima em`` for Agibot audio ownership."""

    def __init__(self, config: AgibotAimaConfig):
        self.config = config
        self.current_mode = "disabled" if not config.enabled else "unknown"
        self.last_results: list[dict[str, Any]] = []
        self.last_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "current_mode": self.current_mode,
            "configured_apps": {
                "bridge_mode_stop_apps": list(self.config.bridge_mode_stop_apps),
                "agibot_mode_start_apps": list(self.config.agibot_mode_start_apps),
            },
            "last_results": list(self.last_results),
            "last_error": self.last_error,
        }

    def reconcile_on_startup(self) -> None:
        if not self.config.enabled or not self.config.reconcile_on_manager_start:
            return
        if self.config.default_mode == "audio_bridge":
            self.switch_audio_bridge()
            return
        if self.config.default_mode == "agibot":
            self.switch_agibot()
            return
        self.last_error = f"Unsupported AIMA default_mode: {self.config.default_mode}"
        raise RuntimeError(self.last_error)

    def ensure_bridge_start_allowed(self) -> None:
        if not self.config.enabled:
            return
        if self.current_mode == "audio_bridge":
            return
        if self.config.auto_stop_before_bridge_start:
            self.switch_audio_bridge()
            return
        raise RuntimeError(
            f"AIMA is in {self.current_mode} mode; switch to audio_bridge mode first"
        )

    def switch_audio_bridge(self) -> dict[str, Any]:
        if not self.config.enabled:
            return self.status()
        self._run_app_commands("stop-app", self.config.bridge_mode_stop_apps)
        self.current_mode = "audio_bridge"
        self.last_error = None
        return self.status()

    def switch_agibot(self) -> dict[str, Any]:
        if not self.config.enabled:
            return self.status()
        self._run_app_commands("start-app", self.config.agibot_mode_start_apps)
        self.current_mode = "agibot"
        self.last_error = None
        return self.status()

    def doctor(self) -> dict[str, Any]:
        result = self._run_em_command(["doctor"])
        return {
            "result": result,
            "status": self.status(),
        }

    def _run_app_commands(self, action: str, apps: list[str]) -> None:
        for app in apps:
            self._run_em_command([action, app])

    def _run_em_command(self, args: list[str]) -> dict[str, Any]:
        command = [self.config.command, "em", *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
                check=False,
            )
            result = {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "command": command,
                "returncode": None,
                "stdout": self._output_text(exc.stdout),
                "stderr": self._output_text(exc.stderr),
                "timed_out": True,
            }
            self.last_results.append(result)
            self.last_error = f"AIMA command timed out: {' '.join(command)}"
            raise RuntimeError(self.last_error) from exc
        except OSError as exc:
            result = {
                "command": command,
                "returncode": None,
                "stdout": "",
                "stderr": str(exc),
                "timed_out": False,
            }
            self.last_results.append(result)
            self.last_error = f"AIMA command failed to execute: {' '.join(command)}: {exc}"
            raise RuntimeError(self.last_error) from exc

        self.last_results.append(result)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
            self.last_error = f"AIMA command failed: {' '.join(command)}: {detail}"
            raise RuntimeError(self.last_error)
        return result

    @staticmethod
    def _output_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

