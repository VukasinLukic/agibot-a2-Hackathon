"""Typed loading for the top-level supervisor config.

The new top-level sections are intentionally small. Legacy service definitions
remain raw data until each service is migrated deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from humanoid_platform import PlatformId, RobotModelId, get_robot_model


_SUPERVISOR_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = _SUPERVISOR_ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = _SUPERVISOR_ROOT / "config.example.yaml"
SERVICE_ROBOT_CONTEXT_KEY = "_supervisor_robot"


class SupervisorConfigError(ValueError):
    """Raised when supervisor config is missing or invalid."""


class RobotConfig(BaseModel):
    """Identity and behavior-selecting robot config."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    location: str | None = None
    platform: PlatformId
    model: RobotModelId

    @model_validator(mode="after")
    def validate_model_platform(self) -> "RobotConfig":
        robot_model = get_robot_model(self.model)
        if robot_model.platform != self.platform:
            raise ValueError(
                f"robot.model '{self.model.value}' belongs to platform "
                f"'{robot_model.platform.value}', not '{self.platform.value}'"
            )
        configured_name = (self.name or "").strip()
        self.name = configured_name or robot_model.display_name
        return self

    def to_service_context(self) -> dict[str, str]:
        """Return the minimal robot identity passed to migrated services."""

        return {
            "id": self.id,
            "name": self.name or get_robot_model(self.model).display_name,
            "platform": self.platform.value,
            "model": self.model.value,
        }


class ApiConfig(BaseModel):
    """Supervisor API bind settings."""

    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    token: str | None = None


class AudioDeviceDefaults(BaseModel):
    """Optional preferred audio devices."""

    model_config = ConfigDict(extra="forbid")

    default_microphone: str | int | None = None
    default_speakers: str | int | None = None


class CameraDeviceDefaults(BaseModel):
    """Optional preferred camera device."""

    model_config = ConfigDict(extra="forbid")

    default_device: str | int | None = None


class DeviceDefaults(BaseModel):
    """Optional preferred local devices."""

    model_config = ConfigDict(extra="forbid")

    audio: AudioDeviceDefaults = Field(default_factory=AudioDeviceDefaults)
    camera: CameraDeviceDefaults = Field(default_factory=CameraDeviceDefaults)


class SupervisorConfig(BaseModel):
    """Top-level supervisor config.

    `services`, `health_monitoring`, `engagement`, and `command_presets` are
    legacy sections owned by existing supervisor code. They remain raw data.
    """

    model_config = ConfigDict(extra="forbid")

    robot: RobotConfig
    api: ApiConfig = Field(default_factory=ApiConfig)
    devices: DeviceDefaults = Field(default_factory=DeviceDefaults)
    services: list[dict[str, Any]] = Field(default_factory=list)
    health_monitoring: dict[str, Any] = Field(default_factory=dict)
    engagement: dict[str, Any] = Field(default_factory=dict)
    command_presets: list[dict[str, Any]] = Field(default_factory=list)


@dataclass(frozen=True)
class LoadedSupervisorConfig:
    path: Path
    using_example: bool
    config: SupervisorConfig


def get_startup_config_path() -> tuple[Path, bool]:
    """Return the config path used by supervisor startup."""

    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH, False
    return EXAMPLE_CONFIG_PATH, True


def load_supervisor_config(path: Path | str) -> SupervisorConfig:
    """Load and validate a supervisor config file."""

    config_path = Path(path)
    if not config_path.exists():
        raise SupervisorConfigError(f"Supervisor config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SupervisorConfigError(f"Failed to parse supervisor config {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise SupervisorConfigError(f"Supervisor config {config_path} must be a YAML object")

    try:
        return SupervisorConfig.model_validate(raw)
    except ValidationError as exc:
        raise SupervisorConfigError(f"Invalid supervisor config {config_path}: {exc}") from exc


def load_startup_config() -> LoadedSupervisorConfig:
    """Load the config used by normal supervisor startup."""

    config_path, using_example = get_startup_config_path()
    return LoadedSupervisorConfig(
        path=config_path,
        using_example=using_example,
        config=load_supervisor_config(config_path),
    )
