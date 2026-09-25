"""Temperature monitor support map for registered robot models."""

from __future__ import annotations

from humanoid_platform.registry import get_robot_model
from humanoid_platform.types import (
    RobotModelId,
    RobotModelSpec,
    TemperatureMonitorBackend,
    TemperatureMonitorSpec,
)


_TEMPERATURE_MONITOR_SPECS: dict[RobotModelId, TemperatureMonitorSpec] = {
    RobotModelId.UNITREE_G1_EDU: TemperatureMonitorSpec(
        backend=TemperatureMonitorBackend.UNITREE_LOWSTATE,
        notes=("Reads Unitree SDK2 LowState_ motor temperatures from rt/lowstate.",),
    ),
}


def get_temperature_monitor_spec(
    robot_model: RobotModelId | str | RobotModelSpec,
) -> TemperatureMonitorSpec | None:
    """Return temperature monitor support for a robot model, if available."""

    if isinstance(robot_model, RobotModelSpec):
        model_id = robot_model.id
    else:
        model_id = RobotModelId(robot_model)

    get_robot_model(model_id)
    return _TEMPERATURE_MONITOR_SPECS.get(model_id)


def temperature_monitor_supported_models() -> tuple[RobotModelId, ...]:
    """Return robot models that currently support temperature monitoring."""

    return tuple(_TEMPERATURE_MONITOR_SPECS)
