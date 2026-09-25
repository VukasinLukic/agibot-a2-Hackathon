"""Unitree G1 Edu robot model spec."""

from humanoid_platform.types import (
    AudioBridgeMode,
    AudioBridgeSpec,
    PlatformId,
    RobotModelId,
    RobotModelSpec,
)


UNITREE_G1_EDU_MODEL = RobotModelSpec(
    id=RobotModelId.UNITREE_G1_EDU,
    platform=PlatformId.UNITREE,
    display_name="Unitree G1 Edu",
    audio_bridge=AudioBridgeSpec(
        mode=AudioBridgeMode.LOCAL,
        notes=(
            "Supervisor and audio bridge run on the robot development PC.",
        ),
    ),
    notes=(
        "Current implementation should be treated as a legacy/reference model until a new G1 is available.",
    ),
)
