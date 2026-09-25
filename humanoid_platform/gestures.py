"""Gesture support map for registered robot models."""

from __future__ import annotations

from humanoid_platform.registry import get_robot_model
from humanoid_platform.types import (
    GestureBackend,
    GestureSpec,
    RobotModelId,
    RobotModelSpec,
)


_GESTURE_SPECS: dict[RobotModelId, GestureSpec] = {
    RobotModelId.UNITREE_G1_EDU: GestureSpec(
        backend=GestureBackend.UNITREE_G1_ARM_ACTIONS,
        catalog_id="unitree_g1_edu",
        notes=("Executes Unitree G1 arm action IDs through unitree_sdk2py.",),
    ),
    RobotModelId.AGIBOT_A2_ULTRA: GestureSpec(
        backend=GestureBackend.AGIBOT_A2_MOTION_PLAYER,
        catalog_id="agibot_a2_ultra",
        notes=(
            "Plays curated AIMA preset motions through the native "
            "MotionCommandService/ResourceService HTTP JSON-RPC interfaces.",
        ),
    ),
}


def get_gesture_spec(robot_model: RobotModelId | str | RobotModelSpec) -> GestureSpec | None:
    """Return gesture support for a robot model, if available."""

    if isinstance(robot_model, RobotModelSpec):
        model_id = robot_model.id
    else:
        model_id = RobotModelId(robot_model)

    get_robot_model(model_id)
    return _GESTURE_SPECS.get(model_id)


def gesture_supported_models() -> tuple[RobotModelId, ...]:
    """Return robot models that currently support gesture execution."""

    return tuple(_GESTURE_SPECS)

