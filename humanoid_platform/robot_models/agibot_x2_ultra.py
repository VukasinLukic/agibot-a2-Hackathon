"""Agibot X2 Ultra robot model spec."""

from humanoid_platform.types import (
    AudioBridgeMode,
    AudioBridgeSpec,
    PlatformId,
    RobotModelId,
    RobotModelSpec,
)


AGIBOT_X2_ULTRA_MODEL = RobotModelSpec(
    id=RobotModelId.AGIBOT_X2_ULTRA,
    platform=PlatformId.AGIBOT,
    display_name="Agibot X2 Ultra",
    audio_bridge=AudioBridgeSpec(
        mode=AudioBridgeMode.REMOTE,
        notes=(
            "Supervisor runs on the robot development PC.",
            "Audio bridge runs remotely because X2 audio devices are owned by PC3.",
        ),
    ),
    notes=(
        "AIMA EM audio ownership is handled by the PC3 remote audio bridge manager.",
    ),
)
