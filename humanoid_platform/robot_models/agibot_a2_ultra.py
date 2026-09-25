"""Agibot A2 Ultra robot model spec."""

from humanoid_platform.types import (
    AudioBridgeMode,
    AudioBridgeSpec,
    PlatformId,
    RobotModelId,
    RobotModelSpec,
)


AGIBOT_A2_ULTRA_MODEL = RobotModelSpec(
    id=RobotModelId.AGIBOT_A2_ULTRA,
    platform=PlatformId.AGIBOT,
    display_name="Agibot A2 Ultra",
    audio_bridge=AudioBridgeSpec(
        mode=AudioBridgeMode.LOCAL,
        notes=(
            "Supervisor and audio bridge run on the robot development PC.",
        ),
    ),
    notes=(
        "AIMA EM audio ownership is handled by the local audio bridge service on PC2.",
    ),
)
