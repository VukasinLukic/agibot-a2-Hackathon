"""Registry for built-in platform and robot model specs."""

from __future__ import annotations

from humanoid_platform.platforms import AGIBOT_PLATFORM, UNITREE_PLATFORM
from humanoid_platform.robot_models import (
    AGIBOT_A2_ULTRA_MODEL,
    AGIBOT_X2_ULTRA_MODEL,
    UNITREE_G1_EDU_MODEL,
)
from humanoid_platform.types import PlatformId, PlatformSpec, RobotModelId, RobotModelSpec


_PLATFORMS: dict[PlatformId, PlatformSpec] = {
    AGIBOT_PLATFORM.id: AGIBOT_PLATFORM,
    UNITREE_PLATFORM.id: UNITREE_PLATFORM,
}

_ROBOT_MODELS: dict[RobotModelId, RobotModelSpec] = {
    AGIBOT_A2_ULTRA_MODEL.id: AGIBOT_A2_ULTRA_MODEL,
    AGIBOT_X2_ULTRA_MODEL.id: AGIBOT_X2_ULTRA_MODEL,
    UNITREE_G1_EDU_MODEL.id: UNITREE_G1_EDU_MODEL,
}


def list_platforms() -> tuple[PlatformSpec, ...]:
    """Return all known platform specs."""

    return tuple(_PLATFORMS.values())


def get_platform(platform_id: PlatformId | str) -> PlatformSpec:
    """Return a platform spec by canonical ID."""

    return _PLATFORMS[PlatformId(platform_id)]


def list_robot_models() -> tuple[RobotModelSpec, ...]:
    """Return all known robot model specs."""

    return tuple(_ROBOT_MODELS.values())


def get_robot_model(model_id: RobotModelId | str) -> RobotModelSpec:
    """Return a robot model spec by canonical ID."""

    return _ROBOT_MODELS[RobotModelId(model_id)]


def robot_models_for_platform(platform_id: PlatformId | str) -> tuple[RobotModelSpec, ...]:
    """Return robot model specs for one platform."""

    platform = PlatformId(platform_id)
    return tuple(model for model in _ROBOT_MODELS.values() if model.platform == platform)
