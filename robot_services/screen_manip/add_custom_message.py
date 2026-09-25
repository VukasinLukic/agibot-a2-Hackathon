"""Show a custom message on the robot's head screen for a set amount of time.

A single entry point that takes a message and holds it on screen for the
requested duration, then puts the default face back. For example, when asked
for the time of day the robot says what time it is and flashes the same time on
screen while it speaks.

The head screen is model-specific, so the backend is selected through
``humanoid_platform`` rather than assumed. On a robot model with no head screen
backend this disables itself cleanly and the caller just loses the visual.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from humanoid_platform import HeadScreenBackend, RobotModelId, get_head_screen_spec

LOG = logging.getLogger("screen_manip")

DEFAULT_MESSAGE_DURATION_S = 2.0

_controller: Any | None = None
_controller_lock = threading.Lock()
_controller_disabled = False


def _resolve_robot_model_id() -> RobotModelId | None:
    raw = os.getenv("HUMANOID_ROBOT_MODEL") or os.getenv("ROBOT_MODEL")
    if raw is None or not raw.strip():
        # This module only exists for the Agibot head screen, so an unset model
        # means "the robot we are running on" rather than the Unitree default
        # used elsewhere in the stack.
        return RobotModelId.AGIBOT_A2_ULTRA
    try:
        return RobotModelId(raw.strip())
    except ValueError:
        LOG.warning("Unknown robot model %r; head screen disabled.", raw)
        return None


def get_screen_controller() -> Any | None:
    """Return the head screen controller for this robot model, if any."""

    global _controller, _controller_disabled

    if _controller is not None:
        return _controller
    if _controller_disabled:
        return None

    with _controller_lock:
        if _controller is not None:
            return _controller
        if _controller_disabled:
            return None

        model_id = _resolve_robot_model_id()
        if model_id is None:
            _controller_disabled = True
            return None

        spec = get_head_screen_spec(model_id)
        if spec is None:
            LOG.info(
                "Head screen disabled: robot model '%s' has no head screen backend.",
                model_id.value,
            )
            _controller_disabled = True
            return None

        if spec.backend != HeadScreenBackend.AGIBOT_EMOTICON_PLAYER:
            LOG.warning("Head screen backend unsupported: %s", spec.backend.value)
            _controller_disabled = True
            return None

        try:
            from robot_services.screen_manip.emoticon_screen import (
                AgibotEmoticonScreenController,
            )

            _controller = AgibotEmoticonScreenController()
        except Exception as exc:
            LOG.warning("Head screen controller init failed: %s", exc)
            _controller_disabled = True
            return None

        return _controller


def show_message(
    message: str,
    subtitle: str = "",
    *,
    duration_s: float = DEFAULT_MESSAGE_DURATION_S,
) -> dict[str, Any]:
    """Hold ``message`` on the head screen for ``duration_s``, then restore.

    Blocks for the duration of the message. Never raises: a screen that cannot
    be driven is reported in the returned status instead.
    """

    controller = get_screen_controller()
    if controller is None:
        return {"status": "skipped", "reason": "head_screen_unavailable"}

    try:
        return controller.flash_text(message, subtitle, duration_s=duration_s)
    except Exception as exc:
        LOG.warning("Head screen message failed for %r: %s", message, exc)
        return {"status": "error", "reason": str(exc)}


async def show_message_async(
    message: str,
    subtitle: str = "",
    *,
    duration_s: float = DEFAULT_MESSAGE_DURATION_S,
) -> dict[str, Any]:
    """``show_message`` off the event loop, for use from async callers."""

    return await asyncio.to_thread(
        show_message, message, subtitle, duration_s=duration_s
    )


def restore_default_face() -> bool:
    """Put the robot's configured default face back on screen."""

    controller = get_screen_controller()
    if controller is None:
        return False
    try:
        return bool(controller.restore_default_face())
    except Exception as exc:
        LOG.warning("Default face restore failed: %s", exc)
        return False


def provision_screen() -> int | None:
    """Pre-create the reusable screen slot so the first message is fast."""

    controller = get_screen_controller()
    if controller is None:
        return None
    try:
        return controller.provision()
    except Exception as exc:
        LOG.warning("Head screen provisioning failed: %s", exc)
        return None


__all__ = [
    "DEFAULT_MESSAGE_DURATION_S",
    "get_screen_controller",
    "provision_screen",
    "restore_default_face",
    "show_message",
    "show_message_async",
]
