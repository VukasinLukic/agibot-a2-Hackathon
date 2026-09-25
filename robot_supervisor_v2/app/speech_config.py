"""Persistent speech configuration for Truebar voice and pronunciation."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATE_FILE = _REPO_ROOT / "robot_supervisor_v2" / "state" / "speech.json"
_AUDIO_UPLOAD_DIR = _REPO_ROOT / "robot_supervisor_v2" / "state" / "audio"
ACCEPTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".opus", ".flac", ".aac", ".m4a", ".webm", ".mp4"}
BUILTIN_BACKGROUND_AUDIO_CLIPS = (
    "CITY_AMBIENCE",
    "FOREST_AMBIENCE",
    "OFFICE_AMBIENCE",
    "CROWDED_ROOM",
    "KEYBOARD_TYPING",
    "KEYBOARD_TYPING2",
    "HOLD_MUSIC",
)
DEFAULT_BACKGROUND_AUDIO: dict[str, Any] = {
    "enabled": False,
    "source_type": "builtin",
    "source": "KEYBOARD_TYPING",
    "volume": 0.45,
}

# Deliberately empty, like the English map below: pronunciation is owned entirely
# by the configured entries in the speech state. The Slovenian respellings that
# used to live here (kavomat, obljubim, Vitrex, "igra" -> "igra" with an accent)
# were Petrol leftovers and mangled ordinary Serbian words.
DEFAULT_CUSTOM_TRANSFORMATIONS: dict[str, str] = {}

# The English-locale counterpart, empty for the same reason. Both maps are kept
# so get_custom_transformations() still picks a bucket per locale; an operator's
# Serbian and English lists stay separate.
DEFAULT_ENGLISH_TRANSFORMATIONS: dict[str, str] = {}

VOICE_CONFIG_SCHEMA_VERSION = 2
VOICE_FIELDS = ("provider", "model", "voice_id", "label", "language")

# Spoken responses are plain speech, not rich chat. Removing pictographs before
# they enter chat history, TTS, or published transcription avoids useless tokens
# and prevents providers from trying to verbalize visual-only symbols.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"  # flags
    "\U0001F300-\U0001FAFF"  # emoji and pictographs
    "\u2600-\u27BF"          # miscellaneous symbols/dingbats
    "\u200D"                 # zero-width joiner
    "\u20E3"                 # keycap combiner
    "\uFE0E-\uFE0F"          # text/emoji variation selectors
    "]+"
)


def get_speech_state_file() -> Path:
    return _STATE_FILE


def get_audio_upload_dir() -> Path:
    return _AUDIO_UPLOAD_DIR


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clamp_volume(value: Any) -> float:
    try:
        volume = float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_BACKGROUND_AUDIO["volume"])
    return max(0.0, min(1.0, volume))


def _uploaded_audio_source_path(source: str) -> Path:
    source_path = Path(source)
    if source_path.is_absolute():
        return source_path
    return _REPO_ROOT / source_path


def _audio_upload_source(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_REPO_ROOT))
    except ValueError:
        return str(path)


def _default_voice_label(tts_tag: str) -> str:
    parts = [part for part in tts_tag.split(":") if part]
    if len(parts) >= 3:
        return f"{parts[2].replace('_', ' ').replace('-', ' ').title()} ({parts[1]})"
    return "Default"


def voice_identity(voice: dict[str, Any] | None) -> str:
    if not isinstance(voice, dict):
        return ""
    return "|".join(_clean_text(voice.get(field)) for field in ("provider", "model", "voice_id", "language"))


def _normalize_voice(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict) or "tts_tag" in raw:
        return None
    provider = _clean_text(raw.get("provider")).lower()
    model = _clean_text(raw.get("model"))
    voice_id = _clean_text(raw.get("voice_id"))
    language = _clean_text(raw.get("language"))
    if not provider:
        return None
    label = _clean_text(raw.get("label")) or voice_id or model or provider.title()
    return {
        "provider": provider,
        "model": model,
        "voice_id": voice_id,
        "label": label,
        "language": language,
    }


def _legacy_truebar_voice(tts_tag: str, label: str = "") -> dict[str, str]:
    """Explicit rollback-only mapping; a Truebar tag never becomes another provider's ID."""
    return {
        "provider": "truebar",
        "model": "legacy",
        "voice_id": tts_tag,
        "label": label or _default_voice_label(tts_tag),
        "language": "sr",
    }


def migrate_legacy_speech_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert schema v1 tts_tag values only into an explicit Truebar contract."""
    if not isinstance(raw, dict):
        return raw
    if isinstance(raw.get("active_voice"), dict):
        return dict(raw)

    legacy_active = _clean_text(raw.get("active_voice"))
    legacy_voices = raw.get("voices") or []
    voices: list[dict[str, str]] = []
    by_tag: dict[str, dict[str, str]] = {}
    for item in legacy_voices:
        if not isinstance(item, dict):
            continue
        tag = _clean_text(item.get("tts_tag"))
        if not tag:
            continue
        voice = _legacy_truebar_voice(tag, _clean_text(item.get("label")))
        voices.append(voice)
        by_tag[tag] = voice
    active = by_tag.get(legacy_active)
    if legacy_active and active is None:
        active = _legacy_truebar_voice(legacy_active)
        voices.insert(0, active)

    migrated = dict(raw)
    migrated["schema_version"] = VOICE_CONFIG_SCHEMA_VERSION
    migrated["active_voice"] = active
    migrated["voices"] = voices
    migrated["legacy_truebar_tts_tag"] = legacy_active
    return migrated


def _default_active_voice() -> dict[str, str]:
    provider = _clean_text(os.getenv("TTS_PROVIDER")) or "elevenlabs"
    provider = provider.lower()
    provider_defaults = {
        "elevenlabs": _clean_text(os.getenv("ELEVENLABS_MODEL")) or "eleven_multilingual_v2",
        "cartesia": "sonic-3",
        "deepgram_aura2": "aura-2-andromeda-en",
    }
    model = _clean_text(os.getenv("TTS_MODEL")) or provider_defaults.get(provider, "")
    voice_id = _clean_text(os.getenv("TTS_VOICE_ID"))
    if provider == "elevenlabs" and not voice_id:
        voice_id = _clean_text(os.getenv("ELEVENLABS_VOICE_ID")) or "ODq5zmih8GrVes37Dizd"
    language = _clean_text(os.getenv("TTS_LANGUAGE")) or "sr"
    return {
        "provider": provider,
        "model": model,
        "voice_id": voice_id,
        "label": _clean_text(os.getenv("TTS_VOICE_LABEL")) or f"{provider.title()} {voice_id or model}",
        "language": language,
    }


def _default_config() -> dict[str, Any]:
    active_voice = _default_active_voice()
    return {
        "schema_version": VOICE_CONFIG_SCHEMA_VERSION,
        "active_voice": active_voice,
        "voices": [active_voice],
        "legacy_truebar_tts_tag": _clean_text(os.getenv("TRUEBAR_TTS_TAG")),
        "custom_transformations": {},
        "english_transformations": {},
        "background_audio": DEFAULT_BACKGROUND_AUDIO.copy(),
    }


def normalize_background_audio_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return DEFAULT_BACKGROUND_AUDIO.copy()

    source_type = _clean_text(raw.get("source_type")) or DEFAULT_BACKGROUND_AUDIO["source_type"]
    if source_type not in {"builtin", "upload"}:
        source_type = DEFAULT_BACKGROUND_AUDIO["source_type"]

    source = _clean_text(raw.get("source")) or DEFAULT_BACKGROUND_AUDIO["source"]
    if source_type == "builtin" and source not in BUILTIN_BACKGROUND_AUDIO_CLIPS:
        source = DEFAULT_BACKGROUND_AUDIO["source"]

    return {
        "enabled": bool(raw.get("enabled", DEFAULT_BACKGROUND_AUDIO["enabled"])),
        "source_type": source_type,
        "source": source,
        "volume": _clamp_volume(raw.get("volume", DEFAULT_BACKGROUND_AUDIO["volume"])),
    }


def validate_background_audio_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("background_audio must be an object")

    enabled = bool(raw.get("enabled", False))
    source_type = _clean_text(raw.get("source_type"))
    if source_type not in {"builtin", "upload"}:
        raise ValueError("background_audio.source_type must be 'builtin' or 'upload'")

    source = _clean_text(raw.get("source"))
    if not source and not enabled:
        source_type = str(DEFAULT_BACKGROUND_AUDIO["source_type"])
        source = str(DEFAULT_BACKGROUND_AUDIO["source"])
    if not source:
        raise ValueError("background_audio.source is required")

    if source_type == "builtin" and source not in BUILTIN_BACKGROUND_AUDIO_CLIPS:
        raise ValueError(
            "background_audio.source must be one of: "
            + ", ".join(BUILTIN_BACKGROUND_AUDIO_CLIPS)
        )

    if source_type == "upload":
        source_path = _uploaded_audio_source_path(source).resolve()
        upload_root = _AUDIO_UPLOAD_DIR.resolve()
        if upload_root not in source_path.parents and source_path != upload_root:
            raise ValueError("background_audio.source must reference an uploaded audio file")
        if enabled and not source_path.is_file():
            raise ValueError("background_audio.source must reference an existing uploaded audio file")
        if source_path.suffix.lower() not in ACCEPTED_AUDIO_EXTENSIONS:
            raise ValueError("background_audio.source has an unsupported audio extension")

    return {
        "enabled": enabled,
        "source_type": source_type,
        "source": source,
        "volume": _clamp_volume(raw.get("volume", DEFAULT_BACKGROUND_AUDIO["volume"])),
    }


def normalize_speech_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = _default_config()
    if not isinstance(raw, dict):
        return base

    raw = migrate_legacy_speech_config(raw)

    active_voice = _normalize_voice(raw.get("active_voice")) or base["active_voice"]

    voices: list[dict[str, str]] = []
    seen_voices: set[str] = set()
    for voice in raw.get("voices") or []:
        normalized_voice = _normalize_voice(voice)
        if normalized_voice is None:
            continue
        identity = voice_identity(normalized_voice)
        if identity in seen_voices:
            continue
        voices.append(normalized_voice)
        seen_voices.add(identity)

    if voice_identity(active_voice) not in seen_voices:
        voices.insert(0, active_voice)

    raw_transformations = raw.get("custom_transformations")
    if isinstance(raw_transformations, dict):
        transformations: dict[str, str] = {}
        for source, replacement in raw_transformations.items():
            clean_source = _clean_text(source)
            clean_replacement = _clean_text(replacement)
            if clean_source and clean_replacement:
                transformations[clean_source] = clean_replacement
    else:
        transformations = base["custom_transformations"]

    raw_english_transformations = raw.get("english_transformations")
    if isinstance(raw_english_transformations, dict):
        english_transformations = {
            _clean_text(source): _clean_text(replacement)
            for source, replacement in raw_english_transformations.items()
            if _clean_text(source) and _clean_text(replacement)
        }
    else:
        english_transformations = base["english_transformations"]

    return {
        "schema_version": VOICE_CONFIG_SCHEMA_VERSION,
        "active_voice": active_voice,
        "voices": voices,
        "legacy_truebar_tts_tag": _clean_text(raw.get("legacy_truebar_tts_tag")),
        "custom_transformations": transformations,
        "english_transformations": english_transformations,
        "background_audio": normalize_background_audio_config(raw.get("background_audio")),
    }


def validate_speech_config(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("speech config must be an object")
    if isinstance(raw.get("active_voice"), str) or any(
        isinstance(voice, dict) and "tts_tag" in voice for voice in raw.get("voices") or []
    ):
        raise ValueError("legacy tts_tag input requires explicit migrate_legacy_speech_config()")
    active_voice = _normalize_voice(raw.get("active_voice"))
    if active_voice is None:
        raise ValueError("active_voice must use provider/model/voice_id/label/language")

    voices: list[dict[str, str]] = []
    seen_voices: set[str] = set()
    for index, voice in enumerate(raw.get("voices") or []):
        normalized_voice = _normalize_voice(voice)
        if normalized_voice is None:
            raise ValueError(
                f"voices[{index}] must use provider/model/voice_id/label/language"
            )
        identity = voice_identity(normalized_voice)
        if identity in seen_voices:
            continue
        voices.append(normalized_voice)
        seen_voices.add(identity)

    if voice_identity(active_voice) not in seen_voices:
        raise ValueError("active_voice must match one of the configured voices")

    transformations: dict[str, str] = {}
    raw_transformations = raw.get("custom_transformations")
    if not isinstance(raw_transformations, dict):
        raise ValueError("custom_transformations must be an object")

    for source, replacement in raw_transformations.items():
        clean_source = _clean_text(source)
        clean_replacement = _clean_text(replacement)
        if not clean_source:
            raise ValueError("custom transformation source text cannot be empty")
        if not clean_replacement:
            raise ValueError(f"custom transformation replacement for '{clean_source}' cannot be empty")
        transformations[clean_source] = clean_replacement

    english_transformations: dict[str, str] = {}
    raw_english_transformations = raw.get("english_transformations", {})
    if not isinstance(raw_english_transformations, dict):
        raise ValueError("english_transformations must be an object")
    for source, replacement in raw_english_transformations.items():
        clean_source = _clean_text(source)
        clean_replacement = _clean_text(replacement)
        if not clean_source or not clean_replacement:
            raise ValueError("English pronunciation rows require source and replacement")
        english_transformations[clean_source] = clean_replacement

    return {
        "schema_version": VOICE_CONFIG_SCHEMA_VERSION,
        "active_voice": active_voice,
        "voices": voices,
        "legacy_truebar_tts_tag": _clean_text(raw.get("legacy_truebar_tts_tag")),
        "custom_transformations": transformations,
        "english_transformations": english_transformations,
        "background_audio": validate_background_audio_config(
            raw.get("background_audio", DEFAULT_BACKGROUND_AUDIO)
        ),
    }


def load_speech_config() -> dict[str, Any]:
    if not _STATE_FILE.exists():
        return _default_config()

    try:
        raw = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _default_config()

    return normalize_speech_config(raw)


def save_speech_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_speech_config(config)
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return normalized


def get_active_tts_tag() -> str | None:
    config = load_speech_config()
    voice = _normalize_voice(config.get("active_voice"))
    if voice and voice.get("provider") == "truebar":
        return voice.get("voice_id") or None
    return _clean_text(config.get("legacy_truebar_tts_tag")) or None


def get_active_voice() -> dict[str, str] | None:
    return _normalize_voice(load_speech_config().get("active_voice"))


def english_locale_active(*, locale: str | None = None) -> bool:
    """Whether the running agent reads the English-authored locale assets.

    The supervisor UI has to ask this too. If it decides on its own -- from the
    active voice language, say -- an operator can end up editing the Serbian
    pronunciation list while the agent applies the English one.
    """
    selected_locale = (locale or os.getenv("VOICE_LOCALE") or os.getenv("VOICE_LANGUAGE") or "sr").lower()
    return selected_locale.startswith("en") and (
        locale is not None
        or (os.getenv("VOICE_CANARY_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
    )


def tts_normalizer_language() -> str:
    """Language whose number/time expansion rules apply to spoken text.

    This is the language the robot actually speaks, which is not VOICE_LOCALE: the
    English canary sets VOICE_LOCALE=en-US to pick English-authored prompts while a
    Serbian voice does the talking. Default to the active voice's own language and
    let TTS_NORMALIZER_LANGUAGE override it.
    """
    explicit = _clean_text(os.getenv("TTS_NORMALIZER_LANGUAGE"))
    if explicit:
        return explicit
    voice = get_active_voice() or {}
    return _clean_text(voice.get("language")) or "sr"


def get_custom_transformations(*, locale: str | None = None) -> dict[str, str]:
    config = load_speech_config()
    english = english_locale_active(locale=locale)
    merged = (
        DEFAULT_ENGLISH_TRANSFORMATIONS.copy()
        if english
        else DEFAULT_CUSTOM_TRANSFORMATIONS.copy()
    )
    transformations = config.get(
        "english_transformations" if english else "custom_transformations"
    )
    if isinstance(transformations, dict):
        merged.update({str(key): str(value) for key, value in transformations.items()})
    return merged


def normalize_text_for_tts(text: str, *, locale: str | None = None) -> str:
    normalized = str(text)
    for source, replacement in sorted(
        get_custom_transformations(locale=locale).items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        normalized = re.sub(
            rf"(?<!\w){re.escape(source)}(?!\w)",
            lambda _match, value=replacement: value,
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized


def sanitize_spoken_text(text: str) -> str:
    """Remove internal/visual-only tokens from speech, transcript, and context."""
    sanitized = _EMOJI_PATTERN.sub("", str(text))
    sanitized = re.sub(r"\[\s*ACTIVE\s+PANEL\s*\]", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(
        r"\[\s*RETRIEVED\s+PANEL\s+FOR\s+THIS\s+TURN\s*\]",
        "",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(r"[ \t]+([,.;:!?])", r"\1", sanitized)
    return re.sub(r"[ \t]{2,}", " ", sanitized)


def list_uploaded_audio_files() -> list[dict[str, Any]]:
    if not _AUDIO_UPLOAD_DIR.exists():
        return []

    uploads: list[dict[str, Any]] = []
    for path in sorted(_AUDIO_UPLOAD_DIR.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in ACCEPTED_AUDIO_EXTENSIONS:
            continue
        uploads.append(
            {
                "label": path.name,
                "source": _audio_upload_source(path),
                "filename": path.name,
                "size_bytes": path.stat().st_size,
            }
        )
    return uploads


def background_audio_options() -> dict[str, Any]:
    return {
        "built_in": [
            {"label": name.replace("_", " ").title(), "source": name}
            for name in BUILTIN_BACKGROUND_AUDIO_CLIPS
        ],
        "uploads": list_uploaded_audio_files(),
        "accepted_extensions": sorted(ACCEPTED_AUDIO_EXTENSIONS),
    }


def get_background_audio_config() -> dict[str, Any]:
    return normalize_background_audio_config(load_speech_config().get("background_audio"))


def resolve_uploaded_audio_source(source: str) -> Path:
    return _uploaded_audio_source_path(source)
