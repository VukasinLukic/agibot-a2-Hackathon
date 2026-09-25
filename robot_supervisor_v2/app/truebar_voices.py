"""Truebar TTS voice discovery for supervisor speech configuration."""

from __future__ import annotations

from typing import Any

import aiohttp
from livekit.plugins import truebar


def voice_label_from_tag(tts_tag: str) -> str:
    parts = [part for part in tts_tag.split(":") if part]
    if len(parts) >= 3:
        name = parts[2].replace("_", " ").replace("-", " ").title()
        language = parts[1]
        return f"{name} ({language})"
    return tts_tag


def normalize_voice_options(voices: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen_tags: set[str] = set()
    for voice in voices:
        tag = str(voice.get("tts_tag") or voice.get("tag") or "").strip()
        if not tag or tag in seen_tags:
            continue
        label = str(voice.get("label") or "").strip() or voice_label_from_tag(tag)
        normalized.append({"label": label, "tts_tag": tag})
        seen_tags.add(tag)
    return normalized


async def fetch_truebar_tts_voices() -> list[dict[str, str]]:
    settings = truebar.load_settings_from_env()
    manager = truebar.Util.token_manager(settings=settings)

    async with aiohttp.ClientSession() as session:
        access_token = await manager.get_access_token(session=session, timeout=8)
        stages = await truebar.Util.get_stage_listing(
            settings=settings,
            session=session,
            access_token=access_token,
        )

    voices: list[dict[str, Any]] = []
    for stage in stages or []:
        if str(stage.get("task", "")).lower() != "tts":
            continue
        for option in stage.get("configOptions", []):
            tag = str(option.get("tag") or "").strip()
            if tag:
                voices.append({"label": voice_label_from_tag(tag), "tts_tag": tag})

    return normalize_voice_options(voices)
