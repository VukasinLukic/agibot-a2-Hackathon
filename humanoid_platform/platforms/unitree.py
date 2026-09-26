"""Unitree platform metadata."""

from humanoid_platform.types import PlatformId, PlatformSpec


UNITREE_PLATFORM = PlatformSpec(
    id=PlatformId.UNITREE,
    display_name="Unitree",
    sdk_modules=("unitree_sdk2py",),
    notes=(
        "Uses Unitree SDK2 Python bindings and DDS interface selection.",
        "G1 Edu reference implementation currently comes from the finished client project.",
    ),
)
