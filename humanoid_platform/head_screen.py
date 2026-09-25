"""Head screen support map for registered robot models."""

from __future__ import annotations

from humanoid_platform.registry import get_robot_model
from humanoid_platform.types import (
    HeadScreenBackend,
    HeadScreenSpec,
    RobotModelId,
    RobotModelSpec,
)


_HEAD_SCREEN_SPECS: dict[RobotModelId, HeadScreenSpec] = {
    RobotModelId.AGIBOT_A2_ULTRA: HeadScreenSpec(
        backend=HeadScreenBackend.AGIBOT_EMOTICON_PLAYER,
        notes=(
            "Renders a short clip and plays it through "
            "RcEmoticonPlayerService/PlayerEmoticon. See docs/agibot/head_screen.md.",
        ),
    ),
}


def get_head_screen_spec(
    robot_model: RobotModelId | str | RobotModelSpec,
) -> HeadScreenSpec | None:
    """Return head screen support for a robot model, if available."""

    if isinstance(robot_model, RobotModelSpec):
        model_id = robot_model.id
    else:
        model_id = RobotModelId(robot_model)

    get_robot_model(model_id)
    return _HEAD_SCREEN_SPECS.get(model_id)


def head_screen_supported_models() -> tuple[RobotModelId, ...]:
    """Return robot models that currently support head screen content."""

    return tuple(_HEAD_SCREEN_SPECS)
