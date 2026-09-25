"""Visual UI support map for registered robot models."""

from __future__ import annotations

from humanoid_platform.registry import get_robot_model
from humanoid_platform.types import (
    RobotModelId,
    RobotModelSpec,
    VisualUiBackend,
    VisualUiSpec,
)


_VISUAL_UI_SPECS: dict[RobotModelId, VisualUiSpec] = {
    RobotModelId.UNITREE_G1_EDU: VisualUiSpec(
        backend=VisualUiBackend.UNITREE_G1_AUDIO_LED,
        notes=("Maps LiveKit agent state to Unitree G1 AudioClient.LedControl RGB values.",),
    ),
}


def get_visual_ui_spec(robot_model: RobotModelId | str | RobotModelSpec) -> VisualUiSpec | None:
    """Return visual UI support for a robot model, if available."""

    if isinstance(robot_model, RobotModelSpec):
        model_id = robot_model.id
    else:
        model_id = RobotModelId(robot_model)

    get_robot_model(model_id)
    return _VISUAL_UI_SPECS.get(model_id)


def visual_ui_supported_models() -> tuple[RobotModelId, ...]:
    """Return robot models that currently support visual UI control."""

    return tuple(_VISUAL_UI_SPECS)
