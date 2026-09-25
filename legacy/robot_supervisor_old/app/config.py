from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

from dotenv import load_dotenv

load_dotenv()

CONFIG_ENV_VAR = "ROBOT_SUPERVISOR_CONFIG"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.example.yaml"


class ServiceModeDefinition(BaseModel):
    name: str
    display_name: Optional[str] = None
    command: Optional[List[str]] = None
    env: Dict[str, str] = Field(default_factory=dict)


class ServiceDefinition(BaseModel):
    name: str
    display_name: Optional[str] = None
    backend: Optional[Literal["systemd", "process"]] = None
    unit_name: Optional[str] = None
    command: Optional[List[str]] = None
    working_dir: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    ready_after_seconds: float = 0.0
    default_mode: Optional[str] = None
    modes: List[ServiceModeDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_backend(self) -> "ServiceDefinition":
        backend = self.backend
        if backend == "process" and not self.command:
            if not self.modes:
                raise ValueError("process backend requires a command list or per-mode commands")
            missing = [mode.name for mode in self.modes if not mode.command]
            if missing:
                raise ValueError(
                    f"process backend requires command overrides for modes without default command: {missing}"
                )
        if backend == "systemd" and not self.unit_name:
            self.unit_name = f"{self.name}.service"
        if self.modes:
            mode_names = {mode.name for mode in self.modes}
            if self.default_mode and self.default_mode not in mode_names:
                raise ValueError(f"default_mode '{self.default_mode}' not defined in modes for {self.name}")
        return self

    @property
    def initial_mode(self) -> Optional[str]:
        if not self.modes:
            return None
        if self.default_mode:
            return self.default_mode
        return self.modes[0].name

    def mode_definition(self, mode_name: Optional[str]) -> Optional[ServiceModeDefinition]:
        if not mode_name:
            return None
        for mode in self.modes:
            if mode.name == mode_name:
                return mode
        return None

    def with_mode(self, mode_name: Optional[str]) -> "ServiceDefinition":
        copy = self.model_copy(deep=True)
        if not mode_name:
            return copy
        mode = self.mode_definition(mode_name)
        if not mode:
            raise ValueError(f"Unknown mode '{mode_name}' for service '{self.name}'")
        if mode.command:
            copy.command = list(mode.command)
        if mode.env:
            merged_env = copy.env.copy()
            merged_env.update(mode.env)
            copy.env = merged_env
        return copy


class SupervisorConfig(BaseModel):
    default_backend: Literal["systemd", "process"] = "systemd"
    process_log_dir: Optional[str] = None
    services: List[ServiceDefinition]


def _resolve_path(base_dir: Path, maybe_path: Optional[str]) -> Optional[str]:
    if not maybe_path:
        return maybe_path
    path = Path(maybe_path)
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def load_config(explicit_path: Optional[str] = None) -> SupervisorConfig:
    """Load configuration from YAML, allowing overrides via environment."""

    path_str = explicit_path or os.getenv(CONFIG_ENV_VAR) or str(DEFAULT_CONFIG_PATH)
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"Supervisor config not found at {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    if "services" not in raw:
        raise ValueError("Config must define at least one service")

    base_dir = path.parent
    for svc in raw.get("services", []):
        svc["working_dir"] = _resolve_path(base_dir, svc.get("working_dir"))
    raw["process_log_dir"] = _resolve_path(base_dir, raw.get("process_log_dir"))

    return SupervisorConfig(**raw)


def load_services_map(config: SupervisorConfig) -> Dict[str, ServiceDefinition]:
    return {svc.name: svc for svc in config.services}
