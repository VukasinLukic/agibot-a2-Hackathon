import os
import logging
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import JobContext
from livekit.plugins import soniox, elevenlabs

from .voice_canary import env_flag, voice_canary_enabled

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH, override=not voice_canary_enabled())

logger = logging.getLogger("basic-agent-truebar")

TRUEBAR_SETTINGS_CACHE_KEY = "truebar_settings"
# Soniox keeps model, voice and language in three separate fields, and every
# voice speaks every supported language, so a voice is picked once and the
# language code is what changes. "tts-rt-v1-preview" is the plugin's own
# default; the server aliases it to the GA model below.
DEFAULT_SONIOX_TTS_MODEL = "tts-rt-v1"
DEFAULT_SONIOX_TTS_VOICE = "Maya"
DEFAULT_SONIOX_TTS_LANGUAGE = "sr"
SONIOX_TTS_MODEL_PREFIX = "tts-rt"
DEFAULT_ENGLISH_STT_KEYTERMS = (
    "Comtrade",
    "TITAN",
    "CERN",
    "CERN openlab",
    "Large Hadron Collider",
    "EOS",
)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().strip("'").strip('"')


def _language_hints() -> list[str]:
    raw = (
        _env("STT_LANGUAGE_HINTS")
        if voice_canary_enabled()
        else None
    ) or _env("SONIOX_LANGUAGE_HINTS", "sr")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _tts_language() -> str | None:
    """Fixed TTS language code, or None when the provider should auto-detect.

    Bilingual Serbian/English needs per-utterance detection, so an unset value
    (or an explicit "auto"/"multi") means "send no language_code at all".
    Single-language experiment arms still pin a language by setting TTS_LANGUAGE.
    """
    value = _env("TTS_LANGUAGE")
    if value is None or value.lower() in {"auto", "multi", "any", "none"}:
        return None
    return value


def _soniox_tts_language() -> str:
    """Explicit ISO code for Soniox, which has no per-utterance auto-detect.

    ElevenLabs reads an unset (or "auto") TTS_LANGUAGE as "detect per
    utterance", which is what the bilingual Serbian/English arm depends on.
    Soniox requires the code in every request, so "auto" has to resolve to a
    real language here rather than being forwarded as a literal and rejected.
    """
    return _tts_language() or _env("SONIOX_TTS_LANGUAGE", DEFAULT_SONIOX_TTS_LANGUAGE)


def _float_env(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


def _int_env(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _csv_env(name: str) -> list[str]:
    raw = _env(name, "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _stt_keyterms() -> list[str]:
    if not voice_canary_enabled():
        return []
    configured = _csv_env("STT_KEYTERMS")
    if configured:
        return configured
    if (_env("VOICE_LANGUAGE", "en") or "en").lower().startswith("en"):
        return list(DEFAULT_ENGLISH_STT_KEYTERMS)
    return []


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"Missing {name} in environment or {ENV_PATH}")
    return value


def selected_stt_provider() -> str:
    if not voice_canary_enabled():
        return "soniox"
    return (_env("STT_PROVIDER", "soniox") or "soniox").lower()


def selected_tts_provider() -> str:
    if not voice_canary_enabled():
        return "elevenlabs"
    return (_env("TTS_PROVIDER", "elevenlabs") or "elevenlabs").lower()


def load_truebar_settings() -> dict:
    soniox_api_key = _env("SONIOX_API_KEY")
    eleven_api_key = _env("ELEVEN_API_KEY") or _env("ELEVENLABS_API_KEY")

    if not soniox_api_key:
        raise RuntimeError(f"Missing SONIOX_API_KEY in environment or {ENV_PATH}")

    if not eleven_api_key:
        raise RuntimeError(f"Missing ELEVEN_API_KEY or ELEVENLABS_API_KEY in environment or {ENV_PATH}")

    return {
        "soniox_api_key": soniox_api_key,
        "soniox_model": _env("SONIOX_STT_MODEL", "stt-rt-v4"),
        "language_hints": _language_hints(),
        "eleven_api_key": eleven_api_key,
        "eleven_voice_id": _env("ELEVENLABS_VOICE_ID", "ODq5zmih8GrVes37Dizd"),
        "eleven_model": _env("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
    }


def prepare_truebar_settings(ctx: JobContext) -> dict:
    settings = ctx.proc.userdata.get(TRUEBAR_SETTINGS_CACHE_KEY)

    if settings is None:
        settings = load_truebar_settings()
        ctx.proc.userdata[TRUEBAR_SETTINGS_CACHE_KEY] = settings

    return settings


def prepare_truebar_stt(ctx: JobContext) -> soniox.STT:
    settings = prepare_truebar_settings(ctx)

    provider = selected_stt_provider()
    if provider == "deepgram_flux":
        from livekit.plugins import deepgram

        logger.info("Preparing Deepgram Flux STT")
        return deepgram.STTv2(
            api_key=_required_env("DEEPGRAM_API_KEY"),
            model=_env("STT_MODEL", "flux-general-en"),
            eager_eot_threshold=_float_env("DEEPGRAM_FLUX_EAGER_EOT", 0.4),
            eot_threshold=_float_env("DEEPGRAM_FLUX_EOT", 0.7),
            eot_timeout_ms=_int_env("DEEPGRAM_FLUX_EOT_TIMEOUT_MS", 3000),
            keyterm=_stt_keyterms(),
        )

    if provider == "assemblyai":
        from livekit.plugins import assemblyai

        logger.info("Preparing AssemblyAI Universal Streaming STT")
        return assemblyai.STT(
            api_key=_required_env("ASSEMBLYAI_API_KEY"),
            model=_env("STT_MODEL", "universal-streaming-english"),
            min_turn_silence=_int_env("ASSEMBLYAI_MIN_TURN_SILENCE_MS", 400),
            max_turn_silence=_int_env("ASSEMBLYAI_MAX_TURN_SILENCE_MS", 3000),
            keyterms_prompt=_stt_keyterms(),
        )

    if provider != "soniox":
        raise ValueError(
            f"Unknown STT_PROVIDER={provider!r}; expected soniox, deepgram_flux, or assemblyai"
        )

    soniox_model = (
        _env("STT_MODEL", settings["soniox_model"])
        if voice_canary_enabled()
        else settings["soniox_model"]
    )
    language_hints_strict = (
        env_flag("SONIOX_LANGUAGE_HINTS_STRICT", False)
        if voice_canary_enabled()
        else False
    )
    keyterms = _stt_keyterms()
    logger.info(
        "Preparing Soniox STT: model=%s language_hints=%s strict=%s",
        soniox_model,
        settings["language_hints"],
        language_hints_strict,
    )

    return soniox.STT(
        api_key=settings["soniox_api_key"],
        params=soniox.STTOptions(
            model=soniox_model,
            language_hints=settings["language_hints"],
            language_hints_strict=language_hints_strict,
            context=soniox.ContextObject(terms=keyterms) if keyterms else None,
        ),
    )


def prepare_truebar_tts(ctx: JobContext) -> elevenlabs.TTS:
    settings = prepare_truebar_settings(ctx)

    provider = selected_tts_provider()
    if provider == "cartesia":
        from livekit.plugins import cartesia

        voice_id = _required_env("TTS_VOICE_ID")
        language = _tts_language()
        logger.info(
            "Preparing Cartesia TTS: model=%s language=%s",
            _env("TTS_MODEL", "sonic-3"),
            language or "auto-detect",
        )
        return cartesia.TTS(
            api_key=_required_env("CARTESIA_API_KEY"),
            model=_env("TTS_MODEL", "sonic-3"),
            **({"language": language} if language else {}),
            voice=voice_id,
            speed=_float_env("TTS_SPEED", 0.93),
            pronunciation_dict_id=_env("CARTESIA_PRONUNCIATION_DICT_ID"),
        )

    if provider == "deepgram_aura2":
        from livekit.plugins import deepgram

        model = _env("TTS_MODEL", "aura-2-andromeda-en")
        # Aura encodes its voice in the model name. TTS_VOICE_ID may override
        # the model after a blind audition, but an empty value is intentional.
        model = _env("TTS_VOICE_ID", model)
        logger.info("Preparing Deepgram Aura-2 TTS: model=%s", model)
        return deepgram.TTS(
            api_key=_required_env("DEEPGRAM_API_KEY"),
            model=model,
        )

    if provider == "soniox":
        # The Speech tab's voice rows are free text, so the model column can
        # still hold an ElevenLabs id right after an operator switches the
        # provider on an existing row. Fall back rather than fail the session.
        model = _env("TTS_MODEL", DEFAULT_SONIOX_TTS_MODEL)
        if not model.startswith(SONIOX_TTS_MODEL_PREFIX):
            logger.warning(
                "Ignoring TTS_MODEL=%s for Soniox; falling back to %s",
                model,
                DEFAULT_SONIOX_TTS_MODEL,
            )
            model = DEFAULT_SONIOX_TTS_MODEL
        # Soniox voice ids are names ("Noah"), not opaque ids, but they travel
        # in the same generic TTS_VOICE_ID slot every other provider uses.
        voice = _env("TTS_VOICE_ID") or _env("SONIOX_TTS_VOICE", DEFAULT_SONIOX_TTS_VOICE)
        language = _soniox_tts_language()
        logger.info(
            "Preparing Soniox TTS: model=%s voice=%s language=%s",
            model,
            voice,
            language,
        )
        return soniox.TTS(
            api_key=settings["soniox_api_key"],
            model=model,
            voice=voice,
            language=language,
        )

    if provider != "elevenlabs":
        raise ValueError(
            f"Unknown TTS_PROVIDER={provider!r}; expected elevenlabs, soniox, "
            f"cartesia, or deepgram_aura2"
        )

    if voice_canary_enabled():
        voice_id = _env("TTS_VOICE_ID") or settings["eleven_voice_id"]
        model = _env("TTS_MODEL", "eleven_flash_v2_5")
        # A fixed language_code makes ElevenLabs phonetize every utterance as that
        # language, so Serbian replies come out read with English phonetics. Leave
        # TTS_LANGUAGE unset (or "auto"/"multi") to let the model detect the
        # language per utterance, which is what bilingual Serbian/English needs.
        language = _tts_language()
        logger.info(
            "Preparing ElevenLabs TTS: model=%s language=%s",
            model,
            language or "auto-detect",
        )
        return elevenlabs.TTS(
            api_key=settings["eleven_api_key"],
            voice_id=voice_id,
            model=model,
            **({"language": language} if language else {}),
            voice_settings=elevenlabs.VoiceSettings(
                stability=_float_env("TTS_STABILITY", 0.8),
                similarity_boost=_float_env("TTS_SIMILARITY_BOOST", 0.75),
                style=_float_env("TTS_STYLE", 0.0),
                speed=_float_env("TTS_SPEED", 0.93),
                use_speaker_boost=env_flag("TTS_USE_SPEAKER_BOOST", True),
            ),
            # Kept separate from TTS_ENGLISH_PRONUNCIATION: that flag now only
            # controls the English respelling dictionary, which must be off for
            # bilingual output, while SSML parsing is language-independent.
            enable_ssml_parsing=env_flag("TTS_SSML_PARSING", True),
            apply_language_text_normalization=True,
        )

    logger.info(
        "Preparing ElevenLabs TTS: model=%s voice_id=%s",
        settings["eleven_model"],
        settings["eleven_voice_id"],
    )

    return elevenlabs.TTS(
        api_key=settings["eleven_api_key"],
        voice_id=settings["eleven_voice_id"],
        model=settings["eleven_model"],
        apply_language_text_normalization=True,
    )
