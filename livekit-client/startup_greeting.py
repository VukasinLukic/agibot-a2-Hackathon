"""Startup-only capture gate that prevents the robot greeting from entering STT."""

from __future__ import annotations

import asyncio
from typing import Any


async def speak_startup_greeting(
    session: Any,
    text: str,
    *,
    capture_gate_enabled: bool,
    tail_ms: int = 120,
) -> None:
    if not capture_gate_enabled:
        await session.say(text=text, allow_interruptions=True)
        return

    session.input.set_audio_enabled(False)
    try:
        await session.say(text=text, allow_interruptions=False)
        if tail_ms > 0:
            await asyncio.sleep(tail_ms / 1000.0)
    finally:
        session.input.set_audio_enabled(True)
