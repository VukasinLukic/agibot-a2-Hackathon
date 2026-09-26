"""Read-only locale overlays for canary-only spoken content."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .voice_canary import english_voice_enabled, voice_locale


_ROOT = Path(__file__).resolve().parent / "locales"


def load_locale_resource(name: str) -> dict[str, Any]:
    if not english_voice_enabled():
        return {}
    locale = voice_locale()
    path = _ROOT / locale / f"{name}.yaml"
    if not path.is_file() and locale.lower().startswith("en"):
        path = _ROOT / "en-US" / f"{name}.yaml"
    if not path.is_file():
        raise RuntimeError(f"Missing voice locale resource: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Voice locale resource must be an object: {path}")
    return payload


def localized_mapping(name: str, serbian: dict[str, str]) -> dict[str, str]:
    overlay = load_locale_resource(name)
    if not overlay:
        return serbian.copy()
    missing = sorted(set(serbian) - set(overlay))
    if missing:
        raise RuntimeError(f"Locale resource {name} is missing keys: {', '.join(missing)}")
    return {key: str(overlay[key]) for key in serbian}
