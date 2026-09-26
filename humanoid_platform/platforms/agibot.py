"""Agibot platform metadata."""

from humanoid_platform.types import PlatformId, PlatformSpec


AGIBOT_PLATFORM = PlatformSpec(
    id=PlatformId.AGIBOT,
    display_name="Agibot",
    sdk_modules=("aimdk_msgs",),
    notes=(
        "Uses AIMDK/AIMA-family runtime components.",
        "Agibot preflight may need to stop hal_audio, aima, or aima_em before taking over resources.",
    ),
)
