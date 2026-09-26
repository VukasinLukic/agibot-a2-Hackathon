"""Speak a short phrase for IGRA detection tests (default: RADI)."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import time
from typing import Optional

logger = logging.getLogger("igra.announce")

_last_spoken_at = 0.0
_last_text = ""


def _cooldown_ok(text: str, cooldown_s: float) -> bool:
    global _last_spoken_at, _last_text
    now = time.time()
    if text == _last_text and (now - _last_spoken_at) < cooldown_s:
        return False
    _last_spoken_at = now
    _last_text = text
    return True


def speak_local(text: str) -> bool:
    """Best-effort local TTS for laptop / robot host without LiveKit."""
    phrase = (text or "").strip()
    if not phrase:
        return False

    system = platform.system().lower()
    try:
        if system == "darwin" and shutil.which("say"):
            subprocess.Popen(
                ["say", phrase],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Local say: %s", phrase)
            return True
        if shutil.which("espeak-ng"):
            subprocess.Popen(
                ["espeak-ng", phrase],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Local espeak-ng: %s", phrase)
            return True
        if shutil.which("espeak"):
            subprocess.Popen(
                ["espeak", phrase],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Local espeak: %s", phrase)
            return True
    except Exception as exc:
        logger.warning("Local TTS failed: %s", exc)
        return False

    logger.info("No local TTS binary; logged only: %s", phrase)
    return False


async def speak_via_agent(text: str, room: Optional[str] = None) -> bool:
    """Send plain text to the LiveKit agent if credentials/room are available."""
    phrase = (text or "").strip()
    if not phrase:
        return False

    livekit_url = os.getenv("LIVEKIT_URL")
    if not livekit_url or not os.getenv("LIVEKIT_API_KEY") or not os.getenv("LIVEKIT_API_SECRET"):
        return False

    target_room = (
        room
        or os.getenv("IGRA_LIVEKIT_ROOM")
        or os.getenv("LIVEKIT_ROOM")
        or "main-room"
    )

    try:
        # Import lazily so laptop-only runs do not require full supervisor deps.
        from robot_supervisor_v2.app.models.conversation import AgentCommandRequest
        from robot_supervisor_v2.app.utils.agent_commands import send_agent_command
    except Exception as exc:
        logger.debug("Agent command import unavailable: %s", exc)
        return False

    try:
        await send_agent_command(
            AgentCommandRequest(text=phrase, plain_text=False),
            room=target_room,
        )
        logger.info("Agent announce in room %s: %s", target_room, phrase)
        return True
    except Exception as exc:
        logger.warning("Agent announce failed: %s", exc)
        return False


async def announce(text: str = "RADI", *, cooldown_s: float | None = None) -> dict:
    """
    Announce detection success.

    Modes (IGRA_ANNOUNCE_MODE):
      - local: host TTS only
      - agent: LiveKit agent command only
      - both: try agent, always also try local (default for hackathon tests)
    """
    phrase = (text or "RADI").strip() or "RADI"
    cooldown = float(
        cooldown_s
        if cooldown_s is not None
        else os.getenv("IGRA_ANNOUNCE_COOLDOWN_S", "2.5")
    )
    if not _cooldown_ok(phrase, cooldown):
        return {"spoken": False, "reason": "cooldown", "text": phrase}

    mode = (os.getenv("IGRA_ANNOUNCE_MODE") or "both").strip().lower()
    agent_ok = False
    local_ok = False

    if mode in ("agent", "both"):
        agent_ok = await speak_via_agent(phrase)
    if mode in ("local", "both") or (mode == "agent" and not agent_ok):
        local_ok = speak_local(phrase)

    return {
        "spoken": bool(agent_ok or local_ok),
        "agent": agent_ok,
        "local": local_ok,
        "mode": mode,
        "text": phrase,
    }


def announce_sync(text: str = "RADI", *, cooldown_s: float | None = None) -> dict:
    """Thread-safe wrapper for the camera loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Camera thread: run a fresh loop.
            return asyncio.run(announce(text, cooldown_s=cooldown_s))
        return loop.run_until_complete(announce(text, cooldown_s=cooldown_s))
    except RuntimeError:
        return asyncio.run(announce(text, cooldown_s=cooldown_s))
