from __future__ import annotations

import os


def robot_enabled() -> bool:
    """Whether gesture backends should actually talk to hardware.

    Shared by every gesture controller so ``AUDIO_TARGET=host`` /
    ``ROBOT_ENABLE=0`` consistently no-ops gestures the same way it does for
    audio, instead of each backend re-implementing the same env checks.
    """

    target = os.getenv("AUDIO_TARGET", "robot").strip().lower()
    if target == "host":
        return False
    disable = os.getenv("ROBOT_ENABLE", "").strip().lower()
    if disable in {"0", "false", "no", "off"}:
        return False
    return True


__all__ = ["robot_enabled"]
