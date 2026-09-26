"""Small, dependency-free helpers for the opt-in English voice canary."""

from __future__ import annotations

import os


_TRUE_VALUES = {"1", "true", "yes", "on"}


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


def voice_canary_enabled() -> bool:
    return env_flag("VOICE_CANARY_ENABLED", False)


def voice_language() -> str:
    if not voice_canary_enabled():
        return "sr"
    return (os.getenv("VOICE_LANGUAGE") or "en").strip().lower() or "en"


def voice_locale() -> str:
    if not voice_canary_enabled():
        return "sr-RS"
    return (os.getenv("VOICE_LOCALE") or "en-US").strip() or "en-US"


def english_voice_enabled() -> bool:
    return voice_language().startswith("en")
