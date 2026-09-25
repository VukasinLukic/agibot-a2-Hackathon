import time

_AGENT_MODULE_BOOT_TS = time.perf_counter()
print("agent_main_led.py import started", flush=True)

# standard library imports
import asyncio
import base64
import json
import logging
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Deque, Optional

# local import path bootstrap
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# third-party imports
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    ChatContext,
    JobContext,
    JobProcess,
    AgentServer,
    TurnHandlingOptions,
    cli,
    function_tool,
    get_job_context,
    BackgroundAudioPlayer,
    AudioConfig,
    BuiltinAudioClip,
)
from livekit.agents.worker import ServerType
from livekit.agents.llm import ImageContent
from livekit.agents.llm import StopResponse
from livekit.agents.voice.room_io import RoomInputOptions, RoomOutputOptions
from livekit.plugins import (
    openai,
    silero,
)

# local imports
from livekit_config import (
    build_goodbye_text,
    build_initial_greeting,
    build_system_prompt,
    get_prompt_builder,
)
from livekit_config.tts_normalizer import normalize_tts_text
from livekit_config.known_people import (
    KnownPersonIdentity,
    extract_spoken_known_person_identity,
    is_self_identity_question,
    resolve_known_person_identity,
)
from livekit_config.vocative import first_name_vocative, serbian_greeting_with_vocative
from livekit_config.truebar_config import (
    load_truebar_settings as _load_truebar_settings,
    prepare_truebar_settings as _prepare_truebar_settings,
    prepare_truebar_stt as _prepare_truebar_stt,
    prepare_truebar_tts as _prepare_truebar_tts,
    stt_language_mode_response_directive as _stt_language_mode_response_directive,
)
from livekit_config.voice_latency import get_voice_latency_tracer
from livekit_config.voice_settings import load_voice_settings, log_voice_settings
from livekit_config.language_pack import load_phonetics
from livekit_config.install_lexicon import load_install_lexicon
from livekit_config.runtime_prompts import runtime_prompt
from robot_supervisor_v2.app.speech_config import (
    get_background_audio_config,
    get_custom_transformations,
    get_goodbye_text_override,
    get_initial_greeting_override,
    get_stt_language_mode,
    resolve_builtin_background_audio_source,
    resolve_uploaded_audio_source,
)
from utils.env_vars import( 
        AZURE_OPENAI_BASE, 
        AZURE_OPENAI_API_KEY, 
        OPENAI_API_VERSION,
        CHOSEN_COMPLETION_MODEL,
        AZURE_OPENAI_DEPLOYMENT,
    )
from utils.console_safe import configure_utf8_output
from robot_services.gestures import normalize_gesture
from robot_services.gestures import (
    build_available_gesture_text,
    build_gesture_policy_prompt,
    get_allowed_gestures,
    get_configured_gesture_safety_pool,
)
from robot_services.gestures.candidates import load_conversation_candidates
from robot_services.question_answers_service import get_time, get_weather, match_qa_intent
from robot_services.question_answers_service.screen_display import render_flash_video


# Robot visual UI runtime
from robot.visual_ui_runtime import AgentVisualUiRuntime
from robot.gesture_tokens import (
    GestureTokenProcessor,
    build_inline_gesture_policy_prompt,
)
from robot.gesture_intents import GestureIntentRuntime, build_intent_prompt
from robot.head_motion_runtime import SpeakingHeadMotionRuntime

# RAG client
from rag import RAGServiceClient, RagConfig

# Survey / Quiz flow
from survey_flow import SurveyFlowTask, load_survey_state, normalize_survey_language
from quiz_flow import QuizFlowTask, load_quiz_state, normalize_quiz_language
from face_enrollment_flow import FaceEnrollmentTask, is_face_enrollment_trigger
from turn_gate import (
    TwoAxisTurnGate,
    classify_intent as classify_gate_v2_intent,
    classify_interruption,
    has_explicit_addressee,
    looks_like_self_echo,
    strict_name_wake,
    strip_system_audio,
)
from trigger_word import TriggerWordEngagement, looks_like_repair_followup
from conversation_context import ConversationContextBuffer
from speech.reply_shape import (
    trim_trailing_reply_filler as _trim_trailing_reply_filler,
    trim_trailing_reply_filler_stream as _trim_trailing_reply_filler_stream,
)


# Supervisor passes the selected speech provider/voice through the child env.
# Keep those values authoritative and use .env only for missing local defaults.
load_dotenv(override=False)
configure_utf8_output()
logger = logging.getLogger("basic-agent-truebar")
latency_tracer = get_voice_latency_tracer("basic-agent-truebar")
VOICE_SETTINGS = load_voice_settings()
log_voice_settings(VOICE_SETTINGS)

AGENT_AEC_WARMUP_DURATION_S = VOICE_SETTINGS.agent.aec_warmup_duration_s
AGENT_MIN_INTERRUPTION_DURATION_S = VOICE_SETTINGS.agent.min_interruption_duration_s
AGENT_MIN_INTERRUPTION_WORDS = VOICE_SETTINGS.agent.min_interruption_words
AGENT_MIN_ENDPOINTING_DELAY_S = VOICE_SETTINGS.agent.min_endpointing_delay_s
AGENT_MAX_ENDPOINTING_DELAY_S = VOICE_SETTINGS.agent.max_endpointing_delay_s
AGENT_INTERRUPTION_MODE = VOICE_SETTINGS.agent.interruption_mode
AGENT_FALSE_INTERRUPTION_TIMEOUT_S = VOICE_SETTINGS.agent.false_interruption_timeout_s
AGENT_RESUME_FALSE_INTERRUPTION = VOICE_SETTINGS.agent.resume_false_interruption
AGENT_BACKCHANNEL_BOUNDARY_S = VOICE_SETTINGS.agent.backchannel_boundary_s
AGENT_VAD_MIN_SPEECH_DURATION_S = VOICE_SETTINGS.vad.min_speech_duration_s
AGENT_VAD_MIN_SILENCE_DURATION_S = VOICE_SETTINGS.vad.min_silence_duration_s
AGENT_VAD_PREFIX_PADDING_DURATION_S = VOICE_SETTINGS.vad.prefix_padding_duration_s
AGENT_VAD_ACTIVATION_THRESHOLD = VOICE_SETTINGS.vad.activation_threshold
VOICE_METRICS_ENABLED = VOICE_SETTINGS.misc.voice_metrics_enabled
ADDRESSEE_GATE_ENABLED = VOICE_SETTINGS.engagement.addressee_gate_enabled
ADDRESSEE_REQUIRE_DIRECTED_TURN = VOICE_SETTINGS.engagement.addressee_require_directed_turn
ADDRESSEE_MAX_SKIPPED_TURNS = VOICE_SETTINGS.engagement.addressee_max_skipped_turns
LOOSE_ENGAGEMENT_GATE = VOICE_SETTINGS.engagement.loose_engagement_gate
ENGAGEMENT_WINDOW_S = VOICE_SETTINGS.engagement.engagement_window_s
CONVERSATION_MEMORY_MAX_FACTS = VOICE_SETTINGS.conversation.memory_max_facts
CONVERSATION_MEMORY_MAX_CHARS = VOICE_SETTINGS.conversation.memory_max_chars
CONTEXTUAL_FOLLOWUP_TIMEOUT_S = VOICE_SETTINGS.engagement.contextual_followup_timeout_s
TRIGGER_WORD_FEATURE_ENABLED = VOICE_SETTINGS.trigger_word.feature_enabled
TRIGGER_WORD_DEFAULT_ENABLED = VOICE_SETTINGS.trigger_word.default_enabled
TRIGGER_WORD_ENGAGEMENT_TIMEOUT_S = VOICE_SETTINGS.trigger_word.engagement_timeout_s
TRIGGER_WORD_REPAIR_WINDOW_S = VOICE_SETTINGS.trigger_word.repair_window_s
TRIGGER_WORD_ACK_ENABLED = VOICE_SETTINGS.trigger_word.ack_enabled
TRIGGER_WORD_VARIANTS = VOICE_SETTINGS.trigger_word.variants
RAG_SELECTIVE_GATE_ENABLED = VOICE_SETTINGS.rag.selective_gate_enabled
RAG_QUALITY_MODE = VOICE_SETTINGS.rag.quality_mode
RAG_ENGLISH_PARITY = VOICE_SETTINGS.rag.english_parity
RAG_CLAIM_GROUNDING_SCOPE = (
    VOICE_SETTINGS.rag.claim_grounding_scope
    if VOICE_SETTINGS.rag.claim_grounding_scope in {"all", "wayfinding_only"}
    else "all"
)
RAG_SEARCH_BUDGET_S = VOICE_SETTINGS.rag.search_budget_s
RAG_FALLBACK_SEARCH_BUDGET_S = VOICE_SETTINGS.rag.fallback_search_budget_s
# The service receives the strict search budget. This small local-only grace
# covers response serialization and loop scheduling so a completed 2 s search
# is not discarded a few milliseconds before its HTTP response is read.
RAG_RESPONSE_GRACE_S = VOICE_SETTINGS.rag.response_grace_s
RAG_QUERY_MIN_WORDS = VOICE_SETTINGS.rag.query_min_words
RAG_ASYNC = VOICE_SETTINGS.rag.async_enabled
RAG_ASYNC_WAIT_BUDGET_S = VOICE_SETTINGS.rag.async_wait_budget_s
RAG_QUERY_REWRITE = VOICE_SETTINGS.rag.query_rewrite
RAG_QUERY_REWRITE_TIMEOUT_S = VOICE_SETTINGS.rag.query_rewrite_timeout_s
RAG_PERSON_TOP_K = VOICE_SETTINGS.rag.person_top_k
RAG_SELF_IDENTITY_TOP_K = VOICE_SETTINGS.rag.self_identity_top_k
RAG_MULTI_QUERY_ENABLED = VOICE_SETTINGS.rag.multi_query_enabled
RAG_MULTI_QUERY_MAX_QUERIES = VOICE_SETTINGS.rag.multi_query_max_queries
RAG_MULTI_QUERY_CONTEXT_MAX_CHARS = VOICE_SETTINGS.rag.multi_query_context_max_chars
KNOWN_PERSON_RAG_INTENT_LLM = VOICE_SETTINGS.rag.known_person_intent_llm
KNOWN_PERSON_RAG_INTENT_TIMEOUT_S = VOICE_SETTINGS.rag.known_person_intent_timeout_s
KNOWN_PERSON_RAG_BIAS_DEFAULT = VOICE_SETTINGS.rag.known_person_bias_default
FACE_ENROLLMENT_AUTH_TTL_S = VOICE_SETTINGS.face.enrollment_auth_ttl_s
FACE_IDENTITY_MONITOR_INTERVAL_S = VOICE_SETTINGS.face.identity_monitor_interval_s
FACE_IDENTITY_CHECK_TIMEOUT_S = VOICE_SETTINGS.face.identity_check_timeout_s
FACE_IDENTITY_CHECK_POLL_S = VOICE_SETTINGS.face.identity_check_poll_s
GATE_V2 = VOICE_SETTINGS.turn_gate.gate_v2
GATE_V2_ENGAGEMENT_TIMEOUT_S = VOICE_SETTINGS.turn_gate.gate_v2_engagement_timeout_s
ENGAGE_WINDOW_20S = VOICE_SETTINGS.turn_gate.engage_window_20s
ENGAGE_WINDOW_S = VOICE_SETTINGS.engagement.engagement_window_s
ROOM_SUMMARY = VOICE_SETTINGS.room_summary.enabled
ROOM_SUMMARY_MAX_CHARS = VOICE_SETTINGS.room_summary.max_chars
ROOM_SUMMARY_EVERY_TURNS = VOICE_SETTINGS.room_summary.every_turns
ROOM_SUMMARY_CONTEXT_TURNS = VOICE_SETTINGS.room_summary.context_turns
ROOM_SUMMARY_IDLE_DELAY_S = VOICE_SETTINGS.room_summary.idle_delay_s
ADDRESSEE_LLM_TIEBREAK = VOICE_SETTINGS.turn_gate.addressee_llm_tiebreak
LLM_ROUTING = VOICE_SETTINGS.llm.routing
STT_CONN_REUSE = VOICE_SETTINGS.misc.stt_conn_reuse
INTERRUPT_GATE = VOICE_SETTINGS.turn_gate.interrupt_gate
FLOOR_HOLD_MIN_SPEECH_DURATION_S = VOICE_SETTINGS.turn_gate.floor_hold_min_speech_duration_s
FLOOR_HOLD_MIN_CONTENT_WORDS = VOICE_SETTINGS.turn_gate.floor_hold_min_content_words
FLOOR_HOLD_ECHO_SIMILARITY = VOICE_SETTINGS.turn_gate.floor_hold_echo_similarity
FLOOR_HOLD_TOPIC_OVERLAP_MIN_WORDS = VOICE_SETTINGS.turn_gate.floor_hold_topic_overlap_min_words
FLOOR_HOLD_DECISION_TTL_S = VOICE_SETTINGS.turn_gate.floor_hold_decision_ttl_s
CLIENT_AEC_ENABLED = VOICE_SETTINGS.turn_gate.client_aec_enabled
PRIMARY_SPEAKER_ID_AVAILABLE = VOICE_SETTINGS.turn_gate.primary_speaker_id_available
PRIMARY_SPEAKER_ID = VOICE_SETTINGS.turn_gate.primary_speaker_id
GESTURE_SAFETY_V2 = VOICE_SETTINGS.gestures.safety_v2
GESTURE_INTENT_MAP = VOICE_SETTINGS.gestures.intent_map
STT_SEGMENT_FIX = VOICE_SETTINGS.misc.stt_segment_fix
RECALL_BIAS = VOICE_SETTINGS.misc.recall_bias
RECALL_ENGAGE_TIMEOUT_S = VOICE_SETTINGS.misc.recall_engage_timeout_s
TTS_NORMALIZER_LANGUAGE = VOICE_SETTINGS.tts.normalizer_language
TTS_STREAM_MIN_CHARS = VOICE_SETTINGS.tts.stream_min_chars
TTS_STREAM_FIRST_CHARS = VOICE_SETTINGS.tts.stream_first_chars
TTS_STREAM_SOFT_CHARS = VOICE_SETTINGS.tts.stream_soft_chars
TTS_STREAM_MAX_CHARS = VOICE_SETTINGS.tts.stream_max_chars
INITIAL_GREETING_SPEAKING_MOTION_ENABLED = (
    VOICE_SETTINGS.tts.initial_greeting_speaking_motion_enabled
)
INITIAL_GREETING_SPEAKING_MOTION_MIN_CHARS = (
    VOICE_SETTINGS.tts.initial_greeting_speaking_motion_min_chars
)
INITIAL_GREETING_SPEAKING_MOTION_NAME = (
    VOICE_SETTINGS.tts.initial_greeting_speaking_motion_name
)
INITIAL_GREETING_SECTION_PAUSE_S = VOICE_SETTINGS.tts.initial_greeting_section_pause_s
CONVERSATION_CONTEXT_BUFFER_SIZE = VOICE_SETTINGS.conversation.context_buffer_size
CONVERSATION_CONTEXT_RECENT_MESSAGES = VOICE_SETTINGS.conversation.context_recent_messages
CONVERSATION_CONTEXT_SUMMARY_MAX_WORDS = VOICE_SETTINGS.conversation.context_summary_max_words
CONVERSATION_CONTEXT_SUMMARY_EVERY_MESSAGES = VOICE_SETTINGS.conversation.context_summary_every_messages
RAG_CACHE_TTL_S = VOICE_SETTINGS.rag.cache_ttl_s
RAG_CACHE_MAX_ITEMS = VOICE_SETTINGS.rag.cache_max_items
CLAIM_GROUNDING_TTS_FILTER_ENABLED = VOICE_SETTINGS.reply.claim_grounding_tts_filter_enabled
REPLY_SHAPE_ENABLED = VOICE_SETTINGS.reply.reply_shape_enabled
POSTGEN_TRIM_ENABLED = VOICE_SETTINGS.reply.postgen_trim_enabled
HOST_REPLY_MAX_TOKENS = VOICE_SETTINGS.reply.host_reply_max_tokens
SOCIAL_RECIPROCITY_ENABLED = VOICE_SETTINGS.reply.social_reciprocity_enabled
TTS_EXPLANATION_GESTURE_THRESHOLDS = VOICE_SETTINGS.tts.explanation_gesture_thresholds
RAG_SKIP_BACKCHANNELS = {
    item.strip().lower() for item in VOICE_SETTINGS.rag.skip_backchannels if item.strip()
}
STOP_COMMAND_WORDS = {
    "stani",
    "stop",
    "cuti",
    "ćuti",
    "prekini",
    "dosta",
    "zaustavi",
    "umukni",
}
STOP_COMMAND_PHRASES = {
    "razumeo sam",
}
_CONTROL_PHRASES = {
    "dosta",
    "dosta dosta",
    "cuti",
    "ćuti",
    "stani",
    "stani stani",
    "prekini",
    "prekini prekini",
    "razumeo sam",
    "dosta price",
    "dosta priče",
}
_AFFIRMATIVE_FOLLOWUPS = {
    "da",
    "moze",
    "naravno",
    "zelim",
    "hocu",
    "reci jos",
    "vise informacija",
    "zelim vise informacija",
}
_CHITCHAT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(zdravo|cao|ćao|dobar dan|dobro jutro|dobro veče|pozdrav)"
        r"(?:[\s,]+(?:kortes|korteks|cortex))?[\s.!?,]*$",
        r"\b(kako si|šta radiš|sta radis|ko si|šta si|sta si|kako se zoveš|kako se zoves)\b",
        r"\b(da li me čuješ|da li me cujes|čuješ li me|cujes li me)\b",
        r"\b(nisi\s+(?:ti\s+)?(?:baš\s+)?pametan|glup\s+si|spor\s+si|ne\s+radiš\s+ništa|ne\s+radis\s+nista|ne\s+pričaš\s+(?:baš\s+)?(?:nešto\s+)?lepo|ne\s+pricas\s+(?:bas\s+)?(?:nesto\s+)?lepo|ja\s+bih\s+to\s+bolje)\b",
        r"^(moje ime je|zovem se|ja sam)\b",
        r"^(?:hvala|super|odlično|odlicno|lepo|bravo|pametno|legendarno)[.!?]*$",
        r"\b(pruži mi ruku|pruzi mi ruku|pruži ruku|pruzi ruku|daj mi ruku|daj ruku|rukuj|rukovanje)\b",
        r"\b(shake hand|handshake|high five|daj pet|daj peticu|petica)\b",
        r"\b(po[šs]alji mi poljubac|daj poljubac|poljubac|kiss)\b",
        r"\b(napravi srce|po[šs]alji mi srce|daj srce|srce rukama|hand heart)\b",
        r"\b(zagrljaj|zagrli|hug)\b",
        r"\b(mahni|ma[šs]i|wave|pozdravi)\b",
    )
]
_DIRECTED_QUESTION_RE = re.compile(
    r"^(?:(?:e|ej|pa|dobro|okej|ok|ovaj)\s+)*(?:"
    r"ko|šta|sta|gde|kada|kad|kako|zašto|zasto|koliko|čime|cime|"
    r"koji|koja|koje|kakav|kakva|kakvi|da li|jel|je l|možeš li|mozes li"
    r")\b",
    re.IGNORECASE,
)
_EN_DIRECTED_QUESTION_RE = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|so|uh|well)\s+)*(?:"
    r"who|what|where|when|why|how|which|whose|whom|is|are|do|does|did|"
    r"can|could|would|should|tell|show|explain|describe|give"
    r")\b",
    re.IGNORECASE,
)
_DIRECTED_REQUEST_RE = re.compile(
    r"^(?:(?:e|ej|pa|dobro|okej|ok|ovaj|uh)\s+)*(?:"
    r"reci|kaži|kazi|objasni|pokaži|pokazi|uradi|daj|pruži|pruzi|"
    r"pokreni|startuj|zaustavi|ponovi|pomozi|pričaj|pricaj|"
    r"ispričaj|ispricaj|nabroj|navedi|opiši|opisi|predstavi|upoznaj|vodi|"
    r"hajde|ajde"
    r")\b",
    re.IGNORECASE,
)
_INCOMPLETE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(a\s+)?šta znaš o\.?$",
        r"^(a\s+)?sta znas o\.?$",
        r"^(a|a\?|e|ma|pa|ovaj|znači|znaci|ajde|slušaj|slusaj|pardon)\.?$",
        r"^(?:a|ali|pa|e|dobro|okej|ok|ovaj|znači|znaci|mislim|pošto|posto)\b.{0,50}\b(?:da|što|sto|jer|ako|kad|kada|pošto|posto|dok|pa|i)\s*[.!?…]*$",
        r"^\d+\.?$",
    )
]

_INCOMPLETE_TRAILING_WORDS = {
    "a",
    "ali",
    "ako",
    "da",
    "dok",
    "i",
    "jer",
    "kad",
    "kada",
    "pa",
    "posto",
    "pošto",
    "sto",
    "što",
}
_CONTEXTUAL_QUERY_STARTS = (
    "a ",
    "a sta",
    "a šta",
    "o ",
    "sta radi",
    "šta radi",
    "njihov",
    "njihova",
    "njihovoj",
    "njegov",
    "njegova",
    "to ",
    "tome",
)
_EN_CONTEXTUAL_QUERY_STARTS = (
    "and ",
    "when",
    "where",
    "who is",
    "what is",
    "what about",
    "tell me more",
    "more about",
    "about him",
    "about her",
    "about them",
    "his ",
    "her ",
    "their ",
)
if AGENT_INTERRUPTION_MODE not in {"adaptive", "vad"}:
    logger.warning("Invalid AGENT_INTERRUPTION_MODE=%r; falling back to adaptive", AGENT_INTERRUPTION_MODE)
    AGENT_INTERRUPTION_MODE = "adaptive"

TURN_HANDLING_OPTIONS: TurnHandlingOptions  = {
    "turn_detection": "vad",
    "endpointing": {
        "mode": "fixed",
        "min_delay": AGENT_MIN_ENDPOINTING_DELAY_S,
        "max_delay": AGENT_MAX_ENDPOINTING_DELAY_S,
    },
    "interruption": {
        "enabled": True,
        "mode": AGENT_INTERRUPTION_MODE,
        "discard_audio_if_uninterruptible": not INTERRUPT_GATE,
        "min_duration": (
            FLOOR_HOLD_MIN_SPEECH_DURATION_S
            if INTERRUPT_GATE
            else AGENT_MIN_INTERRUPTION_DURATION_S
        ),
        "min_words": 0 if INTERRUPT_GATE else AGENT_MIN_INTERRUPTION_WORDS,
        "resume_false_interruption": AGENT_RESUME_FALSE_INTERRUPTION,
        "false_interruption_timeout": AGENT_FALSE_INTERRUPTION_TIMEOUT_S,
        "backchannel_boundary": AGENT_BACKCHANNEL_BOUNDARY_S,
    },
    "preemptive_generation": {
        "enabled": VOICE_SETTINGS.agent.preemptive_generation,
        "preemptive_tts": VOICE_SETTINGS.agent.preemptive_tts,
    },
}


# Gesture bridge configuration TODO - move to env vars
ACTIVE_GESTURE_SAFETY_POOL = get_configured_gesture_safety_pool()
ACTIVE_GESTURE_CATALOG_ID = VOICE_SETTINGS.gestures.catalog_id or None
logger.info(
    "Active gesture safety pool: '%s' catalog=%s",
    ACTIVE_GESTURE_SAFETY_POOL,
    ACTIVE_GESTURE_CATALOG_ID or "default",
)
ALLOWED_GESTURES = get_allowed_gestures(ACTIVE_GESTURE_SAFETY_POOL, ACTIVE_GESTURE_CATALOG_ID)
ALLOWED_GESTURE_SET = frozenset(ALLOWED_GESTURES)
VERIFIED_CONVERSATION_GESTURES = frozenset(
    candidate.internal_name for candidate in load_conversation_candidates()
)
GESTURE_LIST = build_available_gesture_text(ACTIVE_GESTURE_SAFETY_POOL, ACTIVE_GESTURE_CATALOG_ID)
GESTURE_API_URL = VOICE_SETTINGS.gestures.api_url
SUPERVISOR_API_URL = VOICE_SETTINGS.misc.supervisor_api_url
GESTURES_ENABLED = VOICE_SETTINGS.gestures.enabled
GESTURE_TOKEN_RATE_LIMIT_S = VOICE_SETTINGS.gestures.token_rate_limit_s
GESTURE_BRIDGE_PROBE_INTERVAL_S = VOICE_SETTINGS.gestures.bridge_probe_interval_s
GESTURE_BRIDGE_TIMEOUT_S = VOICE_SETTINGS.gestures.bridge_timeout_s
RAG_SPEAKING_MOTION_ENABLED = VOICE_SETTINGS.rag.speaking_motion_enabled
GENERAL_SPEAKING_MOTION_MIN_WORDS = VOICE_SETTINGS.gestures.general_speaking_motion_min_words
LINKCRAFT_SPEAKING_MOTION_TIMEOUT_S = VOICE_SETTINGS.gestures.linkcraft_speaking_motion_timeout_s
TTS_ESTIMATED_WORDS_PER_MINUTE = VOICE_SETTINGS.tts.estimated_words_per_minute
AGENT_COMMAND_TOPIC = VOICE_SETTINGS.agent.command_topic
WRAP_UP_COMMAND = "__WRAP_UP__"
GOODBYE_TEXT = VOICE_SETTINGS.agent.goodbye_text
PRODUCT_DRAW_HOOK = "product_draw_hook"
PRODUCT_HUNT_DEFAULT_PREFIX = ""

# Per-installation lexicon: company/campus/event-specific terms, names, and
# lists that change when this agent is deployed elsewhere. See
# livekit_config/locales/<lang>/install_lexicon.yaml.
_INSTALL_LEXICON = load_install_lexicon("sr")
PRODUCT_HUNT_OPTIONS = _INSTALL_LEXICON.product_hunt_options

# Camera bridge configuration
MAX_CONTEXT_IMAGES = VOICE_SETTINGS.misc.max_context_images

# --- Spoken-text guards: wrong model name + English phonetics for Serbian TTS ---
ROBOT_NAME_TTS = VOICE_SETTINGS.misc.robot_name_tts.strip()

_WRONG_MODEL_RE = re.compile(r"\b(?:agibot\s+)?(?:x2|a2)(?:\s+ultra)?\b", re.IGNORECASE)
_AGIBOT_RE = re.compile(r"\bagibot\b", re.IGNORECASE)
_TTS_FLUSH_RE = re.compile(r"[.?!…\n]")

# English words the Serbian TTS mispronounces -> Serbian phonetic spelling.
# Multi-word phrases are matched before single words.
_PHONETIC_MAP = load_phonetics("sr")
_PHONETIC_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, _PHONETIC_MAP), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_COMTRADE_PHONETIC_REPLACEMENTS = _INSTALL_LEXICON.phonetic_replacements
_CUSTOM_PRONUNCIATIONS = get_custom_transformations()
_CUSTOM_PRONUNCIATION_REPLACEMENTS = tuple(
    (
        re.compile(
            rf"(?<!\w){re.escape(source)}(?!\w)",
            re.IGNORECASE,
        ),
        replacement,
    )
    for source, replacement in sorted(
        _CUSTOM_PRONUNCIATIONS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    if source and replacement
)

def _guard_name(segment: str) -> str:
    out = _WRONG_MODEL_RE.sub(ROBOT_NAME_TTS, segment)
    out = _AGIBOT_RE.sub(ROBOT_NAME_TTS, out)
    return out

def _apply_custom_pronunciations(segment: str) -> str:
    out = segment
    for pattern, replacement in _CUSTOM_PRONUNCIATION_REPLACEMENTS:
        out = pattern.sub(lambda _match, value=replacement: value, out)
    return out

def _phoneticize(segment: str) -> str:
    out = segment
    for pattern, replacement in _COMTRADE_PHONETIC_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    return _PHONETIC_RE.sub(lambda m: _PHONETIC_MAP[m.group(0).lower()], out)

def _clean_spoken_text(segment: str, *, language: str = TTS_NORMALIZER_LANGUAGE) -> str:
    normalized = normalize_tts_text(segment, language=language)
    guarded = _guard_name(normalized)
    if not (language or "").lower().startswith("sr"):
        return guarded
    return _phoneticize(_apply_custom_pronunciations(guarded))

def _split_initial_greeting_sections(text: str) -> list[str]:
    sections: list[str] = []
    for raw_section in re.split(r"\n\s*\n+", text.strip()):
        section = re.sub(r"[ \t]*\n[ \t]*", " ", raw_section).strip()
        if section:
            sections.append(section)
    return sections or ([text.strip()] if text.strip() else [])


def _greeting_language_instructions(language_mode: str) -> str:
    normalized = language_mode if language_mode in {"en_only", "sr_only", "mixed"} else "sr_en"
    return runtime_prompt(f"language.{normalized}")


def _face_identity_greeting_instructions(
    display_name: str,
    fun_fact: str = "",
    *,
    language_mode: str = "sr_en",
) -> str:
    greeting_name = first_name_vocative(display_name)
    fun_fact = str(fun_fact or "").strip()
    language_instructions = _greeting_language_instructions(language_mode)
    strict_single_language = language_mode in {"sr_only", "en_only"}
    if fun_fact and strict_single_language:
        return runtime_prompt(
            "greeting.known_with_fact",
            display_name=display_name,
            greeting_name=greeting_name,
            fun_fact=fun_fact,
            language_instruction=language_instructions,
        )
    if fun_fact:
        return runtime_prompt(
            "greeting.known_bilingual",
            display_name=display_name,
            greeting_name=greeting_name,
            language_instruction=language_instructions,
        )
    return runtime_prompt(
        "greeting.known_without_fact",
        display_name=display_name,
        greeting_name=greeting_name,
        language_instruction=language_instructions,
    )


def _unknown_face_greeting_instructions(language_mode: str) -> str:
    return runtime_prompt(
        "greeting.unknown",
        language_instruction=_greeting_language_instructions(language_mode),
    )


def _configured_greeting_instructions(greeting: str, language_mode: str) -> str:
    return runtime_prompt(
        "greeting.configured",
        greeting=greeting,
        language_instruction=_greeting_language_instructions(language_mode),
    )


@dataclass(frozen=True)
class InitialGreetingDecision:
    state: str
    identity: dict | None = None
    configured_greeting: str = ""
    waited_ms: float = 0.0


async def _stream_initial_greeting_sections(text: str):
    for index, section in enumerate(_split_initial_greeting_sections(text)):
        if index > 0 and INITIAL_GREETING_SECTION_PAUSE_S > 0:
            await asyncio.sleep(INITIAL_GREETING_SECTION_PAUSE_S)
        yield section

def _normalize_user_intent_text(text: str) -> str:
    normalized = text.lower()
    normalized = normalized.replace("š", "s").replace("đ", "dj").replace("č", "c").replace("ć", "c").replace("ž", "z")
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"(.)\1{2,}", r"\1", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


_SESSION_INTRO_RE = re.compile(
    r"\b(?:ja\s+sam|zovem\s+se|moje\s+ime\s+je|ime\s+mi\s+je|i\s+am|i'm|my\s+name\s+is)\s+"
    r"([A-Za-zČĆŽŠĐčćžšđ]+(?:\s+[A-Za-zČĆŽŠĐčćžšđ]+)?)",
    re.IGNORECASE,
)
_SESSION_NAME_REJECTS = {
    "ajde",
    "cao",
    "ćao",
    "da",
    "dobro",
    "hej",
    "hello",
    "hi",
    "ne",
    "no",
    "ok",
    "okej",
    "pamti",
    "remember",
    "sure",
    "važi",
    "vazi",
    "yes",
    "zapamti",
    "zdravo",
}


def _format_introduced_name(name: str) -> str:
    parts = []
    for part in re.split(r"\s+", name.strip()):
        if not part:
            continue
        parts.append(part[:1].upper() + part[1:].lower())
    return " ".join(parts)


def _extract_session_introduction_name(text: str) -> str | None:
    """Return a short spoken name while an unknown face is present."""

    raw_text = str(text or "").strip()
    if not raw_text:
        return None
    match = _SESSION_INTRO_RE.search(raw_text)
    candidate = match.group(1) if match else raw_text
    candidate = re.sub(r"[^A-Za-zČĆŽŠĐčćžšđ\s-]", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" -")
    if not candidate:
        return None
    normalized = _normalize_user_intent_text(candidate)
    words = normalized.split()
    if len(words) > 2:
        return None
    if normalized in _SESSION_NAME_REJECTS:
        return None
    if any(word in _SESSION_NAME_REJECTS for word in words):
        return None
    if len(candidate.replace(" ", "")) < 3:
        return None
    return _format_introduced_name(candidate)


def _reply_shape_is_active(active_selection: dict[str, str] | None = None) -> bool:
    if not REPLY_SHAPE_ENABLED:
        return False
    if active_selection is None:
        try:
            active_selection = get_prompt_builder().get_active()
        except Exception as exc:
            logger.warning("Unable to resolve active prompt for reply shape: %s", exc)
            return False
    return (
        active_selection.get("core_mode") == "standard"
        and active_selection.get("persona") == "comtrade_host"
    )


def _estimate_tts_duration_s(text: str) -> float | None:
    """Estimate playback length from already-streamed TTS text without waiting."""
    value = str(text or "").strip()
    words = re.findall(r"\b[\w'-]+\b", value, flags=re.UNICODE)
    if not words:
        return None
    base_s = len(words) * 60.0 / TTS_ESTIMATED_WORDS_PER_MINUTE
    sentence_pause_s = len(re.findall(r"[.!?…]+", value)) * 0.28
    clause_pause_s = len(re.findall(r"[,;:]+", value)) * 0.12
    return round(base_s + sentence_pause_s + clause_pause_s, 3)

_SR_LANGUAGE_STOPWORDS = frozenset(
    {
        "sta", "sto", "je", "da", "ne", "za", "na", "ti", "vi", "mi", "moze",
        "mozes", "mozete", "hoces", "hocete", "kako", "gde", "kada", "kad",
        "zasto", "koliko", "zdravo", "cao", "hvala", "molim", "dobro",
        "hajde", "ajde", "jeste", "nije", "ovo", "ono", "ova", "ovaj",
        "koji", "koja", "koje", "reci", "recite", "objasni", "pokazi",
        "daj", "dajte", "uradi", "imas", "imate", "vam", "vas", "tebi",
        "tebe", "nas", "nam", "bas", "samo", "malo", "puno", "kao", "sam",
        "smo", "ste", "su", "bi", "bio", "bila", "bili", "hocu", "necu",
        "sve", "necu", "juce", "danas", "sutra", "sada", "sad", "ali",
        "ili", "kod", "iz", "od", "do", "pre", "posle", "sa", "u",
    }
)
_EN_LANGUAGE_STOPWORDS = frozenset(
    {
        "the", "is", "are", "what", "where", "when", "how", "why", "who",
        "you", "your", "please", "hello", "hi", "thanks", "thank", "yes",
        "no", "and", "with", "for", "this", "that", "have", "going", "see",
        "later", "can", "could", "would", "im", "channel", "channels",
        "okay", "ok", "bye", "goodbye", "will", "was", "were", "not",
        "know", "think", "want", "need", "good", "great", "sure", "right",
        "do", "does", "did", "understand", "me", "about", "tell", "speak",
        "talk", "answer", "respond",
    }
)
_SR_DIACRITICS_RE = re.compile(r"[čćžšđČĆŽŠĐ]")


def _explicit_requested_reply_language(text: str) -> str | None:
    normalized = _normalize_user_intent_text(text)
    if not normalized:
        return None

    if re.search(
        r"\b(?:engleski|engleskom|na engleskom|in english|speak english|"
        r"talk in english|answer in english|respond in english|say .* in english|"
        r"tell .* in english|translate .* to english)\b",
        normalized,
    ):
        return "en"
    if re.search(
        r"\b(?:srpski|srpskom|na srpskom|in serbian|speak serbian|"
        r"talk in serbian|answer in serbian|respond in serbian|say .* in serbian|"
        r"tell .* in serbian|translate .* to serbian)\b",
        normalized,
    ):
        return "sr"
    return None


def _query_language_evidence(text: str) -> str | None:
    """Return a language only when the latest utterance contains useful evidence."""
    if not text or not text.strip():
        return None
    explicit_language = _explicit_requested_reply_language(text)
    if explicit_language is not None:
        return explicit_language
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if not words:
        return "sr" if _SR_DIACRITICS_RE.search(text) else None
    sr_hits = sum(1 for word in words if word in _SR_LANGUAGE_STOPWORDS)
    en_hits = sum(1 for word in words if word in _EN_LANGUAGE_STOPWORDS)
    if en_hits and en_hits > sr_hits:
        return "en"
    if sr_hits and sr_hits > en_hits:
        return "sr"
    if _SR_DIACRITICS_RE.search(text):
        return "sr"
    return None


def _detect_query_language(text: str) -> str:
    """Best-effort sr/en detector for the latest user query.

    Serbian is the default: the STT hints, persona and TTS normalizer all
    assume 'sr', so this only flips to 'en' on clear evidence, to avoid
    swapping languages mid-conversation on a single borrowed word.
    """
    return _query_language_evidence(text) or "sr"


def _resolve_query_language(
    text: str,
    *,
    stt_language: str | None,
    previous_language: str = "sr",
    stt_language_mode: str = "sr_en",
) -> tuple[str, str]:
    """Resolve reply language without a model/network call.

    Lexical evidence in the latest question wins over a stale or incorrect STT
    language tag. For names and other ambiguous fragments, prefer the STT tag,
    then retain the conversation language instead of unexpectedly reverting.
    """
    forced_language = _forced_reply_language_from_stt_mode(stt_language_mode)
    if forced_language is not None:
        return forced_language, "stt_mode"
    evidence = _query_language_evidence(text)
    if evidence:
        return evidence, "text"
    if stt_language in {"sr", "en"}:
        return stt_language, "stt"
    return (previous_language if previous_language in {"sr", "en"} else "sr"), "previous"


def _forced_reply_language_from_stt_mode(stt_language_mode: str | None) -> str | None:
    mode = str(stt_language_mode or "").strip().lower()
    if mode == "en_only":
        return "en"
    if mode == "sr_only":
        return "sr"
    return None


def _build_turn_language_directive(language: str) -> str:
    if language == "en":
        return (
            "LANGUAGE: The user's latest turn is in English. Reply entirely in English. "
            "Do not answer in Serbian. Keep names and company names natural in English."
        )
    return (
        "LANGUAGE: Poslednji upit korisnika je na srpskom jeziku. Odgovori celim "
        "odgovorom na srpskom, uključujući brojeve, datume, jedinice mere i "
        "skraćenice. Ne mešaj jezike u istom odgovoru."
    )


def _normalize_stt_language(value: object) -> str | None:
    """Map a Soniox-reported language code (e.g. 'en', 'en-US', 'sr-RS') to sr/en.

    Returns None when Soniox didn't report a usable language (identification
    disabled, or an unrelated language), so callers fall back to the
    text-based heuristic instead of trusting an absent signal.
    """
    code = str(value or "").strip().lower()
    if not code:
        return None
    if code.startswith("en"):
        return "en"
    if code.startswith("sr") or code.startswith("hr") or code.startswith("bs"):
        return "sr"
    return None

def _contains_stop_command(text: str) -> bool:
    normalized = _normalize_user_intent_text(text)
    words = set(normalized.split())
    return bool(
        words & STOP_COMMAND_WORDS
        or normalized in STOP_COMMAND_PHRASES
    )

def _is_vision_query(text: str) -> bool:
    normalized = _normalize_user_intent_text(text)
    if not normalized:
        return False
    return bool(
        re.search(
            r"\b(?:sta|sto|st[aā]|shta)\s+(?:vidis|vidite|vidis li|imas pred sobom)\b",
            normalized,
        )
        or re.search(
            r"\b(?:kazi|reci|opisi|objasni)\s+(?:mi\s+)?(?:sta|sto|st[aā]|shta)\s+"
            r"(?:vidis|vidite)\b",
            normalized,
        )
        or re.search(
            r"\b(?:opisi|pogledaj)\s+(?:sliku|scenu|kameru|ispred sebe)\b",
            normalized,
        )
        or re.search(
            r"\b(?:what do you see|describe what you see|describe the image|look at the camera)\b",
            normalized,
        )
    )

def _classify_user_intent(text: str) -> str:
    normalized = _normalize_user_intent_text(text)
    if not normalized:
        return "empty"

    if _contains_stop_command(text):
        return "control"

    words = normalized.split()
    unique_words = set(words)
    compact = " ".join(words[:3])
    if normalized in _CONTROL_PHRASES or compact in _CONTROL_PHRASES:
        return "control"
    if 1 <= len(words) <= 3 and unique_words <= _CONTROL_PHRASES:
        return "control"

    if any(pattern.search(text) or pattern.search(normalized) for pattern in _CHITCHAT_PATTERNS):
        return "chitchat"
    if any(pattern.search(text) or pattern.search(normalized) for pattern in _INCOMPLETE_PATTERNS):
        return "incomplete"
    words = normalized.split()
    questionish = "?" in text or bool(_DIRECTED_QUESTION_RE.search(normalized))
    if RAG_ENGLISH_PARITY:
        questionish = questionish or bool(_EN_DIRECTED_QUESTION_RE.search(normalized))
    if re.search(r"[—–-]\s*$", text):
        if not questionish or len(words) <= 4 or words[-1] in {"o", "za", "sa", "u"}:
            return "incomplete"
    if (
        "?" not in text
        and len(words) <= 6
        and words[-1] in _INCOMPLETE_TRAILING_WORDS
    ):
        return "incomplete"

    return "knowledge"

def _normalized_phrase_set(values: set[str]) -> set[str]:
    return {_normalize_user_intent_text(value) for value in values if value}

_NORMALIZED_BACKCHANNELS = _normalized_phrase_set(RAG_SKIP_BACKCHANNELS | _CONTROL_PHRASES)

def _is_backchannel(text: str) -> bool:
    normalized = _normalize_user_intent_text(text)
    if not normalized:
        return True
    if normalized in _NORMALIZED_BACKCHANNELS:
        return True
    words = normalized.split()
    return len(words) <= 3 and all(word in _NORMALIZED_BACKCHANNELS for word in words)

def _is_affirmative_followup(text: str) -> bool:
    normalized = _normalize_user_intent_text(text)
    return normalized in {
        _normalize_user_intent_text(value)
        for value in _AFFIRMATIVE_FOLLOWUPS
    }

_MEMORY_FACT_PATTERNS = (
    ("ime", re.compile(r"\b(?:moje ime je|zovem se)\s+([^,.!?;:]{1,60})", re.IGNORECASE)),
    ("poreklo", re.compile(r"\b(?:dolazim iz|ja sam iz|poreklom sam iz)\s+([^,.!?;:]{1,60})", re.IGNORECASE)),
    ("prebivalište", re.compile(r"\b(?:živim u|zivim u|stanujem u)\s+([^,.!?;:]{1,60})", re.IGNORECASE)),
    ("posao", re.compile(r"\b(?:radim u|radim za|zaposlen sam u|zaposlena sam u)\s+([^,.!?;:]{1,80})", re.IGNORECASE)),
)

def _extract_conversation_facts(text: str) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    for label, pattern in _MEMORY_FACT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = re.sub(r"\s+", " ", match.group(1)).strip(" -")
        value = re.split(
            r"\s+(?:i\s+)?(?:radim|živim|zivim|stanujem|zaposlen(?:a)?\s+sam)\b",
            value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        if value:
            facts.append((label, value))
    return facts

def _robot_address_terms() -> set[str]:
    raw_terms = _INSTALL_LEXICON.wake_words | {
        ROBOT_NAME_TTS,
        VOICE_SETTINGS.misc.robot_name_tts,
    }
    return {
        normalized
        for value in raw_terms
        if (normalized := _normalize_user_intent_text(value))
    }

def _has_explicit_robot_address(text: str) -> bool:
    normalized = _normalize_user_intent_text(text)
    if not normalized:
        return False
    padded = f" {normalized} "
    return any(f" {term} " in padded for term in _robot_address_terms())

def _looks_directed_at_robot(text: str, intent: str) -> bool:
    if not ADDRESSEE_GATE_ENABLED or not ADDRESSEE_REQUIRE_DIRECTED_TURN:
        return True
    if _has_explicit_robot_address(text):
        return True
    if intent == "chitchat":
        return True
    if "?" in text:
        return True
    normalized = _normalize_user_intent_text(text)
    if len(normalized.split()) == 1 and normalized in {
        "ko",
        "sta",
        "kako",
        "gde",
        "kad",
        "zasto",
        "koliko",
    }:
        return False
    return bool(
        _DIRECTED_QUESTION_RE.search(normalized)
        or (RAG_ENGLISH_PARITY and _EN_DIRECTED_QUESTION_RE.search(normalized))
        or _DIRECTED_REQUEST_RE.search(normalized)
    )


def _loose_engagement_decision(
    query: str,
    intent: str,
    *,
    active: bool,
) -> tuple[bool, str]:
    """Return the content-only ACTIVE/COLD acceptance decision.

    Empty, incomplete, control, and backchannel turns are filtered by the
    caller first. ACTIVE therefore deliberately favors recall; COLD retains
    the existing directed-pattern/name protection against room chatter.
    """
    if active:
        return True, "active_continuation"
    directed = _looks_directed_at_robot(query, intent)
    return directed, "cold_directed_pattern" if directed else "cold_not_directed"


def _matched_turn_signal(query: str, intent: str) -> str:
    if _has_explicit_robot_address(query):
        return "robot_name"
    if _DIRECTED_REQUEST_RE.search(_normalize_user_intent_text(query)):
        return "request_word"
    normalized = _normalize_user_intent_text(query)
    if (
        _DIRECTED_QUESTION_RE.search(normalized)
        or (RAG_ENGLISH_PARITY and _EN_DIRECTED_QUESTION_RE.search(normalized))
        or "?" in query
    ):
        return "question_word"
    if intent == "chitchat":
        return "social_pattern"
    return "continuation"

def _should_attempt_rag(query: str, intent: str) -> bool:
    if intent != "knowledge" or _is_backchannel(query):
        return False
    if is_self_identity_question(query):
        return True
    # An explicitly named person is a high-value knowledge-base lookup even
    # when the surrounding wording is a continuation ("nastavi o ...") that
    # does not otherwise look like a standalone directed question.
    if _contains_probable_person_name(query):
        return True
    # STT frequently gives only a first name ("koja je Alexis"), but an
    # explicit person-question cue still makes this a high-value KB lookup.
    normalized = _normalize_user_intent_text(query)
    if re.search(
        r"\b(?:ko|koja|koji|koje)\s+je\s+[a-z]{3,}\b|"
        r"\b(?:pricaj|reci|kazi)\s+mi\b.*\b[a-z]{3,}\b",
        normalized,
    ):
        return True
    # Visitors often ask wayfinding as a trailing fragment after STT splits the
    # utterance ("Gde su..." -> "Kafici u Comtrade-u."). These are high-value
    # reception lookups even without a question mark or request verb.
    if _contains_wayfinding_rag_keyword(query):
        return True
    if not RAG_SELECTIVE_GATE_ENABLED:
        return True
    words = _normalize_user_intent_text(query).split()
    if len(words) < RAG_QUERY_MIN_WORDS:
        return False
    return _looks_directed_at_robot(query, intent)

def _contains_probable_person_name(query: str) -> bool:
    name_token = r"[A-ZČĆŽŠĐ][a-zčćžšđ]{2,}"
    return bool(re.search(rf"\b{name_token}\s+{name_token}\b", query))


def _is_person_lookup_query(query: str) -> bool:
    normalized = _normalize_user_intent_text(query)
    if is_self_identity_question(query) or _contains_probable_person_name(query):
        return True
    if bool(
        re.search(
            r"\b(?:ko|koja|koji|koje)\s+je\s+[a-zčćžšđ]{3,}\b|"
            r"\b(?:sta|šta)\s+znas?\s+o\s+[a-zčćžšđ]{3,}\b|"
            r"\b(?:pricaj|reci|kazi|kaži)\s+mi\b.*\b[a-zčćžšđ]{3,}\b",
            normalized,
        )
    ):
        return True
    if not RAG_ENGLISH_PARITY:
        return False
    return bool(
        re.search(
            r"\bwho\s+is\s+[a-z]{3,}\b|"
            r"\btell\s+me\s+(?:more\s+)?about\s+[a-z]{3,}\b|"
            r"\bwhat\s+do\s+you\s+know\s+about\s+[a-z]{3,}\b|"
            r"\bmore\s+about\s+[a-z]{3,}\b|"
            r"\btell\s+me\s+about\s+the\s+(?:ceo|founder|president|chairman)\b",
            normalized,
        )
    )


_WAYFINDING_RAG_KEYWORDS = _INSTALL_LEXICON.wayfinding_keywords
_WAYFINDING_AMBIGUOUS_KEYWORDS = _INSTALL_LEXICON.wayfinding_ambiguous_keywords
_WAYFINDING_DETAIL_TERMS = _INSTALL_LEXICON.host_detail_terms


def _contains_wayfinding_rag_keyword(query: str) -> bool:
    words = set(_normalize_user_intent_text(query).split())
    if words & _WAYFINDING_RAG_KEYWORDS:
        return True
    return bool(words & _WAYFINDING_AMBIGUOUS_KEYWORDS) and bool(
        words & _WAYFINDING_DETAIL_TERMS
    )


_DANGLING_MULTIQUERY_RE = re.compile(
    r"(?:\b(?:i|pa)\s+)?(?:posle|poslije|nakon)\s+toga\b.*"
    r"\b(?:mi\s+)?(?:odmah\s+)?(?:reci|kaži|kazi|objasni|pokaži|pokazi|"
    r"ispričaj|ispricaj|navedi|gde|gdje|ko|šta|sta)\s*[—–-]*\s*$|"
    r"\b(?:i|pa)\s+onda\b.*"
    r"\b(?:mi\s+)?(?:odmah\s+)?(?:reci|kaži|kazi|objasni|pokaži|pokazi|"
    r"ispričaj|ispricaj|navedi|gde|gdje|ko|šta|sta)\s*[—–-]*\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)


def _is_dangling_multiquery(query: str) -> bool:
    normalized = _normalize_user_intent_text(query)
    if len(normalized.split()) < 5:
        return False
    return bool(_DANGLING_MULTIQUERY_RE.search(query.strip()))


def _can_complete_pending_multiquery(query: str) -> bool:
    if not query.strip() or _is_backchannel(query):
        return False
    intent = _classify_user_intent(query)
    return (
        _looks_directed_at_robot(query, intent)
        or _contains_probable_person_name(query)
        or _contains_wayfinding_rag_keyword(query)
    )


_RAG_LEADING_DISCOURSE_RE = re.compile(
    r"^\s*(?:(?:jesam|da|dobro|okej|u\s+redu|aha)\s*[.!?,;:—-]+\s*)+"
    r"(?=(?:ko|šta|sta|gde|kako|kada|kad|zašto|zasto|koji|koja|koje|"
    r"reci|kaži|kazi)\b)",
    flags=re.IGNORECASE | re.UNICODE,
)


def _strip_rag_leading_discourse(query: str) -> str:
    """Remove a standalone acknowledgement before the actual search question."""
    stripped = _RAG_LEADING_DISCOURSE_RE.sub("", query, count=1).strip()
    return stripped or query.strip()


_MULTI_RAG_QUESTION_WORD_RE = re.compile(
    r"\b(?:where|who|what|when|how|which|gde|gdje|ko|šta|sta|kada|kad|"
    r"kako|koliko|koji|koja|koje)\b",
    flags=re.IGNORECASE | re.UNICODE,
)
_MULTI_RAG_SEPARATOR_RE = re.compile(
    r"(?:[?;,]\s*|\b(?:and|i|pa)\s+)(?=(?:where|who|what|when|how|which|"
    r"gde|gdje|ko|šta|sta|kada|kad|kako|koliko|koji|koja|koje)\b)",
    flags=re.IGNORECASE | re.UNICODE,
)


def _split_rag_subqueries(query: str, *, max_queries: int) -> tuple[str, ...]:
    """Split repeated, explicit question clauses without an extra model call."""
    clean = " ".join(str(query or "").split()).strip()
    if not clean or max_queries < 2:
        return (clean,) if clean else ()
    first_question = _MULTI_RAG_QUESTION_WORD_RE.search(clean)
    if first_question is None:
        return (clean,)

    question_text = clean[first_question.start():]
    parts = [part.strip(" ,;?.") for part in _MULTI_RAG_SEPARATOR_RE.split(question_text)]
    parts = [part for part in parts if len(_normalize_user_intent_text(part).split()) >= 2]
    if len(parts) < 2:
        return (clean,)
    return tuple(dict.fromkeys(parts))[:max_queries]


def _rag_query_cache_key(queries: tuple[str, ...]) -> str:
    return " || ".join(query.strip() for query in queries if query.strip())


def _needs_contextual_rag_query(query: str) -> bool:
    normalized = _normalize_user_intent_text(query)
    words = normalized.split()
    contextual_starts = _CONTEXTUAL_QUERY_STARTS
    if RAG_ENGLISH_PARITY:
        contextual_starts = contextual_starts + _EN_CONTEXTUAL_QUERY_STARTS
    return (
        len(words) <= 5
        or normalized.startswith(contextual_starts)
        or any(word in {"njihovoj", "njihova", "njihov", "njegovoj", "njegova", "tome"} for word in words)
    )

def _is_contextual_followup(query: str) -> bool:
    # A short but independently searchable facility subject is a new lookup,
    # not a pronoun follow-up to the previous location (coffee -> canteen).
    if _contains_wayfinding_rag_keyword(query) and _MULTI_RAG_QUESTION_WORD_RE.search(query):
        return False
    words = set(_normalize_user_intent_text(query).split())
    normalized = _normalize_user_intent_text(query)
    contextual_starts = _CONTEXTUAL_QUERY_STARTS
    contextual_terms = {
        "osoba",
        "osobu",
        "licnost",
        "njega",
        "njemu",
        "njoj",
        "njih",
        "to",
        "tome",
        "taj",
        "ta",
        "ono",
    }
    if RAG_ENGLISH_PARITY:
        contextual_starts = contextual_starts + _EN_CONTEXTUAL_QUERY_STARTS
        contextual_terms = contextual_terms | {
            "it",
            "him",
            "her",
            "them",
            "more",
            "about",
            "there",
            "directions",
            "direction",
            "hours",
            "when",
            "where",
        }
    return len(words) <= 6 and bool(
        normalized.startswith(contextual_starts)
        or words & contextual_terms
    )

def _expand_common_stt_confusions(query: str) -> str:
    normalized = _normalize_user_intent_text(query)
    words = set(normalized.split())
    return _INSTALL_LEXICON.expand_stt_confusions(query, normalized=normalized, words=words)


_HOST_LOOKUP_QUERY_TERMS = _INSTALL_LEXICON.host_lookup_terms
_HOST_DETAIL_QUERY_TERMS = _INSTALL_LEXICON.host_detail_terms
_HOST_FACT_CLAIM_TERMS = _INSTALL_LEXICON.host_fact_claim_terms
_HOST_UNSUPPORTED_DETAIL_PATTERNS = list(_INSTALL_LEXICON.host_unsupported_detail_patterns)
_HOST_NUMERIC_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\b")
_HOST_CONTEXT_EQUIVALENTS = _INSTALL_LEXICON.host_context_equivalents


def _is_host_lookup_query(query: str) -> bool:
    words = set(_normalize_user_intent_text(query).split())
    if _contains_wayfinding_rag_keyword(query) or _is_person_lookup_query(query):
        return True
    return bool(words & _HOST_LOOKUP_QUERY_TERMS) and bool(words & _HOST_DETAIL_QUERY_TERMS)


def _is_wayfinding_host_query(query: str) -> bool:
    words = set(_normalize_user_intent_text(query).split())
    if _is_person_lookup_query(query):
        return False
    return _contains_wayfinding_rag_keyword(query) or (
        bool(words & _HOST_LOOKUP_QUERY_TERMS)
        and bool(words & _HOST_DETAIL_QUERY_TERMS)
    )


def _should_apply_claim_grounding(query: str) -> bool:
    if not CLAIM_GROUNDING_TTS_FILTER_ENABLED:
        return False
    if RAG_CLAIM_GROUNDING_SCOPE == "wayfinding_only":
        return _is_wayfinding_host_query(query) and not _is_person_lookup_query(query)
    return _is_host_lookup_query(query)


def _host_claim_has_support(segment: str, context: str) -> bool:
    normalized_segment = _normalize_user_intent_text(segment)
    normalized_context = _normalize_user_intent_text(context)
    if not normalized_segment or not normalized_context:
        return False
    context_terms = normalized_context.split()
    context_equivalents: list[str] = []
    for word in context_terms:
        equivalent = _HOST_CONTEXT_EQUIVALENTS.get(word)
        if equivalent:
            context_equivalents.append(equivalent)
    if context_equivalents:
        normalized_context = (
            normalized_context
            + " "
            + _normalize_user_intent_text(" ".join(context_equivalents))
        )
    if normalized_segment in normalized_context:
        return True

    segment_numbers = _HOST_NUMERIC_RE.findall(segment)
    if segment_numbers:
        context_numbers = set(_HOST_NUMERIC_RE.findall(context))
        if any(number not in context_numbers for number in segment_numbers):
            return False

    segment_words = {
        word
        for word in normalized_segment.split()
        if len(word) >= 4 and word not in (_SR_LANGUAGE_STOPWORDS | _EN_LANGUAGE_STOPWORDS)
    }
    if not segment_words:
        return True
    context_words = set(normalized_context.split())
    overlap = segment_words & context_words
    return (len(overlap) / max(1, len(segment_words))) >= 0.50


def _filter_host_grounded_tts_segment(
    segment: str,
    context: str,
    *,
    language: str,
    abstention_already_added: bool = False,
) -> tuple[str, bool, bool]:
    """Drop unsupported local-host factual claims from a TTS segment.

    This intentionally uses only the already-retrieved RAG context. It is a
    low-latency guardrail, not a second retrieval/rerank pass.
    """

    stripped = segment.strip()
    if not stripped or not context.strip():
        return segment, False, False

    normalized_segment = _normalize_user_intent_text(stripped)
    words = set(normalized_segment.split())
    has_host_claim = bool(words & _HOST_FACT_CLAIM_TERMS) or bool(
        pattern.search(stripped) for pattern in _HOST_UNSUPPORTED_DETAIL_PATTERNS
    )
    if not has_host_claim:
        return segment, False, False

    unsupported = any(
        pattern.search(stripped)
        and _normalize_user_intent_text(pattern.search(stripped).group(0))
        not in _normalize_user_intent_text(context)
        for pattern in _HOST_UNSUPPORTED_DETAIL_PATTERNS
    )
    if not unsupported:
        unsupported = not _host_claim_has_support(stripped, context)
    if not unsupported:
        return segment, False, False

    if abstention_already_added:
        return "", True, False
    fallback = (
        "For that exact detail I do not have reliable information in the current context; please check at reception."
        if language == "en"
        else "Za taj tačan detalj nemam pouzdan podatak u trenutnom kontekstu; proverite na recepciji."
    )
    suffix = " " if segment.endswith((" ", "\n")) else ""
    return fallback + suffix, True, True

def _build_base_instructions(
    gesture_processor: GestureTokenProcessor | None = None,
    person_name_prompt: str = "",
) -> str:
    gesture_policy_prompt = ""
    inline_gesture_policy_prompt = ""
    if gesture_processor is not None and gesture_processor.reachable:
        gesture_policy_prompt = build_gesture_policy_prompt(
            ACTIVE_GESTURE_SAFETY_POOL,
            ACTIVE_GESTURE_CATALOG_ID,
        )
        inline_gesture_policy_prompt = (
            build_intent_prompt(True)
            if GESTURE_INTENT_MAP
            else build_inline_gesture_policy_prompt(gesture_processor.active_gestures)
        )

    base_instructions = build_system_prompt(
        extra_variables={
            "gesture_policy_prompt": gesture_policy_prompt,
            "inline_gesture_policy_prompt": inline_gesture_policy_prompt,
            "contextual_gesture_names": (
                gesture_processor.active_contextual_motions
                if gesture_processor is not None and gesture_processor.reachable
                else ()
            ),
            "person_name_prompt": person_name_prompt,
        }
    )
    language_directive = _stt_language_mode_response_directive(
        VOICE_SETTINGS.misc.stt_language_mode
    )
    return base_instructions + language_directive


# Agent configuration
BASE_INSTRUCTIONS = _build_base_instructions()

# Dataclasses for agent state
@dataclass(frozen=True)
class CommandStep:
    text: str = ""
    gesture: str | None = None
    pause_after_ms: int | None = None
    force_gesture: bool = False


class VisionApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        payload: dict | None = None,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {}
        self.user_message = user_message or "Trenutno ne mogu da pristupim sistemu za prepoznavanje lica."


class HumanoidAgent(Agent):

    def __init__(
        self,
        chat_ctx: ChatContext | None = None,
        *,
        visual_ui_runtime: AgentVisualUiRuntime | None = None,
        gesture_processor: GestureTokenProcessor | None = None,
        fast_llm=None,
        room_summary_llm=None,
    ) -> None:

        self._gesture_processor = gesture_processor or GestureTokenProcessor(
            bridge_url=GESTURE_API_URL,
            safe_gestures=ALLOWED_GESTURES,
            enabled=GESTURES_ENABLED,
            min_interval_s=GESTURE_TOKEN_RATE_LIMIT_S,
            request_timeout_s=GESTURE_BRIDGE_TIMEOUT_S,
            probe_interval_s=GESTURE_BRIDGE_PROBE_INTERVAL_S,
        )
        # Explicit user motion requests must not depend on the LLM remembering
        # to emit an inline marker.  The semantic catalog only resolves motions
        # already exposed by the active supervisor pool, and the runtime keeps
        # the existing one-motion/cooldown contract.  GESTURE_INTENT_MAP still
        # controls autonomous conversational intent markers separately.
        self._gesture_intent_runtime = (
            GestureIntentRuntime(
                verified_names=ALLOWED_GESTURES,
                dispatch=lambda motion: self._call_gesture_api(
                    motion,
                    requested=True,
                    force_gesture=False,
                ),
                dispatch_explicit_linkcraft=self._call_explicit_linkcraft_motion,
                cooldown_s=GESTURE_TOKEN_RATE_LIMIT_S,
            )
            if GESTURES_ENABLED
            else None
        )
        self._base_instructions = _build_base_instructions(self._gesture_processor)
        self._stop_requested: asyncio.Event = asyncio.Event()
        self._tasks = []
        self._visual_ui_runtime = visual_ui_runtime
        self._fast_llm = fast_llm
        self._room_summary_llm = room_summary_llm
        self._llm_role = "question_factual"
        self._reply_shape_active = _reply_shape_is_active()
        self._consecutive_skipped_turns = 0
        self._conversation_facts: OrderedDict[str, str] = OrderedDict()
        self._conversation_memory_message_id: str | None = None
        self._conversation_context_message_id: str | None = None
        self._conversation_context = ConversationContextBuffer(
            max_messages=CONVERSATION_CONTEXT_BUFFER_SIZE,
            recent_count=CONVERSATION_CONTEXT_RECENT_MESSAGES,
            summary_max_words=CONVERSATION_CONTEXT_SUMMARY_MAX_WORDS,
            summary_every_messages=CONVERSATION_CONTEXT_SUMMARY_EVERY_MESSAGES,
        )
        self._last_directed_query = ""
        self._last_directed_query_ts = 0.0
        self._last_rag_topic = ""
        self._last_rag_topic_ts = 0.0
        self._pending_multiquery_prefix = ""
        self._pending_multiquery_ts = 0.0
        self._last_agent_speech_ended_ts = 0.0
        self._last_engagement_activity_ts = 0.0
        self._agent_was_speaking = False
        self._trigger_word_runtime_enabled = (
            TRIGGER_WORD_DEFAULT_ENABLED if TRIGGER_WORD_FEATURE_ENABLED else False
        )
        self._trigger_word = TriggerWordEngagement(
            TRIGGER_WORD_VARIANTS,
            TRIGGER_WORD_ENGAGEMENT_TIMEOUT_S,
        )
        self._latest_image: Optional[ImageContent] = None
        self._image_lock = asyncio.Lock()
        self._handler_registered = False
        self._transcription_handler_registered = False
        self._transcription_handler = None
        self._speech_created_handler = None
        self._agent_state_handler = None
        self._user_state_handler = None
        self._conversation_item_handler = None
        self._protected_speech_active = False
        self._barge_in_started_at = 0.0
        self._last_interrupt_gate_text = ""
        self._last_interrupt_gate_ts = 0.0
        self._floor_hold_final_text = ""
        self._floor_hold_final_ts = 0.0
        self._floor_hold_final_decision = None
        self._floor_hold_metrics: Counter[str] = Counter()
        self._primary_speaker_id = PRIMARY_SPEAKER_ID or None
        self._current_tts_text = ""
        self._current_speech_handle = None
        self._last_interrupted_spoken_text = ""
        self._last_interrupted_unspoken_text = ""
        self._last_hard_stop_ts = 0.0
        self._last_hard_stop_text = ""
        self._image_history: Deque[tuple[object, ImageContent]] = deque()
        self._max_context_images = MAX_CONTEXT_IMAGES
        self._command_lock = asyncio.Lock()
        self._rag_config = RagConfig()
        self._rag_client: RAGServiceClient | None = None
        self._rag_next_retry_ts = 0.0
        self._rag_init_lock = asyncio.Lock()
        self._rag_message_id: str | None = None
        self._rag_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._rag_prefetch_task: asyncio.Task[str] | None = None
        self._rag_prefetch_query = ""
        self._rag_prefetch_resolved_query = ""
        self._rag_speaking_motion_pending = False
        self._rag_speaking_motion_resource_name = ""
        self._rag_speaking_motion_dispatched = False
        self._contextual_motion_blocked = False
        self._contextual_motion_selected_for_response = False
        self._contextual_motion_fallback_family = ""
        self._known_person_rag_turn_ids: set[str] = set()
        self._claim_grounding_active = False
        self._claim_grounding_context = ""
        self._claim_grounding_query = ""
        self._claim_grounding_abstention_added = False
        self._last_query_language = (
            _forced_reply_language_from_stt_mode(VOICE_SETTINGS.misc.stt_language_mode)
            or "sr"
        )
        self._last_stt_language = ""
        self._room_summary = ""
        self._room_summary_turns: Deque[str] = deque(
            maxlen=ROOM_SUMMARY_CONTEXT_TURNS
        )
        self._room_summary_eligible_since_refresh = 0
        self._room_summary_refresh_count = 0
        self._room_summary_task: asyncio.Task | None = None
        self._room_summary_message_id: str | None = None
        self._room_summary_call_active = False
        self._room_summary_cancel_requests = 0
        self._directed_turn_pending = False
        self._turn_gate_v2 = TwoAxisTurnGate(
            robot_names=tuple(_robot_address_terms()),
            engagement_timeout_s=(
                RECALL_ENGAGE_TIMEOUT_S
                if RECALL_BIAS
                else ENGAGE_WINDOW_S
                if ENGAGE_WINDOW_20S
                else GATE_V2_ENGAGEMENT_TIMEOUT_S
            ),
            recall_bias=RECALL_BIAS,
            third_person_filter_inside_engagement=ENGAGE_WINDOW_20S,
        )
        self._suppress_next_reply = False
        self._rag_runtime_enabled: bool | None = None  
        self._survey_running = False
        self._quiz_running = False
        self._quiz_default_enabled = VOICE_SETTINGS.misc.quiz_runtime_enabled
        self._survey_default_enabled = VOICE_SETTINGS.misc.survey_runtime_enabled
        self._quiz_runtime_enabled: bool | None = None
        self._survey_runtime_enabled: bool | None = None
        self._current_identity: dict | None = None
        self._current_known_person = None
        self._face_enrollment_running = False
        self._face_enrollment_authorized_until = 0.0
        self._face_enrollment_needs_explicit_consent = False
        self._identity_monitor_task: asyncio.Task | None = None
        self._last_greeted_face_key = ""
        self._pending_unknown_face = False
        self._introduced_identity_name = ""
        self._initial_greeting_override: str | None = None
        self._suppress_initial_greeting = False
        self._initial_greeting_started = False
        self._initial_greeting_completed = False

        super().__init__(
            instructions=self._render_instructions(),
            chat_ctx=chat_ctx,
        )

    def _track_task(self, task: asyncio.Task) -> asyncio.Task:
        self._tasks.append(task)
        task.add_done_callback(self._discard_task)
        return task

    def _discard_task(self, task: asyncio.Task) -> None:
        try:
            self._tasks.remove(task)
        except ValueError:
            pass


    async def _ensure_rag_client(self) -> RAGServiceClient | None:
        cfg = self._rag_config
        if not self._is_rag_enabled():
            return None
        if self._rag_client is not None:
            return self._rag_client

        now = time.monotonic()
        if now < self._rag_next_retry_ts:
            return None

        async with self._rag_init_lock:
            if self._rag_client is not None:
                return self._rag_client

            now = time.monotonic()
            if now < self._rag_next_retry_ts:
                return None
            if not self._is_rag_enabled():
                return None

            try:
                client = RAGServiceClient(
                    base_url=cfg.api_url,
                    search_path=cfg.search_path,
                    health_path=cfg.health_path,
                    timeout_s=(
                        min(cfg.timeout_s, RAG_SEARCH_BUDGET_S)
                        if RAG_SELECTIVE_GATE_ENABLED
                        else cfg.timeout_s
                    ),
                    top_k=cfg.top_k,
                    include_scores=cfg.include_scores,
                )
                if cfg.healthcheck_on_init:
                    await client.ahealthcheck()

                self._rag_client = client
                return self._rag_client
            except Exception as exc:
                self._rag_client = None
                self._rag_next_retry_ts = now + cfg.retry_seconds
                logger.warning("RAG client init failed: %s (retry in %ss)", exc, cfg.retry_seconds)
                return None

    def _floor_hold_speaker_match(self, speaker_id: str | None) -> bool | None:
        if not PRIMARY_SPEAKER_ID_AVAILABLE:
            return None
        if not speaker_id or not self._primary_speaker_id:
            return None
        return speaker_id == self._primary_speaker_id

    def _classify_floor_hold(
        self,
        transcript: str,
        *,
        event_created_at: float,
        speaker_id: str | None,
    ):
        speech_duration_s = None
        if self._barge_in_started_at > 0.0:
            speech_duration_s = max(
                0.0,
                event_created_at - self._barge_in_started_at,
            )
        return classify_interruption(
            transcript,
            gate=self._turn_gate_v2,
            playing_text=self._current_tts_text,
            speech_duration_s=speech_duration_s,
            min_speech_duration_s=FLOOR_HOLD_MIN_SPEECH_DURATION_S,
            min_content_words=FLOOR_HOLD_MIN_CONTENT_WORDS,
            echo_guard_enabled=not CLIENT_AEC_ENABLED,
            echo_similarity_threshold=FLOOR_HOLD_ECHO_SIMILARITY,
            speaker_matches_primary=self._floor_hold_speaker_match(speaker_id),
            topic_overlap_min_words=FLOOR_HOLD_TOPIC_OVERLAP_MIN_WORDS,
        )

    async def on_enter(self):
        """Called when agent joins the room. Register image and command handlers."""
        if self._handler_registered:
            return
        
        def _image_received_handler(reader, participant_identity):
            task = asyncio.create_task(
                self._image_received(reader, participant_identity)
            )
            self._track_task(task)

        def _command_received_handler(reader, participant_identity):
            task = asyncio.create_task(
                self._command_received(reader, participant_identity)
            )
            self._track_task(task)

        def _user_input_transcribed_handler(ev):
            transcript = str(getattr(ev, "transcript", "") or "")
            if not transcript:
                return
            if ROOM_SUMMARY:
                self._cancel_room_summary(reason="human_speech")
            system_audio_contaminated = False
            if STT_SEGMENT_FIX:
                filtered_transcript, removed = strip_system_audio(transcript)
                if removed:
                    system_audio_contaminated = True
                    logger.info(
                        "SYSTEM_AUDIO_FILTER stage=%s action=%s phrases=%s "
                        "original=%r remaining=%r",
                        "final_transcript"
                        if bool(getattr(ev, "is_final", False))
                        else "interim_transcript",
                        "dropped" if not filtered_transcript else "stripped",
                        ",".join(removed),
                        transcript,
                        filtered_transcript,
                    )
                    transcript = filtered_transcript
                    if not transcript:
                        return
            is_final = bool(getattr(ev, "is_final", False))
            event_created_at = float(getattr(ev, "created_at", time.time()))
            speaker_id = str(getattr(ev, "speaker_id", "") or "") or None
            if is_final:
                stt_language = _normalize_stt_language(getattr(ev, "language", None))
                if stt_language:
                    self._last_stt_language = stt_language
                if (
                    PRIMARY_SPEAKER_ID_AVAILABLE
                    and not self._primary_speaker_id
                    and speaker_id
                    and has_explicit_addressee(transcript)
                ):
                    self._primary_speaker_id = speaker_id
                    logger.info(
                        "FLOOR_HOLD primary_speaker_bound speaker_id=%r "
                        "source=explicit_directed_turn",
                        speaker_id,
                    )
            if is_final and ROOM_SUMMARY:
                summary_filtered, summary_removed = strip_system_audio(transcript)
                summary_intent = classify_gate_v2_intent(transcript)
                summary_eligible = (
                    not system_audio_contaminated
                    and not summary_removed
                    and bool(summary_filtered)
                    and summary_intent not in {"backchannel", "system_audio"}
                    and not looks_like_self_echo(
                        summary_filtered,
                        self._current_tts_text,
                    )
                )
                if summary_eligible:
                    self._observe_room_summary(summary_filtered)
            if (
                is_final
                and TRIGGER_WORD_FEATURE_ENABLED
                and self._trigger_word_runtime_enabled
            ):
                previous_summary_version = (
                    self._conversation_context.summary_version
                )
                contains_trigger = self._trigger_word.strip_trigger(
                    transcript
                ).found
                added = self._conversation_context.add(
                    transcript,
                    update_summary=not contains_trigger,
                )
                summary_refreshed = (
                    self._conversation_context.summary_version
                    != previous_summary_version
                )
                logger.info(
                    "CONVERSATION_CONTEXT buffered=%d added=%s "
                    "contains_trigger=%s summary_refreshed=%s "
                    "summary_version=%d",
                    len(self._conversation_context),
                    added,
                    contains_trigger,
                    summary_refreshed,
                    self._conversation_context.summary_version,
                )
            speaking_now = (
                INTERRUPT_GATE
                and getattr(self.session, "agent_state", "") == "speaking"
            )
            recent_overlap = (
                INTERRUPT_GATE
                and self._barge_in_started_at > 0.0
                and event_created_at - self._barge_in_started_at
                <= FLOOR_HOLD_DECISION_TTL_S
            )
            floor_hold_candidate = speaking_now or recent_overlap
            if floor_hold_candidate:
                barge_started_at = self._barge_in_started_at
                decision = self._classify_floor_hold(
                    transcript,
                    event_created_at=event_created_at,
                    speaker_id=speaker_id,
                )
                if is_final:
                    self._floor_hold_final_text = _normalize_user_intent_text(
                        transcript
                    )
                    self._floor_hold_final_ts = time.monotonic()
                    self._floor_hold_final_decision = decision
                task = asyncio.create_task(
                    self._handle_interruption_transcript(
                        transcript,
                        is_final=is_final,
                        event_created_at=event_created_at,
                        decision=decision,
                        barge_started_at=barge_started_at,
                    )
                )
                self._track_task(task)
                if is_final:
                    self._barge_in_started_at = 0.0
            elif _contains_stop_command(transcript):
                self._conversation_context.clear()
                task = asyncio.create_task(
                    self._hard_stop_from_transcript(transcript, is_final=is_final)
                )
                self._track_task(task)
            if (
                RAG_ASYNC
                and is_final
                and not floor_hold_candidate
                and not (
                    TRIGGER_WORD_FEATURE_ENABLED
                    and self._trigger_word_runtime_enabled
                )
            ):
                self._start_rag_prefetch(transcript)

        def _speech_created_handler(ev):
            if not INTERRUPT_GATE:
                return
            handle = getattr(ev, "speech_handle", None)
            if handle is None:
                return
            logger.debug(
                "INTERRUPT_GATE speech_registered speech_id=%s",
                getattr(handle, "id", getattr(handle, "speech_id", "unknown")),
            )

        def _agent_state_changed_handler(ev):
            new_state = str(getattr(ev, "new_state", ""))
            if new_state == "speaking":
                self._agent_was_speaking = True
                handle = getattr(self.session, "current_speech", None)
                if INTERRUPT_GATE and handle is not None:
                    try:
                        handle.allow_interruptions = False
                        self._protected_speech_active = True
                        self._current_speech_handle = handle
                        logger.info(
                            "INTERRUPT_GATE speech_protected speech_id=%s "
                            "stage=playback_started",
                            getattr(
                                handle,
                                "id",
                                getattr(handle, "speech_id", "unknown"),
                            ),
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to protect active speech from VAD "
                            "interruption: %s",
                            exc,
                        )
                if self._gesture_intent_runtime is not None:
                    self._gesture_intent_runtime.on_first_audio()
                if (
                    RAG_SPEAKING_MOTION_ENABLED
                    and self._rag_speaking_motion_pending
                    and self._current_tts_text.strip()
                ):
                    estimated_duration_s = _estimate_tts_duration_s(
                        self._current_tts_text
                    )
                    self._schedule_rag_speaking_motion(
                        max(8.0, estimated_duration_s or 0.0),
                        reason="playback-started",
                    )
            elif self._agent_was_speaking:
                self._agent_was_speaking = False
                self._last_agent_speech_ended_ts = time.monotonic()
                self._last_engagement_activity_ts = self._last_agent_speech_ended_ts
                self._gesture_processor.cancel_explanations()
                if self._gesture_intent_runtime is not None:
                    self._gesture_intent_runtime.on_speech_end()
            if new_state != "speaking":
                self._protected_speech_active = False
            if ROOM_SUMMARY and new_state in {"idle", "listening"}:
                self._directed_turn_pending = False
                self._maybe_schedule_room_summary()

        def _user_state_changed_handler(ev):
            new_state = str(getattr(ev, "new_state", ""))
            if ROOM_SUMMARY and new_state == "speaking":
                self._cancel_room_summary(reason="user_speaking")
            if (
                INTERRUPT_GATE
                and new_state == "speaking"
                and getattr(self.session, "agent_state", "") == "speaking"
            ):
                self._barge_in_started_at = float(
                    getattr(ev, "created_at", time.time())
                )

        def _conversation_item_added_handler(ev):
            item = getattr(ev, "item", None)
            if (
                item is None
                or getattr(item, "role", None) != "assistant"
                or not bool(getattr(item, "interrupted", False))
            ):
                return
            spoken = str(getattr(item, "text_content", "") or "").strip()
            generated = self._current_tts_text.strip()
            remaining = ""
            if spoken and generated.startswith(spoken):
                remaining = generated[len(spoken):].lstrip()
            self._last_interrupted_spoken_text = spoken
            self._last_interrupted_unspoken_text = remaining
            logger.info(
                "FLOOR_HOLD context_preserved speech_id=%s spoken_chars=%d "
                "unspoken_chars=%d framework_truncated=true",
                getattr(
                    self._current_speech_handle,
                    "id",
                    "unknown",
                ),
                len(spoken),
                len(remaining),
            )


        try:
            get_job_context().room.register_byte_stream_handler("images", _image_received_handler)
            get_job_context().room.register_byte_stream_handler(AGENT_COMMAND_TOPIC, _command_received_handler)
            self.session.on("user_input_transcribed", _user_input_transcribed_handler)
            self.session.on("speech_created", _speech_created_handler)
            self.session.on("agent_state_changed", _agent_state_changed_handler)
            self.session.on("user_state_changed", _user_state_changed_handler)
            self.session.on(
                "conversation_item_added",
                _conversation_item_added_handler,
            )
            self._transcription_handler = _user_input_transcribed_handler
            self._speech_created_handler = _speech_created_handler
            self._agent_state_handler = _agent_state_changed_handler
            self._user_state_handler = _user_state_changed_handler
            self._conversation_item_handler = _conversation_item_added_handler
            self._transcription_handler_registered = True
            self._handler_registered = True
            logger.info(
                "Registered byte stream handlers for topics 'images' and '%s'",
                AGENT_COMMAND_TOPIC,
            )
            if self._is_rag_enabled():
                self._track_task(
                    asyncio.create_task(
                        self._ensure_rag_client(),
                        name="rag-client-prewarm",
                    )
                )
        except Exception as exc:
            logger.error("Failed to register byte stream handler: %s", exc)

        # Must be awaited directly here (not via asyncio.create_task): AgentTask
        # subclasses like FaceEnrollmentTask can only be awaited inside a
        # tool_function or an Agent's on_enter/on_exit call stack. This also
        # means the initial greeting must be decided and spoken here, in
        # sequence, rather than back in entrypoint() after session.start()
        # returns -- session.start() does not block on on_enter() finishing,
        # so entrypoint() would otherwise race this identity check and speak
        # the generic greeting before (or on top of) the enrollment flow.
        greeting_decision = await self._resolve_initial_greeting()
        await self._emit_initial_greeting(greeting_decision)
        if self._identity_monitor_task is None or self._identity_monitor_task.done():
            self._identity_monitor_task = self._track_task(
                asyncio.create_task(
                    self._monitor_face_identity(),
                    name="face-identity-monitor",
                )
            )

    async def on_exit(self) -> None:
        if self._identity_monitor_task is not None and not self._identity_monitor_task.done():
            self._identity_monitor_task.cancel()
        self._identity_monitor_task = None
        if self._transcription_handler_registered and self._transcription_handler is not None:
            try:
                self.session.off("user_input_transcribed", self._transcription_handler)
            except Exception as exc:
                logger.debug("Failed to unregister user transcript handler: %s", exc)
        self._transcription_handler_registered = False
        self._transcription_handler = None
        for event_name, handler in (
            ("speech_created", self._speech_created_handler),
            ("agent_state_changed", self._agent_state_handler),
            ("user_state_changed", self._user_state_handler),
            ("conversation_item_added", self._conversation_item_handler),
        ):
            if handler is not None:
                try:
                    self.session.off(event_name, handler)
                except Exception as exc:
                    logger.debug("Failed to unregister %s handler: %s", event_name, exc)
        self._speech_created_handler = None
        self._agent_state_handler = None
        self._user_state_handler = None
        self._conversation_item_handler = None
        if self._rag_client is not None:
            try:
                await self._rag_client.aclose()
            except Exception as exc:
                logger.debug("Failed to close RAG HTTP client: %s", exc)
            self._rag_client = None
        if self._rag_prefetch_task is not None and not self._rag_prefetch_task.done():
            self._rag_prefetch_task.cancel()
        self._cancel_room_summary(reason="agent_exit")
    
    async def _image_received(self, reader, participant_identity):
        """Receive and cache image from byte stream."""
        try:
            image_bytes = bytearray()
            async for chunk in reader:
                image_bytes.extend(chunk)
            
            loop = asyncio.get_running_loop()
            data_uri = await loop.run_in_executor(
                None,
                lambda: f"data:image/jpeg;base64,{base64.b64encode(bytes(image_bytes)).decode('utf-8')}"
            )
            
            image_content = ImageContent(image=data_uri)
            async with self._image_lock:
                self._latest_image = image_content
            logger.info("Cached latest image from %s (%d bytes)", participant_identity, len(image_bytes))
        except Exception as exc:
            logger.warning("Failed to process image from %s: %s", participant_identity, exc)

    async def _command_received(self, reader, participant_identity) -> None:
        try:
            payload_bytes = bytearray()
            async for chunk in reader:
                payload_bytes.extend(chunk)

            steps = self._parse_command_payload(bytes(payload_bytes))
            if not steps:
                logger.warning("Ignoring empty command payload from %s", participant_identity)
                return

            if self._is_stop_command(steps):
                logger.info("Stop command received from %s, cancelling script", participant_identity)
                self._stop_requested.set()
                return

            if self._is_wrap_up_command(steps):
                logger.info("Wrap-up command received from %s", participant_identity)
                self._stop_requested.set()
                async with self._command_lock:
                    await self._handle_wrap_up_command()
                return

            logger.info(
                "Received direct agent command from %s with %d step(s)",
                participant_identity,
                len(steps),
            )
            rag_toggle = self._parse_rag_toggle_command(steps)
            if rag_toggle is not None:
                self._rag_runtime_enabled = rag_toggle
                if not rag_toggle:
                    self._rag_client = None 
                logger.info(
                    "RAG runtime toggled by %s -> %s",
                    participant_identity,
                    "ON" if rag_toggle else "OFF",
                )
                return

            trigger_toggle = self._parse_trigger_word_toggle_command(steps)
            if trigger_toggle is not None:
                previous = self._trigger_word_runtime_enabled
                self._trigger_word_runtime_enabled = (
                    trigger_toggle if TRIGGER_WORD_FEATURE_ENABLED else False
                )
                self._trigger_word.deactivate()
                logger.info(
                    "TRIGGER_WORD mode_change requested=%s effective=%s previous=%s "
                    "feature_available=%s participant=%s engagement_reset=true",
                    trigger_toggle,
                    self._trigger_word_runtime_enabled,
                    previous,
                    TRIGGER_WORD_FEATURE_ENABLED,
                    participant_identity,
                )
                return

            quiz_toggle = self._parse_quiz_toggle_command(steps)
            if quiz_toggle is not None:
                self._quiz_runtime_enabled = quiz_toggle
                await self._refresh_runtime_instructions()
                logger.info(
                    "Quiz runtime toggled by %s -> %s",
                    participant_identity,
                    "ON" if quiz_toggle else "OFF",
                )
                return

            survey_toggle = self._parse_survey_toggle_command(steps)
            if survey_toggle is not None:
                self._survey_runtime_enabled = survey_toggle
                await self._refresh_runtime_instructions()
                logger.info(
                    "Survey runtime toggled by %s -> %s",
                    participant_identity,
                    "ON" if survey_toggle else "OFF",
                )
                return
            
            async with self._command_lock:
                await self._execute_command_steps(steps, participant_identity)

        except Exception as exc:
            logger.warning("Failed to process command from %s: %s", participant_identity, exc)

    @staticmethod
    def _parse_command_payload(payload: bytes) -> list[CommandStep]:
        raw = payload.decode("utf-8", "replace").strip()
        if not raw:
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [CommandStep(text=raw)]

        if isinstance(data, dict):
            steps: list[CommandStep] = []
            default_force_gesture = HumanoidAgent._parse_force_gesture(data.get("force_gesture"), False)

            base_step = HumanoidAgent._parse_command_step(
                {
                    "text": data.get("text", ""),
                    "gesture": data.get("gesture"),
                    "pause_after_ms": data.get("pause_after_ms"),
                    "force_gesture": data.get("force_gesture"),
                },
                default_force_gesture=default_force_gesture,
            )
            if base_step:
                steps.append(base_step)

            raw_steps = data.get("steps")
            if isinstance(raw_steps, list):
                for raw_step in raw_steps:
                    step = HumanoidAgent._parse_command_step(
                        raw_step,
                        default_force_gesture=default_force_gesture,
                    )
                    if step:
                        steps.append(step)

            return steps

        if isinstance(data, list):
            return [
                step
                for raw_step in data
                if (step := HumanoidAgent._parse_command_step(raw_step)) is not None
            ]

        return [CommandStep(text=raw)]

    @staticmethod
    def _parse_command_step(raw_step, *, default_force_gesture: bool = False) -> CommandStep | None:
        if isinstance(raw_step, str):
            text = raw_step.strip()
            return CommandStep(text=text, force_gesture=default_force_gesture) if text else None

        if not isinstance(raw_step, dict):
            return None

        text = str(raw_step.get("text", "")).strip()
        gesture = raw_step.get("gesture")
        gesture_name = str(gesture).strip() if gesture is not None else None
        pause_after_ms = HumanoidAgent._parse_pause_after_ms(raw_step.get("pause_after_ms"))
        force_gesture = HumanoidAgent._parse_force_gesture(
            raw_step.get("force_gesture"),
            default_force_gesture,
        )

        if not text and not gesture_name and not pause_after_ms:
            return None

        return CommandStep(
            text=text,
            gesture=gesture_name or None,
            pause_after_ms=pause_after_ms,
            force_gesture=force_gesture,
        )

    @staticmethod
    def _parse_pause_after_ms(value) -> int | None:
        if value is None:
            return None

        try:
            pause_after_ms = int(value)
        except (TypeError, ValueError):
            return None

        return pause_after_ms if pause_after_ms > 0 else None

    @staticmethod
    def _parse_force_gesture(value, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    @staticmethod
    def _is_stop_command(steps: list[CommandStep]) -> bool:
        # Stop command is triggered by a single-step payload with special text  
        return (
            len(steps) == 1
            and steps[0].text.strip().upper() == "__STOP__"
            and not steps[0].gesture
        )

    @staticmethod
    def _is_wrap_up_command(steps: list[CommandStep]) -> bool:
        return (
            len(steps) == 1
            and steps[0].text.strip().upper() == WRAP_UP_COMMAND
            and not steps[0].gesture
        )

    async def _handle_wrap_up_command(self) -> None:
        self._stop_requested.clear()
        goodbye_text = get_goodbye_text_override() or ""
        try:
            goodbye_text = goodbye_text or build_goodbye_text().strip()
        except Exception as exc:
            logger.warning("Failed to build prompt-backed goodbye text: %s", exc)
        if not goodbye_text:
            goodbye_text = GOODBYE_TEXT.strip()
        if goodbye_text:
            await self._say_with_retry(self.session, goodbye_text)

        try:
            self.session.shutdown(drain=True)
            logger.info("Agent session shutdown requested after wrap-up speech")
        except TypeError:
            self.session.shutdown()
            logger.info("Agent session shutdown requested after wrap-up speech")
        except Exception as exc:
            logger.warning("Failed to shutdown agent session after wrap-up speech: %s", exc)
    
    def _is_rag_enabled(self) -> bool:
        if self._rag_runtime_enabled is None:
            return self._rag_config.enabled
        return self._rag_runtime_enabled
    
    @staticmethod
    def _parse_toggle_command(
        steps: list[CommandStep],
        *,
        on_token: str,
        off_token: str,
        reject_if_gesture: bool = False,
    ) -> bool | None:
        if len(steps) != 1 or (reject_if_gesture and steps[0].gesture):
            return None
        cmd = steps[0].text.strip().upper()
        if cmd == on_token:
            return True
        if cmd == off_token:
            return False
        return None

    @staticmethod
    def _parse_rag_toggle_command(steps: list[CommandStep]) -> bool | None:
        return HumanoidAgent._parse_toggle_command(
            steps, on_token="__RAG_ON__", off_token="__RAG_OFF__", reject_if_gesture=True
        )

    @staticmethod
    def _parse_trigger_word_toggle_command(
        steps: list[CommandStep],
    ) -> bool | None:
        return HumanoidAgent._parse_toggle_command(
            steps, on_token="__TRIGGER_WORD_ON__", off_token="__TRIGGER_WORD_OFF__"
        )

    @staticmethod
    def _parse_quiz_toggle_command(steps: list[CommandStep]) -> bool | None:
        return HumanoidAgent._parse_toggle_command(
            steps, on_token="__QUIZ_ON__", off_token="__QUIZ_OFF__"
        )

    def _is_quiz_enabled(self) -> bool:
        if self._quiz_runtime_enabled is None:
            return self._quiz_default_enabled
        return self._quiz_runtime_enabled

    def _is_survey_enabled(self) -> bool:
        if self._survey_runtime_enabled is None:
            return self._survey_default_enabled
        return self._survey_runtime_enabled

    async def _refresh_runtime_instructions(self) -> None:
        self._reply_shape_active = _reply_shape_is_active()
        try:
            await self.update_instructions(self._render_instructions())
        except Exception as exc:
            logger.warning("Failed to refresh runtime instructions: %s", exc)

    async def _refresh_gesture_runtime(self, *, force: bool = False) -> None:
        was_reachable = self._gesture_processor.reachable
        was_gestures = self._gesture_processor.active_gestures
        was_contextual_motions = self._gesture_processor.active_contextual_motions
        reachable = await self._gesture_processor.refresh_availability(force=force)
        if (
            reachable != was_reachable
            or self._gesture_processor.active_gestures != was_gestures
            or self._gesture_processor.active_contextual_motions
            != was_contextual_motions
        ):
            self._base_instructions = _build_base_instructions(self._gesture_processor)
            await self._refresh_runtime_instructions()

    @staticmethod
    def _parse_survey_toggle_command(steps: list[CommandStep]) -> bool | None:
        return HumanoidAgent._parse_toggle_command(
            steps, on_token="__SURVEY_ON__", off_token="__SURVEY_OFF__"
        )

    @staticmethod
    def _is_product_draw_hook(gesture: str | None) -> bool:
        if gesture is None:
            return False
        normalized = str(gesture).strip().lower().replace("-", "_").replace(" ", "_")
        return normalized == PRODUCT_DRAW_HOOK

    async def _say_product_hunt_draw(self, prefix: str) -> None:
        selected = random.sample(PRODUCT_HUNT_OPTIONS, 2)
        logger.info("Product Hunt items selected: %s & %s", selected[0], selected[1])

        speech_prefix = (prefix or PRODUCT_HUNT_DEFAULT_PREFIX).strip()
        text = f"{speech_prefix.rstrip(' :')}: {selected[0]} in {selected[1]}."
        await self._say_with_retry(self.session, text)

    async def _execute_command_steps(
        self,
        steps: list[CommandStep],
        participant_identity,
    ) -> None:
        for index, step in enumerate(steps, start=1):

            if self._stop_requested.is_set():
                self._stop_requested.clear()
                logger.info("Script execution cancelled by stop command")
                break

            if self._is_product_draw_hook(step.gesture):
                logger.info(
                    "Processing product draw hook step %d/%d from %s: text=%r pause_after_ms=%r",
                    index,
                    len(steps),
                    participant_identity,
                    step.text,
                    step.pause_after_ms,
                )
                await self._say_product_hunt_draw(step.text)

                if step.pause_after_ms:
                    await asyncio.sleep(step.pause_after_ms / 1000.0)
                continue

            normalized_gesture = normalize_gesture(step.gesture, ACTIVE_GESTURE_CATALOG_ID)
            logger.info(
                "Processing direct command step %d/%d from %s: text=%r gesture=%r pause_after_ms=%r force_gesture=%r",
                index,
                len(steps),
                participant_identity,
                step.text,
                normalized_gesture or step.gesture,
                step.pause_after_ms,
                step.force_gesture,
            )

            if step.text:
                await self._say_with_retry(self.session, step.text)

            if normalized_gesture:
                if (
                    GESTURE_SAFETY_V2
                    and normalized_gesture not in VERIFIED_CONVERSATION_GESTURES
                ):
                    logger.warning(
                        "Blocked unverified direct-command gesture from %s: %s",
                        participant_identity,
                        normalized_gesture,
                    )
                else:
                    self._schedule_gesture_api_call(
                        normalized_gesture,
                        requested=True,
                        force_gesture=step.force_gesture,
                    )
            elif step.gesture:
                logger.warning(
                    "Ignoring unknown direct-command gesture from %s: %r",
                    participant_identity,
                    step.gesture,
                )

            if step.pause_after_ms:
                await asyncio.sleep(step.pause_after_ms / 1000.0)

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message) -> None:
        """Protect the main answer deployment before any turn-completion work."""
        self._directed_turn_pending = True
        self._cancel_room_summary(reason="directed_turn_onset")
        try:
            await self._on_user_turn_completed_impl(turn_ctx, new_message)
        except StopResponse:
            self._directed_turn_pending = False
            self._maybe_schedule_room_summary()
            raise
        except Exception:
            self._directed_turn_pending = False
            self._maybe_schedule_room_summary()
            raise

    async def _on_user_turn_completed_impl(
        self,
        turn_ctx: ChatContext,
        new_message,
    ) -> None:
        """Handle user turn completion: attach cached images and detect gesture requests."""
        self._schedule_gesture_idle_signal("activity")
        await self._refresh_gesture_runtime()
        self._rag_speaking_motion_pending = False
        self._rag_speaking_motion_resource_name = ""
        self._contextual_motion_blocked = False
        self._contextual_motion_fallback_family = ""

        query = (getattr(new_message, "text_content", "") or "").strip()
        original_query = query
        vision_query = _is_vision_query(query)

        cached_image: Optional[ImageContent] = None
        async with self._image_lock:
            if self._latest_image:
                cached_image = self._latest_image
                self._latest_image = None

        if (
            cached_image is None
            and vision_query
            and self._max_context_images > 0
        ):
            wait_started = time.monotonic()
            while time.monotonic() - wait_started < 1.2:
                await asyncio.sleep(0.05)
                async with self._image_lock:
                    if self._latest_image:
                        cached_image = self._latest_image
                        self._latest_image = None
                        break
            logger.info(
                "VISION_QUERY snapshot_wait_ms=%.1f image_ready=%s query=%r",
                (time.monotonic() - wait_started) * 1000.0,
                cached_image is not None,
                query,
            )

        if cached_image:
            if self._max_context_images <= 0:
                logger.debug("Dropping cached image because attachments are disabled.")
            else:
                new_message.content.append(cached_image)
                self._remember_image_attachment(new_message, cached_image)
                logger.info(
                    "VISION_QUERY attached image to user turn query=%r",
                    query,
                )
        if vision_query:
            turn_ctx.add_message(
                role="developer",
                content=runtime_prompt(
                    "vision_query.image_available"
                    if cached_image
                    else "vision_query.image_unavailable"
                ),
            )

        normalized_query = _normalize_user_intent_text(query)
        face_enrollment_requested = is_face_enrollment_trigger(query)
        if face_enrollment_requested:
            self._face_enrollment_authorized_until = (
                time.monotonic() + FACE_ENROLLMENT_AUTH_TTL_S
            )
            turn_ctx.add_message(
                role="developer",
                content=runtime_prompt("face_routing.explicit_enrollment"),
            )
            logger.info(
                "FACE_ENROLLMENT trigger=accepted query=%r auth_ttl_s=%.1f",
                query,
                FACE_ENROLLMENT_AUTH_TTL_S,
            )
        floor_hold_decision = self._floor_hold_final_decision
        floor_hold_match = (
            INTERRUPT_GATE
            and floor_hold_decision is not None
            and normalized_query == self._floor_hold_final_text
            and time.monotonic() - self._floor_hold_final_ts
            <= FLOOR_HOLD_DECISION_TTL_S
        )
        if floor_hold_match:
            self._floor_hold_final_text = ""
            self._floor_hold_final_ts = 0.0
            self._floor_hold_final_decision = None
            if not floor_hold_decision.interrupt:
                logger.info(
                    "FLOOR_HOLD final_turn_suppressed reason=%s "
                    "semantic_intent=%s addressee=%s query=%r",
                    floor_hold_decision.reason,
                    floor_hold_decision.semantic_intent,
                    floor_hold_decision.addressivity,
                    query,
                )
                raise StopResponse()
        trigger_authorized = False
        trigger_acceptance_reason = "mode_off"
        affirmative_followup = (
            _is_affirmative_followup(query)
            and self._last_agent_speech_ended_ts > 0.0
            and time.monotonic() - self._last_agent_speech_ended_ts
            <= TRIGGER_WORD_REPAIR_WINDOW_S
        )
        if STT_SEGMENT_FIX:
            filtered_query, removed_phrases = strip_system_audio(query)
            if removed_phrases:
                logger.info(
                    "SYSTEM_AUDIO_FILTER stage=final_turn action=%s phrases=%s "
                    "original=%r remaining=%r",
                    "dropped" if not filtered_query else "stripped",
                    ",".join(removed_phrases),
                    query,
                    filtered_query,
                )
                query = filtered_query
                if (
                    query
                    and TRIGGER_WORD_FEATURE_ENABLED
                    and self._trigger_word_runtime_enabled
                ):
                    system_trigger = self._trigger_word.strip_trigger(query)
                    if (
                        system_trigger.found
                        and _normalize_user_intent_text(system_trigger.text)
                        in {"", "hej", "ej"}
                    ):
                        logger.info(
                            "TRIGGER_WORD hard_invariant=system_audio "
                            "action=drop trigger_found=true original=%r",
                            getattr(new_message, "text_content", ""),
                        )
                        query = ""
                try:
                    new_message.content = [query]
                except Exception:
                    pass
                if not query:
                    logger.info(
                        "Turn gate: decision=stop_response reason=system_audio query=%r",
                        getattr(new_message, "text_content", ""),
                    )
                    raise StopResponse()
            if looks_like_self_echo(query, self._current_tts_text):
                logger.info(
                    "TRIGGER_WORD hard_invariant=self_echo action=drop query=%r",
                    query,
                )
                raise StopResponse()

        if face_enrollment_requested:
            trigger_authorized = True
            trigger_acceptance_reason = "face_enrollment"
            self._llm_role = "command"
        elif TRIGGER_WORD_FEATURE_ENABLED and self._trigger_word_runtime_enabled:
            if _contains_stop_command(query):
                self._trigger_word.deactivate()
                self._conversation_context.clear()
                logger.info(
                    "TRIGGER_WORD explicit_deactivation reason=stop_command query=%r",
                    query,
                )
            else:
                trigger_match = self._trigger_word.strip_trigger(query)
                strict_trigger_qualified = (
                    ENGAGE_WINDOW_20S
                    and trigger_match.found
                    and strict_name_wake(
                        query,
                        tuple(_robot_address_terms()),
                    )
                )
                candidate = trigger_match.text if trigger_match.found else query
                if (
                    ENGAGE_WINDOW_20S
                    and trigger_match.found
                    and not strict_trigger_qualified
                ):
                    candidate = query
                if (
                    candidate
                    and _is_backchannel(candidate)
                    and not affirmative_followup
                ):
                    logger.info(
                        "TRIGGER_WORD hard_invariant=backchannel action=drop "
                        "trigger_found=%s query=%r",
                        trigger_match.found,
                        query,
                    )
                    query = candidate
                    try:
                        new_message.content = [query]
                    except Exception:
                        pass
                else:
                    was_engaged = self._trigger_word.is_engaged()
                    repair_followup = (
                        not trigger_match.found
                        and (ENGAGE_WINDOW_20S or not was_engaged)
                        and (
                            looks_like_repair_followup(candidate)
                            or affirmative_followup
                        )
                        and self._last_agent_speech_ended_ts > 0.0
                        and time.monotonic() - self._last_agent_speech_ended_ts
                        <= TRIGGER_WORD_REPAIR_WINDOW_S
                    )
                    if ENGAGE_WINDOW_20S:
                        trigger_authorized = bool(
                            strict_trigger_qualified or repair_followup
                        )
                        if trigger_authorized:
                            self._trigger_word.authorize(trigger_found=True)
                            self._turn_gate_v2.engage()
                    else:
                        trigger_authorized = self._trigger_word.authorize(
                            trigger_found=trigger_match.found or repair_followup
                        )
                    if not trigger_authorized:
                        if ENGAGE_WINDOW_20S:
                            query = candidate
                            try:
                                new_message.content = [query]
                            except Exception:
                                pass
                            logger.info(
                                "TRIGGER_WORD transcript_deferred_to_gate_v2 "
                                "mode=engage_window query=%r",
                                query,
                            )
                        else:
                            logger.info(
                                "TRIGGER_WORD transcript_ignored reason=%s "
                                "mode=on query=%r",
                                "engagement_timeout"
                                if self._trigger_word.has_timed_out()
                                else "no_activation",
                                query,
                            )
                            raise StopResponse()
                    if trigger_authorized:
                        logger.info(
                            "TRIGGER_WORD engage_window_opened=%s",
                            ENGAGE_WINDOW_20S,
                        )
                        query = candidate
                        trigger_acceptance_reason = (
                            "trigger"
                            if trigger_match.found
                            else "affirmative"
                            if affirmative_followup
                            else "repair"
                            if repair_followup
                            else "engagement"
                        )
                        try:
                            new_message.content = [query]
                        except Exception:
                            pass
                        logger.info(
                            "TRIGGER_WORD transcript_accepted reason=%s variant=%r "
                            "engagement_window_s=%.1f forwarded_query=%r",
                            "trigger_found"
                            if trigger_match.found
                            else "affirmative_followup"
                            if affirmative_followup
                            else "repair_followup"
                            if repair_followup
                            else "engagement_active",
                            trigger_match.variant,
                            ENGAGE_WINDOW_S
                            if ENGAGE_WINDOW_20S
                            else TRIGGER_WORD_ENGAGEMENT_TIMEOUT_S,
                            query,
                        )
                        activation_only = _normalize_user_intent_text(query) in {
                            "",
                            "hej",
                            "ej",
                            "zdravo",
                            "cao",
                        }
                        if activation_only and not TRIGGER_WORD_ACK_ENABLED:
                            logger.info(
                                "TRIGGER_WORD activation_only acknowledgment=disabled "
                                "previously_engaged=%s",
                                was_engaged,
                            )
                            raise StopResponse()
        elif TRIGGER_WORD_FEATURE_ENABLED:
            logger.info(
                "TRIGGER_WORD transcript_forwarded mode=off query=%r",
                query,
            )

        now_for_multiquery = time.monotonic()
        if (
            self._pending_multiquery_prefix
            and now_for_multiquery - self._pending_multiquery_ts <= 8.0
        ):
            pending_prefix = self._pending_multiquery_prefix
            if _can_complete_pending_multiquery(query):
                query = f"{pending_prefix.rstrip()} {query.lstrip()}".strip()
                try:
                    new_message.content = [query]
                except Exception:
                    pass
                logger.info(
                    "MULTIQUERY_CONTINUATION merged prefix=%r continuation=%r "
                    "combined=%r",
                    pending_prefix,
                    getattr(new_message, "text_content", ""),
                    query,
                )
            else:
                logger.info(
                    "MULTIQUERY_CONTINUATION cleared reason=non_completion "
                    "prefix=%r continuation=%r",
                    pending_prefix,
                    query,
                )
            self._pending_multiquery_prefix = ""
            self._pending_multiquery_ts = 0.0
        elif self._pending_multiquery_prefix:
            logger.info(
                "MULTIQUERY_CONTINUATION cleared reason=timeout prefix=%r",
                self._pending_multiquery_prefix,
            )
            self._pending_multiquery_prefix = ""
            self._pending_multiquery_ts = 0.0

        if _is_dangling_multiquery(query):
            self._pending_multiquery_prefix = query
            self._pending_multiquery_ts = time.monotonic()
            try:
                new_message.content = [query]
            except Exception:
                pass
            logger.info(
                "MULTIQUERY_CONTINUATION pending prefix=%r",
                query,
            )
            raise StopResponse()

        if (
            self._pending_unknown_face
            and self._current_known_person is None
            and not face_enrollment_requested
        ):
            introduced_name = _extract_session_introduction_name(query)
            if introduced_name:
                await self._apply_person_identity(
                    {
                        "status": "known",
                        "name": introduced_name,
                        "display_name": introduced_name,
                        "canonical_name": introduced_name,
                        "source": "voice_introduction",
                    }
                )
                greeting_name = first_name_vocative(introduced_name)
                logger.info(
                    "Face identity bound from visitor introduction: name=%s query=%r",
                    introduced_name,
                    query,
                )
                try:
                    active_prompt = get_prompt_builder().get_active()
                except Exception:
                    active_prompt = {}
                if active_prompt.get("persona") != "comtrade_kids_lumi":
                    self._face_enrollment_authorized_until = (
                        time.monotonic() + FACE_ENROLLMENT_AUTH_TTL_S
                    )
                    # Giving a name is not the same as consenting to face
                    # enrollment - make remember_face ask the real consent
                    # question instead of jumping straight into capture.
                    self._face_enrollment_needs_explicit_consent = True
                    face_enrollment_requested = True
                    turn_ctx.add_message(
                        role="developer",
                        content=runtime_prompt("face_routing.introduced_visitor"),
                    )
                    logger.info(
                        "Face enrollment authorized after visitor introduction: name=%s",
                        introduced_name,
                    )
                else:
                    await self._say_with_retry(
                        self.session,
                        runtime_prompt(
                            "face_routing.introduced_kids_reply",
                            greeting_name=greeting_name,
                        ),
                    )
                    raise StopResponse()

        await self._apply_spoken_known_identity(query)
        if self._current_known_person is None and is_self_identity_question(query):
            self._remove_previous_rag_message(turn_ctx)
            language = (
                _forced_reply_language_from_stt_mode(VOICE_SETTINGS.misc.stt_language_mode)
                or _detect_query_language(query)
            )
            answer_text = runtime_prompt(
                "face_routing.unknown_identity_en"
                if language == "en"
                else "face_routing.unknown_identity_sr"
            )
            logger.info("IDENTITY_QUERY unknown_person fallback query=%r", query)
            await self._say_with_retry(self.session, answer_text)
            raise StopResponse()
        self._remember_conversation_facts(turn_ctx, new_message, query)
        turn_id = str(getattr(new_message, "id", "") or f"turn_{time.time_ns()}")
        turn_timestamp = time.time()
        intent = _classify_user_intent(query)
        known_person_rag_intent = False
        if self._current_known_person is not None:
            known_person_rag_intent = (
                is_self_identity_question(query)
                or await self._known_person_rag_intent_tiebreak(
                    query,
                    heuristic_intent=intent,
                )
            )
        if known_person_rag_intent:
            intent = "knowledge"
            self._known_person_rag_turn_ids.add(turn_id)
        self._llm_role = (
            "command"
            if face_enrollment_requested
            else "question_social"
            if intent == "chitchat"
            else "question_factual"
        )
        stt_reported_language = self._last_stt_language
        self._last_stt_language = ""
        heuristic_language = _detect_query_language(query)
        self._last_query_language, language_source = _resolve_query_language(
            query,
            stt_language=stt_reported_language,
            previous_language=self._last_query_language,
            stt_language_mode=VOICE_SETTINGS.misc.stt_language_mode,
        )
        logger.info(
            "QUERY_LANGUAGE resolved=%s source=%s stt_reported=%s heuristic=%s query=%r",
            self._last_query_language,
            language_source,
            stt_reported_language or "none",
            heuristic_language,
            query,
        )
        now_monotonic = time.monotonic()
        engagement_active = (
            LOOSE_ENGAGEMENT_GATE
            and self._last_engagement_activity_ts > 0.0
            and now_monotonic - self._last_engagement_activity_ts
            < ENGAGEMENT_WINDOW_S
        )
        gate_state = "ACTIVE" if engagement_active else "COLD"
        repair_followup_context = (
            looks_like_repair_followup(query)
            and self._last_agent_speech_ended_ts > 0.0
            and time.monotonic() - self._last_agent_speech_ended_ts
            <= TRIGGER_WORD_REPAIR_WINDOW_S
        )
        if ENGAGE_WINDOW_20S and repair_followup_context:
            self._turn_gate_v2.engage()
        gate_v2_decision = None
        if GATE_V2 and not trigger_authorized and not repair_followup_context:
            gate_intent = classify_gate_v2_intent(query)
            self._llm_role = gate_intent
            gate_sync_started = time.perf_counter()
            gate_v2_decision = self._turn_gate_v2.decide(query, intent=gate_intent)
            gate_sync_ms = (time.perf_counter() - gate_sync_started) * 1000.0
            gate_v2_respond = gate_v2_decision.respond
            gate_v2_reason = gate_v2_decision.reason
            if (
                ADDRESSEE_LLM_TIEBREAK
                and not gate_v2_respond
                and gate_v2_decision.addressivity == "ambiguous"
                and gate_intent in {"question_factual", "command"}
            ):
                gate_v2_respond = await self._addressee_llm_tiebreak(query)
                gate_v2_reason = (
                    "llm_tiebreak_directed"
                    if gate_v2_respond
                    else "llm_tiebreak_overheard"
                )
            logger.info(
                "Turn gate v2: decision=%s intent=%s addressivity=%s state=%s "
                "signals=%s wireless_mic=unavailable diarization=unavailable "
                "facing=unavailable reason=%s llm_tiebreak=%s "
                "gate_sync_ms=%.3f turn_id=%s timestamp=%.6f query=%r",
                "respond" if gate_v2_respond else "stop_response",
                gate_v2_decision.intent,
                gate_v2_decision.addressivity,
                gate_v2_decision.state.value,
                ",".join(gate_v2_decision.signals) or "none",
                gate_v2_reason,
                ADDRESSEE_LLM_TIEBREAK,
                gate_sync_ms,
                turn_id,
                turn_timestamp,
                query,
            )
            latency_tracer.emit(
                "turn_gate.decision",
                duration_ms=gate_sync_ms,
                turn_id=turn_id,
                critical_path=True,
                decision="respond" if gate_v2_respond else "stop_response",
                intent=gate_v2_decision.intent,
                addressivity=gate_v2_decision.addressivity,
                reason=gate_v2_reason,
            )
            if not gate_v2_respond:
                self._cancel_rag_prefetch(query)
        if intent == "control":
            self._conversation_context.clear()
            if ENGAGE_WINDOW_20S:
                self._turn_gate_v2.engage()
            await self._handle_control_turn(turn_ctx, new_message, query)
            self._suppress_next_reply = False
            logger.info(
                "TURN_ACCEPTANCE state=%s decision=accept reason=control_stop "
                "matched=stop_word stt_conf=unavailable query=%r",
                gate_state,
                query,
            )
            logger.info(
                "Turn gate: decision=stop_response reason=control "
                "turn_id=%s timestamp=%.6f query=%r",
                turn_id,
                turn_timestamp,
                query,
            )
            raise StopResponse()

        if (
            intent in {"empty", "incomplete"}
            or (_is_backchannel(query) and not affirmative_followup)
        ):
            self._remove_previous_rag_message(turn_ctx)
            rejection_reason = (
                "backchannel"
                if _is_backchannel(query) and not affirmative_followup
                else intent
            )
            logger.info(
                "TURN_ACCEPTANCE state=%s decision=reject reason=%s "
                "matched=none stt_conf=unavailable query=%r",
                gate_state,
                rejection_reason,
                query,
            )
            logger.info(
                "Turn gate: decision=stop_response reason=%s "
                "turn_id=%s timestamp=%.6f query=%r",
                rejection_reason,
                turn_id,
                turn_timestamp,
                query,
            )
            raise StopResponse()

        if LOOSE_ENGAGEMENT_GATE:
            directed, acceptance_reason = _loose_engagement_decision(
                query,
                intent,
                active=engagement_active,
            )
        else:
            directed = _looks_directed_at_robot(query, intent)
            acceptance_reason = "directed_pattern" if directed else "not_directed"
        if trigger_authorized or repair_followup_context or affirmative_followup:
            directed = True
            acceptance_reason = (
                "trigger"
                if trigger_authorized
                else "repair_followup"
                if repair_followup_context
                else "affirmative_followup"
            )
        if gate_v2_decision is not None:
            directed = True if trigger_authorized else gate_v2_respond
        contextual_followup = (
            _is_contextual_followup(query)
            and bool(self._last_directed_query)
            and time.monotonic() - self._last_directed_query_ts
            <= CONTEXTUAL_FOLLOWUP_TIMEOUT_S
        )
        if contextual_followup:
            directed = True
            acceptance_reason = "contextual_followup"

        if not directed:
            self._consecutive_skipped_turns += 1
            if self._consecutive_skipped_turns < ADDRESSEE_MAX_SKIPPED_TURNS:
                self._remove_previous_rag_message(turn_ctx)
                logger.info(
                    "TURN_ACCEPTANCE state=%s decision=reject reason=not_directed "
                    "matched=none stt_conf=unavailable query=%r",
                    gate_state,
                    query,
                )
                logger.info(
                    "Turn gate: decision=stop_response reason=not_directed intent=%s "
                    "skipped_turns=%d/%d turn_id=%s timestamp=%.6f query=%r",
                    intent,
                    self._consecutive_skipped_turns,
                    ADDRESSEE_MAX_SKIPPED_TURNS,
                    turn_id,
                    turn_timestamp,
                    query,
                )
                raise StopResponse()
            directed = True
            acceptance_reason = "max_skipped_turns"
            logger.info(
                "Turn gate: decision=respond reason=max_skipped_turns intent=%s "
                "skipped_turns=%d turn_id=%s timestamp=%.6f query=%r",
                intent,
                self._consecutive_skipped_turns,
                turn_id,
                turn_timestamp,
                query,
            )

        self._consecutive_skipped_turns = 0
        self._last_engagement_activity_ts = time.monotonic()
        logger.info(
            "TURN_ACCEPTANCE state=%s decision=accept reason=%s "
            "matched=%s stt_conf=unavailable query=%r",
            gate_state,
            acceptance_reason,
            _matched_turn_signal(query, intent),
            query,
        )

        turn_ctx.add_message(
            role="developer",
            content=_build_turn_language_directive(self._last_query_language),
            created_at=time.time() - 0.0003,
        )

        explicit_gesture = (
            self._gesture_intent_runtime.queue_explicit_command(query)
            if self._gesture_intent_runtime is not None
            else None
        )
        self._contextual_motion_blocked = explicit_gesture is not None
        if explicit_gesture is not None:
            queued = self._gesture_intent_runtime.pending is not None
            if (
                explicit_gesture.kind == "linkcraft"
                and explicit_gesture.use == "explicit_only"
                and queued
            ):
                confirmation = (
                    "Here is six-seven!"
                    if self._last_query_language == "en"
                    else "Evo sixseven!"
                )
                gesture_instruction = runtime_prompt(
                    "gesture_command.linkcraft_explicit",
                    gesture_label=explicit_gesture.label_sr,
                    confirmation=confirmation,
                )
            elif explicit_gesture.kind == "linkcraft":
                gesture_instruction = runtime_prompt(
                    "gesture_command.linkcraft_blocked",
                    gesture_label=explicit_gesture.label_sr,
                    duration_s=f"{explicit_gesture.duration_s:.1f}",
                )
            elif queued:
                gesture_instruction = runtime_prompt(
                    "gesture_command.preset_queued",
                    gesture_label=explicit_gesture.label_sr,
                )
            else:
                gesture_instruction = runtime_prompt(
                    "gesture_command.unavailable",
                    gesture_label=explicit_gesture.label_sr,
                )
            turn_ctx.add_message(
                role="developer",
                content=gesture_instruction,
                created_at=time.time() - 0.00025,
                extra={"gesture_command": explicit_gesture.id},
            )
            logger.info(
                "GESTURE_INTENT_MAP explicit id=%s kind=%s queued=%s query=%r",
                explicit_gesture.id,
                explicit_gesture.kind,
                queued,
                query,
            )

        qa_answer = None
        try:
            qa_answer = await match_qa_intent(query)
        except Exception:
            logger.warning("QA_INTENT lookup failed for query=%r", query, exc_info=True)

        if qa_answer is not None:
            answer_text = (
                qa_answer.text_en if self._last_query_language == "en" else qa_answer.text_sr
            )
            logger.info(
                "QA_INTENT kind=%s query=%r answer=%r",
                qa_answer.kind,
                query,
                answer_text,
            )
            await self._say_with_retry(self.session, answer_text)
            if qa_answer.display_primary:
                asyncio.create_task(
                    self._flash_qa_display(qa_answer.display_primary, qa_answer.display_secondary)
                )
            raise StopResponse()

        if (
            TRIGGER_WORD_FEATURE_ENABLED
            and self._trigger_word_runtime_enabled
            and trigger_authorized
        ):
            self._inject_bounded_conversation_context(
                turn_ctx=turn_ctx,
                new_message=new_message,
                current_query=query,
                current_transcript=original_query,
                acceptance_reason=trigger_acceptance_reason,
            )

        if not GATE_V2:
            logger.info(
                "Turn gate: decision=respond reason=%s intent=%s "
                "turn_id=%s timestamp=%.6f query=%r",
                "repair_followup"
                if repair_followup_context
                else "contextual_followup"
                if contextual_followup
                else "directed",
                intent,
                turn_id,
                turn_timestamp,
                query,
            )
        summary_overlap = (
            self._room_summary_task is not None
            and not self._room_summary_task.done()
        )
        logger.info(
            "room_summary answer_guard active=%s call_active=%s "
            "cancel_requests=%d",
            summary_overlap,
            self._room_summary_call_active,
            self._room_summary_cancel_requests,
        )
        if summary_overlap or self._room_summary_call_active:
            logger.error(
                "room_summary status=answer_overlap_blocker "
                "task_active=%s call_active=%s",
                summary_overlap,
                self._room_summary_call_active,
        )
        self._inject_room_summary(turn_ctx=turn_ctx, new_message=new_message)
        if vision_query:
            self._remove_previous_rag_message(turn_ctx)
            self._rag_speaking_motion_pending = False
            logger.info(
                "External RAG skipped for vision query=%r image_attached=%s",
                query,
                cached_image is not None,
            )
        elif explicit_gesture is None:
            await self._run_rag(turn_ctx, new_message)
        else:
            self._remove_previous_rag_message(turn_ctx)
            # An explicit gesture command already claimed this turn's single
            # motion. Skip retrieval entirely for lower latency and never fire
            # a speaking clip on top of the explicitly requested gesture.
            self._rag_speaking_motion_pending = False
            logger.info(
                "External RAG skipped for explicit gesture id=%s query=%r",
                explicit_gesture.id,
                query,
            )
        self._last_directed_query = query
        self._last_directed_query_ts = time.monotonic()
        self._last_engagement_activity_ts = self._last_directed_query_ts

    async def _fast_llm_classify(
        self,
        context: ChatContext,
        *,
        timeout_s: float,
        debug_label: str,
    ) -> tuple[str, str]:
        """Run one fast-LLM classification call.

        Returns (lowercased result text, status), where status is one of
        "ok", "timeout", or "error". Callers map "ok" onto their own
        decision/status vocabulary; "timeout"/"error" pass straight through.
        Assumes self._fast_llm is not None.
        """

        async def classify() -> str:
            async with self._fast_llm.chat(chat_ctx=context, tools=[]) as stream:
                async for chunk in stream:
                    text = "".join(
                        str(getattr(choice.delta, "content", "") or "")
                        for choice in getattr(chunk, "choices", [])
                    ).strip().lower()
                    if text:
                        return text
            return ""

        try:
            result = await asyncio.wait_for(classify(), timeout=timeout_s)
            return result, "ok"
        except asyncio.TimeoutError:
            return "", "timeout"
        except Exception as exc:
            logger.debug("%s failed: %s", debug_label, exc)
            return "", "error"

    async def _addressee_llm_tiebreak(self, query: str) -> bool:
        started = time.perf_counter()
        decision = "overheard"
        status = "ok"
        if self._fast_llm is None:
            status = "unavailable"
        else:
            context = ChatContext.empty()
            context.add_message(
                role="system",
                content=runtime_prompt("classifiers.addressee"),
            )
            context.add_message(role="user", content=query)
            result, status = await self._fast_llm_classify(
                context, timeout_s=0.150, debug_label="Addressee tiebreak"
            )
            if status == "ok":
                decision = "directed" if result.startswith("directed") else "overheard"
        latency_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "Addressee tiebreak: decision=%s latency_ms=%.2f status=%s query=%r",
            decision,
            latency_ms,
            status,
            query,
        )
        return decision == "directed"

    async def _known_person_rag_intent_tiebreak(
        self,
        query: str,
        *,
        heuristic_intent: str,
    ) -> bool:
        known_person = self._current_known_person
        if known_person is None:
            return False
        if heuristic_intent in {"empty", "control", "incomplete"} or _is_backchannel(query):
            return False

        started = time.perf_counter()
        decision = "rag" if KNOWN_PERSON_RAG_BIAS_DEFAULT else "skip"
        status = "fallback"
        if KNOWN_PERSON_RAG_INTENT_LLM and self._fast_llm is not None:
            context = ChatContext.empty()
            context.add_message(
                role="system",
                content=runtime_prompt("classifiers.known_person_rag"),
            )
            context.add_message(
                role="user",
                content=runtime_prompt(
                    "classifiers.known_person_input",
                    canonical_name=known_person.canonical_name,
                    heuristic_intent=heuristic_intent,
                    query=query,
                ),
            )
            result, call_status = await self._fast_llm_classify(
                context,
                timeout_s=KNOWN_PERSON_RAG_INTENT_TIMEOUT_S,
                debug_label="Known-person RAG intent tiebreak",
            )
            if call_status == "ok":
                decision = "skip" if result.startswith("skip") else "rag"
                status = "llm"
            else:
                status = call_status

        latency_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "Known-person RAG intent: decision=%s status=%s latency_ms=%.2f person=%s query=%r",
            decision,
            status,
            latency_ms,
            known_person.canonical_name,
            query,
        )
        return decision == "rag"

    def _observe_room_summary(self, transcript: str) -> None:
        """Queue eligible room speech without blocking the directed-turn path."""
        if not ROOM_SUMMARY:
            return

        compact = " ".join(transcript.split()).strip()
        if not compact:
            return
        self._room_summary_turns.append(compact)
        self._room_summary_eligible_since_refresh += 1
        if self._room_summary_eligible_since_refresh < ROOM_SUMMARY_EVERY_TURNS:
            return
        self._maybe_schedule_room_summary()

    def _room_summary_is_idle(self) -> bool:
        if self._directed_turn_pending or self._room_summary_call_active:
            return False
        session = getattr(self, "session", None)
        agent_state = str(getattr(session, "agent_state", "") or "")
        user_state = str(getattr(session, "user_state", "") or "")
        return (
            agent_state in {"", "idle", "listening"}
            and user_state not in {"speaking"}
            and not self._protected_speech_active
        )

    def _maybe_schedule_room_summary(self) -> None:
        if (
            not ROOM_SUMMARY
            or self._room_summary_llm is None
            or self._room_summary_eligible_since_refresh
            < ROOM_SUMMARY_EVERY_TURNS
        ):
            return
        if self._room_summary_task is not None and not self._room_summary_task.done():
            return

        self._room_summary_task = self._track_task(
            asyncio.create_task(
                self._run_room_summary_when_idle(),
                name="room-topic-summary",
            )
        )

    def _cancel_room_summary(self, *, reason: str) -> bool:
        task = self._room_summary_task
        if task is None or task.done():
            return False
        self._room_summary_cancel_requests += 1
        task.cancel()
        logger.info(
            "room_summary status=cancel_requested reason=%s "
            "call_active=%s cancel_requests=%d",
            reason,
            self._room_summary_call_active,
            self._room_summary_cancel_requests,
        )
        return True

    async def _run_room_summary_when_idle(self) -> None:
        entered_refresh = False
        try:
            if ROOM_SUMMARY_IDLE_DELAY_S:
                await asyncio.sleep(ROOM_SUMMARY_IDLE_DELAY_S)
            if not self._room_summary_is_idle():
                logger.info(
                    "room_summary status=deferred_busy eligible_since_refresh=%d",
                    self._room_summary_eligible_since_refresh,
                )
                return
            turns = tuple(self._room_summary_turns)
            eligible_at_start = self._room_summary_eligible_since_refresh
            entered_refresh = True
            await self._refresh_room_summary(turns)
            self._room_summary_eligible_since_refresh = max(
                0,
                self._room_summary_eligible_since_refresh - eligible_at_start,
            )
        except asyncio.CancelledError:
            logger.info(
                "room_summary status=cancelled phase=%s",
                "llm_call" if entered_refresh else "idle_wait",
            )
            raise

    async def _refresh_room_summary(self, turns: tuple[str, ...]) -> None:
        """Refresh the one-line topic cache on the main model, while idle."""
        if self._room_summary_llm is None or not self._room_summary_is_idle():
            return
        started = time.perf_counter()
        first_token_at: float | None = None
        self._room_summary_call_active = True
        try:
            context = ChatContext.empty()
            context.add_message(
                role="system",
                content=runtime_prompt(
                    "classifiers.room_summary",
                    max_chars=ROOM_SUMMARY_MAX_CHARS,
                ),
            )
            context.add_message(role="user", content="\n".join(turns))
            pieces: list[str] = []
            async with self._room_summary_llm.chat(
                chat_ctx=context,
                tools=[],
            ) as stream:
                async for chunk in stream:
                    text = "".join(
                        str(getattr(choice.delta, "content", "") or "")
                        for choice in getattr(chunk, "choices", [])
                    )
                    if text:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        pieces.append(text)

            summary = " ".join("".join(pieces).strip(" \t\r\n\"'").split())
            if len(summary) > ROOM_SUMMARY_MAX_CHARS:
                clipped = summary[: ROOM_SUMMARY_MAX_CHARS + 1]
                if " " in clipped:
                    clipped = clipped.rsplit(" ", 1)[0]
                summary = clipped.rstrip(" ,.;:-")
            if not summary:
                logger.info(
                    "room_summary status=empty eligible_turns=%d",
                    len(turns),
                )
                return
            self._room_summary = summary
            self._room_summary_refresh_count += 1
            summary_ttft_ms = (
                (first_token_at - started) * 1000.0
                if first_token_at is not None
                else 0.0
            )
            logger.info(
                "room_summary status=updated refresh=%d summary_ttft_ms=%.2f "
                "chars=%d eligible_turns=%d",
                self._room_summary_refresh_count,
                summary_ttft_ms,
                len(summary),
                len(turns),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "room_summary status=error error_type=%s",
                type(exc).__name__,
            )
        finally:
            self._room_summary_call_active = False

    def _inject_room_summary(self, *, turn_ctx: ChatContext, new_message) -> None:
        """Replace the dynamic tail topic hint; this method is synchronous."""
        if self._room_summary_message_id:
            try:
                turn_ctx.items = [
                    item
                    for item in turn_ctx.items
                    if getattr(item, "id", None) != self._room_summary_message_id
                ]
            except Exception:
                pass
            self._room_summary_message_id = None
        if not ROOM_SUMMARY or not self._room_summary:
            return

        created_at = getattr(new_message, "created_at", None)
        if not isinstance(created_at, (int, float)):
            created_at = time.time()
        message = turn_ctx.add_message(
            role="developer",
            content=[runtime_prompt("context_protocol.room_summary", summary=self._room_summary)],
            created_at=created_at - 0.0005,
            extra={"room_summary": True},
        )
        self._room_summary_message_id = getattr(message, "id", None)
        logger.info(
            "room_summary status=attached chars=%d refresh=%d",
            len(self._room_summary),
            self._room_summary_refresh_count,
        )

    def _start_rag_prefetch(self, query: str) -> None:
        query = query.strip()
        intent = _classify_user_intent(query)
        if self._current_known_person is not None and is_self_identity_question(query):
            intent = "knowledge"
        contextual_followup = (
            _is_contextual_followup(query)
            and self._fresh_rag_or_directed_topic()
        )
        if GATE_V2 and self._llm_role in {
            "command",
            "question_social",
            "chitchat",
        } and intent != "knowledge" and not contextual_followup:
            logger.info(
                "External RAG skipped for intent=%s query=%r",
                self._llm_role,
                query,
            )
            return
        if (
            not contextual_followup
            and not _should_attempt_rag(query, intent)
        ) or self._should_skip_rag(query):
            return
        search_queries = self._build_rag_search_queries(None, None, query)
        search_query = _rag_query_cache_key(search_queries)
        last_directed = self._fresh_rag_topic() or self._fresh_directed_query()
        language, _ = _resolve_query_language(
            query,
            stt_language=self._last_stt_language or None,
            previous_language=self._last_query_language,
            stt_language_mode=VOICE_SETTINGS.misc.stt_language_mode,
        )
        if self._rag_prefetch_task is not None and not self._rag_prefetch_task.done():
            if self._rag_prefetch_query == search_query:
                return
            self._rag_prefetch_task.cancel()
        self._rag_prefetch_query = search_query
        self._rag_prefetch_resolved_query = search_query
        self._rag_prefetch_task = self._track_task(
            asyncio.create_task(
                self._prefetch_rag_context(
                    search_queries,
                    original_query=query,
                    last_directed=last_directed,
                    language=language,
                ),
                name="rag-final-transcript-prefetch",
            )
        )
        logger.info("External RAG async prefetch started query=%r", search_query)

    def _fresh_directed_query(self) -> str:
        if (
            self._last_directed_query
            and time.monotonic() - self._last_directed_query_ts
            <= CONTEXTUAL_FOLLOWUP_TIMEOUT_S
        ):
            return self._last_directed_query
        return ""

    def _fresh_rag_topic(self) -> str:
        if (
            self._last_rag_topic
            and time.monotonic() - self._last_rag_topic_ts
            <= CONTEXTUAL_FOLLOWUP_TIMEOUT_S
        ):
            return self._last_rag_topic
        return ""

    def _fresh_rag_or_directed_topic(self) -> bool:
        return bool(self._fresh_rag_topic() or self._fresh_directed_query())

    def _commit_rag_topic(self, search_query: str, rag_context: str) -> None:
        if not search_query.strip() or not rag_context.strip():
            return
        canonical_match = re.search(
            r"(?:kanonski naziv|canonical (?:name|entity))\s+['\"]([^'\"]{3,120})['\"]",
            rag_context,
            flags=re.IGNORECASE | re.UNICODE,
        )
        if canonical_match is None:
            canonical_match = re.search(
                r"ENTITY MATCHES:\s*[^\n;]+?\s+->\s+([^.;\n]{3,120})",
                rag_context,
                flags=re.IGNORECASE | re.UNICODE,
            )
        self._last_rag_topic = (
            canonical_match.group(1).strip() if canonical_match else search_query.strip()
        )
        self._last_rag_topic_ts = time.monotonic()

    def _cancel_rag_prefetch(self, query: str) -> None:
        search_query = _rag_query_cache_key(
            self._build_rag_search_queries(None, None, query.strip())
        )
        if (
            self._rag_prefetch_task is not None
            and self._rag_prefetch_query == search_query
        ):
            if not self._rag_prefetch_task.done():
                self._rag_prefetch_task.cancel()
            self._rag_prefetch_task = None
            self._rag_prefetch_query = ""
            self._rag_prefetch_resolved_query = ""
            logger.info("External RAG async prefetch discarded query=%r", search_query)

    async def _rewrite_query_llm(
        self,
        query: str,
        last_directed: str,
        language: str,
    ) -> str | None:
        if self._fast_llm is None:
            return None

        context = ChatContext.empty()
        context.add_message(
            role="system",
            content=runtime_prompt("classifiers.rag_query_rewrite"),
        )
        context.add_message(
            role="user",
            content=runtime_prompt(
                "classifiers.rag_query_rewrite_input",
                language="English" if language == "en" else "Serbian",
                last_directed=last_directed or "(none)",
                query=query,
            ),
        )

        async def rewrite() -> str:
            chunks: list[str] = []
            async with self._fast_llm.chat(chat_ctx=context, tools=[]) as stream:
                async for chunk in stream:
                    chunks.extend(
                        str(getattr(choice.delta, "content", "") or "")
                        for choice in getattr(chunk, "choices", [])
                    )
            return "".join(chunks).strip()

        try:
            raw = await asyncio.wait_for(
                rewrite(),
                timeout=RAG_QUERY_REWRITE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.debug(
                "RAG query rewrite timed out after %.0fms",
                RAG_QUERY_REWRITE_TIMEOUT_S * 1000.0,
            )
            return None
        except Exception as exc:
            logger.debug("RAG query rewrite failed: %s", exc)
            return None

        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) != 1:
            return None
        rewritten = lines[0]
        if (
            not rewritten
            or len(rewritten) > 500
            or rewritten.startswith(("-", "*", "`"))
        ):
            return None
        rewritten_language = _query_language_evidence(rewritten)
        if (
            rewritten_language is not None
            and rewritten_language != language
        ):
            return None
        return rewritten

    async def _resolve_prefetch_rag_query(
        self,
        *,
        original_query: str,
        heuristic_query: str,
        last_directed: str,
        language: str,
    ) -> str:
        started = time.perf_counter()
        source = "heuristic"
        search_query = heuristic_query

        if RAG_QUERY_REWRITE and self._fast_llm is not None:
            rewrite_input = _strip_rag_leading_discourse(original_query)
            rewritten = await self._rewrite_query_llm(
                rewrite_input,
                last_directed,
                language,
            )
            if rewritten is None:
                source = "fallback"
            else:
                source = "llm"
                search_query = _expand_common_stt_confusions(
                    _strip_rag_leading_discourse(rewritten)
                )

        logger.info(
            "RAG query rewrite source=%s original=%r rewritten=%r latency_ms=%.2f",
            source,
            original_query,
            search_query,
            (time.perf_counter() - started) * 1000.0,
        )
        return search_query

    async def _prefetch_rag_context(
        self,
        search_queries: tuple[str, ...] | str,
        *,
        original_query: str | None = None,
        last_directed: str = "",
        language: str = "sr",
    ) -> str:
        if isinstance(search_queries, str):
            search_queries = (search_queries,)
        search_query = _rag_query_cache_key(search_queries)
        if original_query is not None:
            if len(search_queries) == 1:
                search_query = await self._resolve_prefetch_rag_query(
                    original_query=original_query,
                    heuristic_query=search_query,
                    last_directed=last_directed,
                    language=language,
                )
                search_queries = (search_query,)
        self._rag_prefetch_resolved_query = search_query

        cached = self._get_cached_rag_context(search_query)
        if cached is not None:
            self._commit_rag_topic(search_query, cached)
            return cached
        client = await self._ensure_rag_client()
        if client is None:
            return ""
        context = (
            await self._search_rag_queries(
                client,
                search_queries,
                top_k=self._rag_top_k_for_query(original_query or search_query, search_query),
            )
        ).strip()
        if context:
            self._put_cached_rag_context(search_query, context)
            self._commit_rag_topic(search_query, context)
        return context

    def _rag_top_k_for_query(self, query: str, search_query: str | None = None) -> int:
        top_k = max(1, int(self._rag_config.top_k))
        if self._current_known_person is not None and is_self_identity_question(query):
            return max(top_k, RAG_SELF_IDENTITY_TOP_K) if RAG_QUALITY_MODE else min(top_k, 3)
        if _is_person_lookup_query(query) or (
            search_query is not None and _is_person_lookup_query(search_query)
        ):
            return max(top_k, RAG_PERSON_TOP_K) if RAG_QUALITY_MODE else min(top_k, 3)
        return top_k

    async def _search_rag_queries(
        self,
        client: RAGServiceClient,
        search_queries: tuple[str, ...] | str,
        *,
        budget_s: float | None = None,
        top_k: int,
    ) -> str:
        if isinstance(search_queries, str):
            search_queries = (search_queries,)
        if len(search_queries) == 1:
            if budget_s is None:
                return await client.asearch_wrapper(search_queries[0], top_k=top_k)
            return await client.asearch_wrapper(
                search_queries[0], budget_s=budget_s, top_k=top_k
            )
        return await client.asearch_many_wrapper(
            search_queries,
            budget_s=budget_s,
            top_k=top_k,
            context_max_chars=RAG_MULTI_QUERY_CONTEXT_MAX_CHARS,
        )

    async def _hard_stop_from_transcript(self, transcript: str, *, is_final: bool) -> None:
        query = transcript.strip()
        if not query or not _contains_stop_command(query):
            return

        now = time.monotonic()
        normalized = _normalize_user_intent_text(query)
        if normalized == self._last_hard_stop_text and now - self._last_hard_stop_ts < 0.4:
            return
        self._last_hard_stop_ts = now
        self._last_hard_stop_text = normalized
        # Stop is an intentional addressed turn: interrupt immediately, then
        # keep the conversation warm so the next correction/question does not
        # need a wake word or repetition.
        self._last_engagement_activity_ts = now

        self._stop_requested.set()
        self._gesture_processor.cancel_explanations()
        if self._gesture_intent_runtime is not None:
            self._gesture_intent_runtime.cancel_pending()
        self._suppress_next_reply = True
        if ENGAGE_WINDOW_20S:
            self._turn_gate_v2.engage()

        try:
            await self.session.interrupt(force=True)
            logger.info(
                "Hard stop interrupt from %s transcript: %r",
                "final" if is_final else "partial",
                query,
            )
        except Exception as exc:
            logger.warning("Failed hard stop interrupt for transcript %r: %s", query, exc)

        await self._release_remote_speaker_playback(reason="hard-stop")

    async def _handle_interruption_transcript(
        self,
        transcript: str,
        *,
        is_final: bool,
        event_created_at: float,
        decision=None,
        barge_started_at: float = 0.0,
    ) -> None:
        now = time.monotonic()
        normalized = _normalize_user_intent_text(transcript)
        if (
            normalized == self._last_interrupt_gate_text
            and now - self._last_interrupt_gate_ts < 0.30
        ):
            return
        self._last_interrupt_gate_text = normalized
        self._last_interrupt_gate_ts = now

        if decision is None:
            decision = self._classify_floor_hold(
                transcript,
                event_created_at=event_created_at,
                speaker_id=None,
            )
        if (
            not decision.interrupt
            and ADDRESSEE_LLM_TIEBREAK
            and decision.addressivity == "ambiguous"
            and decision.intent in {"question_factual", "command"}
            and (is_final or len(normalized.split()) >= 3)
        ):
            directed = await self._addressee_llm_tiebreak(transcript)
            if directed:
                decision = replace(
                    decision,
                    interrupt=True,
                    path="slow",
                    addressivity="directed",
                    reason="llm_tiebreak_directed",
                )
                if is_final:
                    self._floor_hold_final_decision = decision

        action = "keep"
        floor_decision = (
            "cut"
            if decision.interrupt
            else "continue"
            if decision.semantic_intent == "backchannel"
            else "ignore"
        )
        cancel_ts = 0.0
        if decision.interrupt:
            action = "interrupt"
            self._gesture_processor.cancel_explanations()
            if self._gesture_intent_runtime is not None:
                self._gesture_intent_runtime.cancel_pending()
            cancel_ts = time.time()
            if ENGAGE_WINDOW_20S:
                self._turn_gate_v2.engage()
            if decision.path == "fast":
                await self._hard_stop_from_transcript(transcript, is_final=is_final)
            else:
                try:
                    await self.session.interrupt(force=True)
                    await self._release_remote_speaker_playback(
                        reason="interrupt-gate"
                    )
                except Exception as exc:
                    action = "interrupt_error"
                    floor_decision = "ignore"
                    logger.warning("Manual interruption failed: %s", exc)

        start_ts = barge_started_at or self._barge_in_started_at or event_created_at
        barge_id = f"barge_{int(start_ts * 1000)}"
        yield_lag_ms = (
            max(0.0, (cancel_ts - start_ts) * 1000.0)
            if cancel_ts and start_ts
            else 0.0
        )
        self._floor_hold_metrics["events"] += 1
        self._floor_hold_metrics[floor_decision] += 1
        if decision.reason == "self_echo":
            self._floor_hold_metrics["self_echo_suppressed"] += 1
        logger.info(
            "FLOOR_HOLD acoustic_pass=%s addressee=%s "
            "semantic_intent=%s decision=%s reason=%s "
            "speaker_match=%s speech_duration_ms=%.1f is_final=%s "
            "events_total=%d cut_total=%d continue_total=%d "
            "ignore_total=%d self_echo_suppressed_total=%d transcript=%r",
            decision.acoustic_pass,
            decision.addressivity,
            decision.semantic_intent,
            floor_decision,
            decision.reason,
            decision.speaker_match,
            max(0.0, (event_created_at - start_ts) * 1000.0),
            is_final,
            self._floor_hold_metrics["events"],
            self._floor_hold_metrics["cut"],
            self._floor_hold_metrics["continue"],
            self._floor_hold_metrics["ignore"],
            self._floor_hold_metrics["self_echo_suppressed"],
            transcript,
        )
        logger.info(
            "INTERRUPT_GATE decision path=%s action=%s intent=%s "
            "addressivity=%s reason=%s is_final=%s event_ts=%.6f "
            "barge_id=%s barge_start_ts=%.6f cancel_ts=%.6f "
            "yield_lag_ms=%.2f transcript=%r",
            decision.path,
            action,
            decision.intent,
            decision.addressivity,
            decision.reason,
            is_final,
            event_created_at,
            barge_id,
            start_ts,
            cancel_ts,
            yield_lag_ms,
            transcript,
        )

    async def _release_remote_speaker_playback(self, *, reason: str) -> None:
        def _post_release() -> None:
            request = urllib.request.Request(
                "http://127.0.0.1:8765/remote-playback/release",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=0.35) as response:
                response.read()

        try:
            await asyncio.to_thread(_post_release)
            logger.info("Released remote speaker playback after %s", reason)
        except Exception as exc:
            logger.debug("Failed to release remote speaker playback after %s: %s", reason, exc)

    def _schedule_rag_speaking_motion(
        self,
        estimated_duration_s: float | None = None,
        *,
        reason: str,
    ) -> None:
        if not RAG_SPEAKING_MOTION_ENABLED:
            return
        resource_name = self._rag_speaking_motion_resource_name
        self._rag_speaking_motion_resource_name = ""
        self._rag_speaking_motion_pending = False
        self._rag_speaking_motion_dispatched = True
        self._track_task(
            asyncio.create_task(
                self._call_rag_speaking_motion(
                    estimated_duration_s,
                    resource_name=resource_name,
                ),
                name=f"rag-speaking-motion:{reason}",
            )
        )

    def _remember_image_attachment(self, message, image_content: ImageContent) -> None:
        self._image_history.append((message, image_content))
        self._trim_image_history()

    def _trim_image_history(self) -> None:
        while len(self._image_history) > self._max_context_images:
            old_message, old_image = self._image_history.popleft()
            if self._remove_image_from_message(old_message, old_image):
                logger.debug(
                    "Removed oldest cached image to keep <= %d attachments in context.",
                    self._max_context_images,
                )

    @staticmethod
    def _remove_image_from_message(message, image_content: ImageContent) -> bool:
        try:
            message.content.remove(image_content)
            return True
        except (ValueError, AttributeError):
            return False

    async def _run_rag(self, turn_ctx: ChatContext, new_message) -> None:
        self._remove_previous_rag_message(turn_ctx)
        self._rag_speaking_motion_pending = False
        self._rag_speaking_motion_resource_name = ""
        self._contextual_motion_fallback_family = ""
        self._claim_grounding_active = False
        self._claim_grounding_context = ""
        self._claim_grounding_query = ""
        self._claim_grounding_abstention_added = False

        query = (getattr(new_message, "text_content", "") or "").strip()
        if not query:
            return
        turn_id = str(getattr(new_message, "id", "") or "")
        known_person_rag_intent = bool(
            turn_id and turn_id in self._known_person_rag_turn_ids
        )
        intent = _classify_user_intent(query)
        if (
            self._current_known_person is not None
            and (is_self_identity_question(query) or known_person_rag_intent)
        ):
            intent = "knowledge"
        contextual_followup = (
            _is_contextual_followup(query)
            and self._fresh_rag_or_directed_topic()
        )
        # A longer factual/general answer benefits from the same natural
        # speaking motion even when the LLM does not emit a {motion:...}
        # marker itself. This is a deterministic fallback under the marker
        # path: an explicit marker chosen later in the response still wins,
        # since _strip_and_select_contextual_motion overwrites the resource
        # name and re-sets pending when it finds one.
        if (
            RAG_SPEAKING_MOTION_ENABLED
            and intent == "knowledge"
            and len(_normalize_user_intent_text(query).split())
            >= GENERAL_SPEAKING_MOTION_MIN_WORDS
        ):
            self._rag_speaking_motion_pending = True
        if not self._is_rag_enabled():
            logger.info("External RAG disabled (runtime toggle or config).")
            latency_tracer.emit(
                "rag.agent",
                event="skip",
                turn_id=getattr(new_message, "id", None),
                critical_path=True,
                reason="disabled",
                intent=intent,
            )
            return
        if GATE_V2 and self._llm_role in {
            "command",
            "question_social",
            "chitchat",
        } and intent != "knowledge" and not contextual_followup:
            logger.info(
                "External RAG skipped for intent=%s query=%r",
                self._llm_role,
                query,
            )
            latency_tracer.emit(
                "rag.agent",
                event="skip",
                turn_id=getattr(new_message, "id", None),
                critical_path=True,
                reason="llm_role",
                intent=self._llm_role,
            )
            return
        if (
            not known_person_rag_intent
            and not contextual_followup
            and not _should_attempt_rag(query, intent)
        ) or self._should_skip_rag(query):
            logger.info("External RAG skipped for intent=%s query=%r", intent, query)
            latency_tracer.emit(
                "rag.agent",
                event="skip",
                turn_id=getattr(new_message, "id", None),
                critical_path=True,
                reason="gate",
                intent=intent,
            )
            return

        search_queries = self._build_rag_search_queries(turn_ctx, new_message, query)
        search_query = _rag_query_cache_key(search_queries)
        matching_prefetch = (
            RAG_ASYNC
            and self._rag_prefetch_task is not None
            and self._rag_prefetch_query == search_query
        )
        if not matching_prefetch and len(search_queries) == 1:
            language, _ = _resolve_query_language(
                query,
                stt_language=self._last_stt_language or None,
                previous_language=self._last_query_language,
                stt_language_mode=VOICE_SETTINGS.misc.stt_language_mode,
            )
            rewritten_query = await self._resolve_prefetch_rag_query(
                original_query=query,
                heuristic_query=search_query,
                last_directed=self._fresh_rag_topic() or self._fresh_directed_query(),
                language=language,
            )
            search_query = rewritten_query or search_query
            search_queries = (search_query,)
        rag_top_k = self._rag_top_k_for_query(query, search_query)

        rag_start = time.perf_counter()
        rag_context = self._get_cached_rag_context(search_query)
        if rag_context is not None:
            rag_ms = (time.perf_counter() - rag_start) * 1000.0
            self._commit_rag_topic(search_query, rag_context)
            self._rag_message_id = self._add_rag_message(
                turn_ctx=turn_ctx,
                new_message=new_message,
                rag_context=rag_context,
                rag_ms=rag_ms,
                search_query=search_query,
            )
            self._claim_grounding_active = _should_apply_claim_grounding(search_query)
            self._claim_grounding_context = rag_context if self._claim_grounding_active else ""
            self._claim_grounding_query = search_query if self._claim_grounding_active else ""
            self._set_grounded_wayfinding_motion(query, search_query, rag_context)
            logger.info(
                "External RAG cache hit: intent=%s search_ms=%.2f context_chars=%d claim_grounding=%s query=%r msg_id=%s",
                intent,
                rag_ms,
                len(rag_context or ""),
                self._claim_grounding_active,
                search_query,
                getattr(new_message, "id", None),
            )
            latency_tracer.emit(
                "rag.agent",
                event="cache_hit",
                duration_ms=rag_ms,
                turn_id=getattr(new_message, "id", None),
                critical_path=True,
                intent=intent,
                context_chars=len(rag_context or ""),
                claim_grounding=self._claim_grounding_active,
            )
            return
        rag_context = ""

        if self._visual_ui_runtime is not None:
            await self._visual_ui_runtime.set_thinking()

        kb_index = await self._ensure_rag_client()
        if kb_index is None:
            logger.warning("External RAG client not available.")
            return
        else:
            try:
                search_task: asyncio.Task | None = None
                if matching_prefetch:
                    rag_context = await asyncio.wait_for(
                        self._rag_prefetch_task,
                        timeout=RAG_ASYNC_WAIT_BUDGET_S,
                    )
                    search_query = (
                        self._rag_prefetch_resolved_query or search_query
                    )
                    logger.info(
                        "External RAG async prefetch ready: wait_ms=%.2f query=%r",
                        (time.perf_counter() - rag_start) * 1000.0,
                        search_query,
                    )
                else:
                    server_budget_s = max(
                        RAG_SEARCH_BUDGET_S,
                        RAG_FALLBACK_SEARCH_BUDGET_S,
                    )
                    search_task = asyncio.create_task(
                        self._search_rag_queries(
                            kb_index,
                            search_queries,
                            budget_s=server_budget_s,
                            top_k=rag_top_k,
                        )
                    )
                    rag_context = await asyncio.wait_for(
                        asyncio.shield(search_task),
                        timeout=RAG_SEARCH_BUDGET_S + RAG_RESPONSE_GRACE_S,
                    )
                if rag_context.strip():
                    self._put_cached_rag_context(search_query, rag_context.strip())
                    self._commit_rag_topic(search_query, rag_context.strip())
            except asyncio.TimeoutError:
                rag_ms = (time.perf_counter() - rag_start) * 1000.0
                budget_s = (
                    RAG_ASYNC_WAIT_BUDGET_S
                    if RAG_ASYNC and self._rag_prefetch_query == search_query
                    else RAG_SEARCH_BUDGET_S
                )
                logger.warning(
                    "External RAG budget exceeded: search_ms=%.2f budget_ms=%.2f query=%r",
                    rag_ms,
                    budget_s * 1000.0,
                    search_query,
                )
                latency_tracer.emit(
                    "rag.agent",
                    event="timeout",
                    duration_ms=rag_ms,
                    turn_id=getattr(new_message, "id", None),
                    critical_path=True,
                    intent=intent,
                    budget_ms=budget_s * 1000.0,
                )
                if RAG_ASYNC and self._rag_prefetch_query == search_query:
                    self._rag_prefetch_task = None
                    self._rag_prefetch_query = ""
                    self._rag_prefetch_resolved_query = ""
                # Preserve the fast path for normal queries, but when a search
                # hits the local response budget, continue waiting for the same
                # in-flight RAG request. A second full search repeats embedding
                # and vector lookup work on the critical path.
                fallback_started = time.perf_counter()
                try:
                    if search_task is None:
                        search_task = asyncio.create_task(
                            self._search_rag_queries(
                                kb_index,
                                search_queries,
                                budget_s=RAG_FALLBACK_SEARCH_BUDGET_S,
                                top_k=rag_top_k,
                            )
                        )
                    elapsed_s = time.perf_counter() - rag_start
                    remaining_s = max(
                        0.05,
                        RAG_FALLBACK_SEARCH_BUDGET_S
                        + RAG_RESPONSE_GRACE_S
                        - elapsed_s,
                    )
                    rag_context = await asyncio.wait_for(
                        search_task,
                        timeout=remaining_s,
                    )
                    logger.info(
                        "External RAG fallback completed from in-flight request: search_ms=%.2f budget_ms=%.2f query=%r",
                        (time.perf_counter() - fallback_started) * 1000.0,
                        RAG_FALLBACK_SEARCH_BUDGET_S * 1000.0,
                        search_query,
                    )
                    if rag_context.strip():
                        self._put_cached_rag_context(search_query, rag_context.strip())
                        self._commit_rag_topic(search_query, rag_context.strip())
                except Exception as fallback_exc:
                    logger.warning(
                        "External RAG fallback failed: budget_ms=%.2f query=%r error=%s",
                        RAG_FALLBACK_SEARCH_BUDGET_S * 1000.0,
                        search_query,
                        fallback_exc,
                    )
                    return
            except Exception as exc:
                cfg = self._rag_config
                self._rag_client = None
                self._rag_next_retry_ts = time.monotonic() + cfg.retry_seconds
                logger.warning("External RAG search failed: %s (retry in %ss)", exc, cfg.retry_seconds)
                return

        rag_ms = (time.perf_counter() - rag_start) * 1000.0
        rag_context = rag_context.strip()
        if not rag_context:
            logger.info(
                "External RAG: intent=%s search_ms=%.2f context_chars=0 query=%r msg_id=%s",
                intent,
                rag_ms,
                search_query,
                getattr(new_message, "id", None),
            )
            latency_tracer.emit(
                "rag.agent",
                event="empty",
                duration_ms=rag_ms,
                turn_id=getattr(new_message, "id", None),
                critical_path=True,
                intent=intent,
                context_chars=0,
            )
            return

        self._rag_message_id = self._add_rag_message(
            turn_ctx=turn_ctx,
            new_message=new_message,
            rag_context=rag_context,
            rag_ms=rag_ms,
            search_query=search_query,
        )
        self._claim_grounding_active = _should_apply_claim_grounding(search_query)
        self._claim_grounding_context = rag_context if self._claim_grounding_active else ""
        self._claim_grounding_query = search_query if self._claim_grounding_active else ""
        self._set_grounded_wayfinding_motion(query, search_query, rag_context)

        logger.info(
            "External RAG: intent=%s search_ms=%.2f context_chars=%d claim_grounding=%s query=%r msg_id=%s",
            intent,
            rag_ms,
            len(rag_context or ""),
            self._claim_grounding_active,
            search_query,
            getattr(new_message, "id", None),
        )
        latency_tracer.emit(
            "rag.agent",
            duration_ms=rag_ms,
            turn_id=getattr(new_message, "id", None),
            critical_path=True,
            intent=intent,
            context_chars=len(rag_context or ""),
            claim_grounding=self._claim_grounding_active,
        )

    def _set_grounded_wayfinding_motion(
        self,
        original_query: str,
        search_query: str,
        rag_context: str,
    ) -> None:
        if (
            not RAG_SPEAKING_MOTION_ENABLED
            or self._contextual_motion_blocked
            or not rag_context.strip()
            or not (
                _is_wayfinding_host_query(original_query)
                or _is_wayfinding_host_query(search_query)
            )
        ):
            return
        motion_name = self._gesture_processor.contextual_motion_for_family(
            "directional"
        )
        if motion_name is None:
            return
        self._rag_speaking_motion_resource_name = motion_name
        self._rag_speaking_motion_pending = True
        self._contextual_motion_fallback_family = "directional"
        logger.info(
            "Contextual LinkCraft motion selected by grounded wayfinding fallback: %s",
            motion_name,
        )

    async def _handle_control_turn(self, turn_ctx: ChatContext, new_message, query: str) -> None:
        self._remove_previous_rag_message(turn_ctx)
        await self._hard_stop_from_transcript(query, is_final=True)
        try:
            new_message.content = [query]
        except Exception:
            pass

    def _contextualize_rag_query(self, query: str) -> str:
        if _is_contextual_followup(query):
            topic = self._fresh_rag_topic() or self._fresh_directed_query()
            if topic:
                query = f"{topic} {query}"
        return _expand_common_stt_confusions(_strip_rag_leading_discourse(query))

    def _is_known_person_rag_turn(self, new_message, query: str) -> bool:
        if self._current_known_person is None:
            return False
        turn_id = str(getattr(new_message, "id", "") or "")
        return (
            is_self_identity_question(query)
            or bool(turn_id and turn_id in self._known_person_rag_turn_ids)
        )

    def _build_rag_search_query(self, turn_ctx: ChatContext, new_message, query: str) -> str:
        search_query = self._contextualize_rag_query(query)
        known_person = self._current_known_person
        if known_person is not None and self._is_known_person_rag_turn(new_message, query):
            hints = " ".join(
                part
                for part in (
                    known_person.canonical_name,
                    *known_person.aliases,
                    "Comtrade",
                )
                if part
            )
            if hints:
                return hints.strip()
        return search_query

    def _build_rag_search_queries(self, turn_ctx, new_message, query: str) -> tuple[str, ...]:
        base = self._build_rag_search_query(turn_ctx, new_message, query)
        if not RAG_MULTI_QUERY_ENABLED:
            return (base,)
        parts = _split_rag_subqueries(query, max_queries=RAG_MULTI_QUERY_MAX_QUERIES)
        if len(parts) < 2:
            return (base,)
        queries = tuple(self._contextualize_rag_query(part) for part in parts)
        logger.info("External RAG multi-query split: original=%r queries=%r", query, queries)
        return queries

    @staticmethod
    def _should_skip_rag(query: str) -> bool:
        normalized = re.sub(r"[^\w\sčćžšđ]", " ", query.lower(), flags=re.UNICODE)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized in RAG_SKIP_BACKCHANNELS

    def _get_cached_rag_context(self, query: str) -> str | None:
        if RAG_CACHE_TTL_S <= 0 or RAG_CACHE_MAX_ITEMS <= 0:
            return None
        key = _normalize_user_intent_text(query)
        if not key:
            return None
        item = self._rag_cache.get(key)
        if item is None:
            return None
        created_at, context = item
        if time.monotonic() - created_at > RAG_CACHE_TTL_S:
            self._rag_cache.pop(key, None)
            return None
        self._rag_cache.move_to_end(key)
        return context

    def _put_cached_rag_context(self, query: str, context: str) -> None:
        if RAG_CACHE_TTL_S <= 0 or RAG_CACHE_MAX_ITEMS <= 0 or not context:
            return
        key = _normalize_user_intent_text(query)
        if not key:
            return
        self._rag_cache[key] = (time.monotonic(), context)
        self._rag_cache.move_to_end(key)
        while len(self._rag_cache) > RAG_CACHE_MAX_ITEMS:
            self._rag_cache.popitem(last=False)

    def _remove_previous_rag_message(self, turn_ctx: ChatContext) -> None:
        if not self._rag_message_id:
            return

        try:
            turn_ctx.items = [
                item for item in turn_ctx.items
                if getattr(item, "id", None) != self._rag_message_id
            ]
        except Exception:
            pass

    def _inject_bounded_conversation_context(
        self,
        *,
        turn_ctx: ChatContext,
        new_message,
        current_query: str,
        current_transcript: str,
        acceptance_reason: str,
    ) -> None:
        snapshot = self._conversation_context.snapshot_before(
            current_transcript
        )
        current_message_id = getattr(new_message, "id", None)
        removed_history = 0

        try:
            retained_items = []
            for item in turn_ctx.items:
                item_id = getattr(item, "id", None)
                role = str(getattr(item, "role", "") or "").lower()
                is_historical_dialogue = (
                    role in {"user", "assistant"}
                    and item_id != current_message_id
                )
                is_previous_context = (
                    self._conversation_context_message_id is not None
                    and item_id == self._conversation_context_message_id
                )
                if is_historical_dialogue or is_previous_context:
                    removed_history += 1
                    continue
                retained_items.append(item)
            turn_ctx.items = retained_items
        except Exception as exc:
            logger.warning(
                "CONVERSATION_CONTEXT history_prune_failed error=%s",
                type(exc).__name__,
            )

        self._conversation_context_message_id = None
        has_previous_context = bool(
            snapshot.summary or snapshot.recent_messages
        )
        if has_previous_context:
            created_at = getattr(new_message, "created_at", None)
            if not isinstance(created_at, (int, float)):
                created_at = time.time()
            message = turn_ctx.add_message(
                role="developer",
                content=[snapshot.render(current_query)],
                created_at=created_at - 0.003,
                extra={"bounded_conversation_context": True},
            )
            self._conversation_context_message_id = getattr(
                message, "id", None
            )

        logger.info(
            "CONVERSATION_CONTEXT prepared acceptance=%s buffered=%d "
            "recent_sent=%d summary_words=%d context_added=%s "
            "history_removed=%d",
            acceptance_reason,
            snapshot.buffered_messages,
            len(snapshot.recent_messages),
            len(snapshot.summary.split()),
            has_previous_context,
            removed_history,
        )

    def _remember_conversation_facts(
        self,
        turn_ctx: ChatContext,
        new_message,
        query: str,
    ) -> None:
        for label, value in _extract_conversation_facts(query):
            self._conversation_facts[label] = value
            self._conversation_facts.move_to_end(label)
        while len(self._conversation_facts) > CONVERSATION_MEMORY_MAX_FACTS:
            self._conversation_facts.popitem(last=False)

        if self._conversation_memory_message_id:
            try:
                turn_ctx.items = [
                    item
                    for item in turn_ctx.items
                    if getattr(item, "id", None) != self._conversation_memory_message_id
                ]
            except Exception:
                pass
            self._conversation_memory_message_id = None

        if not self._conversation_facts:
            return
        facts = "; ".join(
            f"{label}: {value}" for label, value in self._conversation_facts.items()
        )[:CONVERSATION_MEMORY_MAX_CHARS]
        created_at = getattr(new_message, "created_at", None)
        if not isinstance(created_at, (int, float)):
            created_at = time.time()
        message = turn_ctx.add_message(
            role="developer",
            content=[runtime_prompt("context_protocol.short_memory", facts=facts)],
            created_at=created_at - 0.002,
            extra={"conversation_memory": True},
        )
        self._conversation_memory_message_id = getattr(message, "id", None)

    def _add_rag_message(
        self,
        *,
        turn_ctx: ChatContext,
        new_message,
        rag_context: str,
        rag_ms: float,
        search_query: str,
    ) -> str | None:
        created_at = getattr(new_message, "created_at", None)
        if not isinstance(created_at, (int, float)):
            created_at = time.time()
        created_at = created_at - 0.001

        reply_language = self._last_query_language
        identity_context = ""
        if self._current_known_person is not None:
            query_text = (getattr(new_message, "text_content", "") or "").strip()
            if self._is_known_person_rag_turn(new_message, query_text):
                identity_context = runtime_prompt(
                    "rag_protocol.identity_reference",
                    canonical_name=self._current_known_person.canonical_name,
                )
        wayfinding_query = _is_wayfinding_host_query(search_query)
        grounding_policy = runtime_prompt(
            "rag_protocol.grounding_wayfinding"
            if wayfinding_query
            else "rag_protocol.grounding_general"
        )

        content = runtime_prompt(
            "rag_protocol.message",
            reply_language=reply_language,
            rag_context=rag_context.strip() if rag_context else "(empty)",
            identity_context=identity_context,
            grounding_policy=grounding_policy,
        )

        message = turn_ctx.add_message(
            role="developer",
            content=[content],
            created_at=created_at,
            extra={
                "rag_search_ms": rag_ms,
            },
        )
        return getattr(message, "id", None)

        
    
    async def trigger_gesture(
        self,
        gesture: str,
        requested: bool = False,
    ):
        """Dispatch a gesture to the external gesture API."""
        normalized_gesture = normalize_gesture(gesture, ACTIVE_GESTURE_CATALOG_ID)
        if normalized_gesture is None:
            logger.warning("Ignoring unknown tool gesture: %r", gesture)
            return f"Gesture '{gesture}' is unknown."
        if normalized_gesture not in ALLOWED_GESTURE_SET:
            logger.warning(
                "Ignoring unavailable tool gesture '%s' for pool '%s'",
                normalized_gesture,
                ACTIVE_GESTURE_SAFETY_POOL,
            )
            return f"Gesture '{normalized_gesture}' is not available in the current gesture pool."

        logger.info("Dispatching gesture via API: %s", normalized_gesture)
        self._schedule_gesture_api_call(
            normalized_gesture,
            requested=requested,
            force_gesture=False,
        )
        return f"Gesture '{normalized_gesture}' triggered."

    @function_tool(
        name="remember_face",
        description=(
            "Start the consent-based face enrollment flow only after the current user "
            "explicitly says 'zapamti', 'zapamti me', 'zapamti moje lice', or an exact "
            "English equivalent. A one-turn authorization guard rejects every other call."
        ),
    )
    async def remember_face(self) -> str:
        now = time.monotonic()
        if now > self._face_enrollment_authorized_until:
            logger.warning("FACE_ENROLLMENT tool rejected reason=no_recent_voice_authorization")
            return "Face enrollment was not authorized by an explicit voice request. Do not start it."
        self._face_enrollment_authorized_until = 0.0

        try:
            active_prompt = get_prompt_builder().get_active()
        except Exception as exc:
            logger.warning("FACE_ENROLLMENT prompt safety check failed: %s", exc)
            return "Face enrollment is unavailable right now."
        if active_prompt.get("persona") == "comtrade_kids_lumi":
            logger.info("FACE_ENROLLMENT tool rejected reason=kids_persona")
            return "Face enrollment is disabled in the kids profile."

        try:
            status = await self._vision_api_json("GET", "/api/vision", timeout_s=1.0)
        except VisionApiError as exc:
            logger.warning("FACE_ENROLLMENT vision status failed: %s", exc)
            return "The face recognition camera is unavailable right now."

        feature = ((status.get("features") or {}).get("voice_enrollment") or {})
        if not feature.get("enabled"):
            logger.info("FACE_ENROLLMENT tool rejected reason=feature_disabled")
            return "Voice-triggered face enrollment is currently disabled."

        detected_at = status.get("last_detected_at")
        detected_recently = False
        try:
            detected_recently = (
                detected_at is not None and time.time() - float(detected_at) < 10.0
            )
        except (TypeError, ValueError):
            detected_recently = False
        target = status.get("target") or {}
        target_visible_recently = False
        try:
            target_visible_recently = (
                bool(target.get("visible"))
                and target.get("timestamp") is not None
                and time.time() - float(target["timestamp"]) < 10.0
            )
        except (TypeError, ValueError):
            target_visible_recently = False
        tracked_person_recently = False
        try:
            tracked_person_recently = (
                bool(status.get("person_present"))
                and bool(status.get("track_id") or target.get("track_id"))
                and (
                    detected_recently
                    or (
                        target.get("timestamp") is not None
                        and time.time() - float(target["timestamp"]) < 10.0
                    )
                )
            )
        except (TypeError, ValueError):
            tracked_person_recently = False
        if not (tracked_person_recently or target_visible_recently):
            logger.info("FACE_ENROLLMENT tool rejected reason=no_recent_face")
            return "I cannot see a person clearly enough to remember a face right now."

        identity = status.get("identity") or self._current_identity or {}
        if (
            identity.get("status") == "known"
            and identity.get("name")
            and (
                identity.get("face_id")
                or str(identity.get("source") or "") != "voice_introduction"
            )
        ):
            await self._apply_person_identity(identity)
            return f"This person is already remembered as {identity['name']}."

        initial_name = str(
            identity.get("display_name")
            or identity.get("name")
            or self._introduced_identity_name
            or ""
        ).strip()
        # An explicit "zapamti me" is itself consent, so skip the redundant
        # question. A name given only because the robot asked "who are you"
        # is not consent - that path flags this so the flow asks for real.
        needs_explicit_consent = self._face_enrollment_needs_explicit_consent
        self._face_enrollment_needs_explicit_consent = False
        result = await self._start_face_enrollment(
            initial_name=initial_name or None,
            consent_already_given=not needs_explicit_consent,
        )
        if result.get("status") == "known":
            return "The enrollment flow completed and already gave its closing confirmation. Do not add another response."
        return "The enrollment flow ended without storing a face. Do not claim that the face was remembered."

    @function_tool(
        name="forget_me",
        description=(
            "Delete the current person's remembered face when they explicitly ask to be "
            "forgotten or to have their face data deleted. Only call this if a name is "
            "currently known for this person; otherwise there is nothing to forget."
        ),
    )
    async def forget_me(self) -> str:
        identity = self._current_identity or {}
        face_id = identity.get("face_id")
        name = identity.get("name")
        if identity.get("status") != "known" or not (face_id or name):
            return "There is no remembered face for this person right now."

        try:
            result = await self._vision_api_json(
                "POST",
                "/api/vision/face/forget",
                {"face_id": face_id, "name": name},
            )
        except VisionApiError as exc:
            logger.warning("Forget-me request failed: %s", exc)
            return "I could not reach the face recognition system to forget this person. Please try again shortly."

        self._current_identity = {"status": "unknown"}
        self._current_known_person = None
        self._base_instructions = _build_base_instructions(self._gesture_processor, person_name_prompt="")
        await self._refresh_runtime_instructions()
        logger.info("Forget-me requested face_id=%s name=%s result=%s", face_id, name, result.get("state"))
        return f"Done, I will no longer remember {name}'s face."

    @function_tool(
        name="start_survey",
        description=(
            "Start the spoken survey only when the user explicitly asks to begin the survey or anketa. "
            "This pauses normal conversation, runs the full survey flow, and then returns to normal conversation. "
            "Pass language='sr' when the user asks in Serbian, and language='en' when the user asks in English."
        ),
    )
    async def start_survey(self, language: str = "sr") -> str:
        """Start the survey flow when the user explicitly asks for it."""
        survey_language = normalize_survey_language(language)
        if not self._is_survey_enabled():
            if survey_language == "en":
                return (
                    'Survey is currently unavailable. Reply with: '
                    '"The survey is currently unavailable." Do not start the survey and do not add extra suggestions.'
                )
            return (
                'Survey is currently unavailable. Reply with: '
                '"Anketa trenutno nije dostupna." Do not start the survey and do not add extra suggestions.'
            )

        if self._survey_running:
            return "A survey is already in progress. Do not start another one."

        self._survey_running = True
        try:
            await SurveyFlowTask(survey_state=load_survey_state(), language=survey_language)
        finally:
            self._survey_running = False

        return (
            "The survey flow has finished. Resume normal conversation. "
            "Do not repeat the survey closing message unless the user asks."
        )

    @function_tool(
        name="start_quiz",
        description=(
            "Start the spoken quiz only when the user explicitly asks to begin the quiz. "
            "This pauses normal conversation, runs the full quiz flow, and then returns to normal conversation. "
            "Pass language='sr' when the user asks in Serbian, and language='en' when the user asks in English."
        ),
    )
    async def start_quiz(self, language: str = "sr") -> str:
        """Start the quiz flow when the user explicitly asks for it."""
        return await self._start_quiz_with_language(language)

    async def _start_quiz_with_language(self, language: str = "sr") -> str:
        quiz_language = normalize_quiz_language(language)
        if not self._is_quiz_enabled():
            if quiz_language == "en":
                return (
                    'Quiz is currently unavailable. Reply with: '
                    '"The quiz is currently unavailable." Do not start the quiz and do not add extra suggestions.'
                )
            return (
                'Quiz is currently unavailable. Reply with: '
                '"Kviz trenutno nije dostupan." Do not start the quiz and do not add extra suggestions.'
            )

        if self._quiz_running:
            return "A quiz is already in progress. Do not start another one."

        self._quiz_running = True
        try:
            await QuizFlowTask(quiz_state=load_quiz_state(), language=quiz_language)
        finally:
            self._quiz_running = False

        return (
            "The quiz flow has finished. Resume normal conversation. "
            "Do not repeat the quiz closing message unless the user asks."
        )

    async def _flash_qa_display(self, primary: str, secondary: str) -> None:
        if self._visual_ui_runtime is None:
            return
        try:
            video_path = await asyncio.to_thread(render_flash_video, primary, secondary)
        except Exception:
            logger.warning("QA_DISPLAY render failed for primary=%r", primary, exc_info=True)
            return
        shown = await self._visual_ui_runtime.flash_video(video_path)
        logger.info("QA_DISPLAY primary=%r secondary=%r shown=%s", primary, secondary, shown)

    async def _show_face_scan_visual(self, reason: str) -> bool:
        if self._visual_ui_runtime is None:
            return False
        try:
            shown = await self._visual_ui_runtime.set_scanning()
        except Exception:
            logger.debug("Face scan visual state failed reason=%s", reason, exc_info=True)
            return False
        logger.debug("Face scan visual state reason=%s shown=%s", reason, shown)
        return bool(shown)

    @function_tool(
        name="get_current_time",
        description=(
            "Get the current time for a city. Call this whenever the user asks what "
            "time it is, in any phrasing, instead of guessing the time yourself. Pass "
            "the city they mentioned; omit it to default to Belgrade."
        ),
    )
    async def get_current_time(self, city: str = "Belgrade") -> str:
        result = await get_time(city, language=self._last_query_language)
        if result is None:
            fallback = await get_time("Belgrade", language=self._last_query_language)
            if fallback is None:
                return "The time service is unavailable right now."
            return (
                f"There is no known city called '{city}'. The time in Belgrade is "
                f"hour {fallback.hour}, minute {fallback.minute}. Tell the user their "
                "city was not recognized and give the Belgrade time instead. Say the "
                "hour and minute as two separate spoken numbers, never as HH:MM."
            )
        return (
            f"The current time in {result.city} is hour {result.hour}, minute {result.minute}. Say the "
            "hour and minute as two separate spoken numbers, never as HH:MM."
        )

    @function_tool(
        name="get_current_weather",
        description=(
            "Get the current weather/temperature for a region. Call this whenever the "
            "user asks about weather, temperature, or conditions outside, in any "
            "phrasing, instead of guessing the weather yourself. Pass the region they "
            "mentioned and units ('metric' or 'imperial'); omit both to default to "
            "Belgrade and metric."
        ),
    )
    async def get_current_weather(self, region: str = "Belgrade", units: str = "metric") -> str:
        try:
            resolved_region, temperature = await get_weather(region, units)
        except Exception as exc:
            logger.warning("get_current_weather tool failed for region=%s: %s", region, exc)
            return f"The weather service is unavailable right now for {region}."
        unit_symbol = "°F" if units.strip().lower() == "imperial" else "°C"
        return f"The current temperature in {resolved_region} is {temperature}{unit_symbol}."

    def _render_instructions(self) -> str:
        quiz_enabled = self._is_quiz_enabled()
        survey_enabled = self._is_survey_enabled()
        flow_rules = [
            runtime_prompt("flow_routing.header"),
            runtime_prompt("flow_routing.state", flow_name="Quiz", state="enabled" if quiz_enabled else "disabled"),
            runtime_prompt("flow_routing.state", flow_name="Survey", state="enabled" if survey_enabled else "disabled"),
        ]
        flow_rules.append(runtime_prompt("flow_routing.quiz_enabled" if quiz_enabled else "flow_routing.quiz_disabled"))
        flow_rules.append(runtime_prompt("flow_routing.survey_enabled" if survey_enabled else "flow_routing.survey_disabled"))
        flow_rules.append(runtime_prompt("flow_routing.common"))

        return (
            f"{self._base_instructions}\n\n"
            + "\n".join(flow_rules)
            + "\n"
        )

    def _strip_and_select_contextual_motion(self, text: str) -> str:
        cleaned, motion_name = self._gesture_processor.strip_contextual_motion_tokens(text)
        if motion_name is None:
            return cleaned
        if (
            self._contextual_motion_fallback_family
            and not motion_name.startswith(
                ("iwaswrong_gesture_", "notsure_gesture_", "joking_gesture_")
            )
        ):
            logger.info(
                "Contextual LinkCraft marker ignored in favor of %s fallback: %s",
                self._contextual_motion_fallback_family,
                motion_name,
            )
            return cleaned
        if motion_name.startswith(
            ("iwaswrong_gesture_", "notsure_gesture_", "joking_gesture_")
        ):
            self._contextual_motion_fallback_family = ""
        if (
            not RAG_SPEAKING_MOTION_ENABLED
            or self._contextual_motion_blocked
            or self._contextual_motion_selected_for_response
        ):
            logger.debug(
                "Contextual LinkCraft marker ignored: motion=%s blocked=%s selected=%s",
                motion_name,
                self._contextual_motion_blocked,
                self._contextual_motion_selected_for_response,
            )
            return cleaned

        self._contextual_motion_selected_for_response = True
        self._rag_speaking_motion_resource_name = motion_name
        self._rag_speaking_motion_pending = True
        logger.info("Contextual LinkCraft motion selected by response: %s", motion_name)
        return cleaned

    async def _clean_text_stream(self, text, cleaner):
        carry = ""
        sentence_boundary = re.compile(r"([.!?…]+[\"')\]]?(?:\s+|$)|\n+)")
        soft_boundary = re.compile(r"([,;:]+[\"')\]]?\s+|\s+[–—-]\s+)")

        def split_emit(value: str, *, force: bool = False) -> tuple[str | None, str]:
            if not value:
                return None, ""
            if force:
                return value, ""
            sentence_end = None
            for match in sentence_boundary.finditer(value):
                if match.end() >= TTS_STREAM_MIN_CHARS:
                    sentence_end = match.end()
                    break
            soft_end = None
            for match in soft_boundary.finditer(value):
                if match.end() >= TTS_STREAM_SOFT_CHARS:
                    soft_end = match.end()
                    break
            boundary = min(
                end for end in (sentence_end, soft_end) if end is not None
            ) if sentence_end is not None or soft_end is not None else None
            if boundary is not None:
                return value[:boundary], value[boundary:]
            if len(value) >= TTS_STREAM_MAX_CHARS:
                cut = max(value.rfind(" ", 0, TTS_STREAM_MAX_CHARS), value.rfind(",", 0, TTS_STREAM_MAX_CHARS))
                if cut < TTS_STREAM_MIN_CHARS:
                    cut = TTS_STREAM_MAX_CHARS
                return value[:cut], value[cut:]
            return None, value

        async for chunk in text:
            carry += chunk
            while True:
                emit, carry = split_emit(carry)
                if not emit:
                    break
                stripped = self._gesture_processor.strip(emit, trigger=False)
                if stripped:
                    yield cleaner(stripped)
        if carry:
            emit, _ = split_emit(carry, force=True)
            stripped = self._gesture_processor.strip(emit, trigger=False)
            if stripped:
                yield cleaner(stripped)

    async def _clean_tts_text_stream(self, text):
        buffer = ""
        spoken_chars = 0
        explanation_gesture_count = 0
        emitted_chunks = 0
        sentence_boundary = re.compile(r"([.!?…]+[\"')\]]?(?:\s+|$)|\n+)")
        soft_boundary = re.compile(r"([,;:]+[\"')\]]?\s+|\s+[–—-]\s+)")

        def maybe_schedule_explanation_gestures(cleaned: str) -> None:
            nonlocal spoken_chars, explanation_gesture_count
            spoken_chars += len(cleaned)
            while (
                explanation_gesture_count < len(TTS_EXPLANATION_GESTURE_THRESHOLDS)
                and spoken_chars >= TTS_EXPLANATION_GESTURE_THRESHOLDS[explanation_gesture_count]
            ):
                delay_s = 0.45 + (5.5 * explanation_gesture_count)
                if self._gesture_processor.schedule_explanation(
                    delay_s=delay_s,
                    sequence_index=explanation_gesture_count,
                ):
                    logger.info(
                        "Queued explanation gesture %d at spoken_chars=%d",
                        explanation_gesture_count + 1,
                        spoken_chars,
                    )
                explanation_gesture_count += 1

        def split_emit(value: str, *, force: bool = False) -> tuple[str | None, str]:
            if not value:
                return None, ""

            if force:
                return value, ""

            boundary = None
            for match in sentence_boundary.finditer(value):
                if match.end() >= TTS_STREAM_MIN_CHARS:
                    boundary = match.end()
                    break

            if self._claim_grounding_active:
                if boundary is not None:
                    return value[:boundary], value[boundary:]
                # Claim-level grounding needs an atomic sentence/claim. For
                # host lookup turns, avoid streaming tiny soft chunks such as
                # "walk " before we can decide whether the complete claim is
                # supported by RAG_CONTEXT. Keep a bounded escape hatch for
                # punctuation-free model output.
                if len(value) < max(TTS_STREAM_MAX_CHARS * 3, 180):
                    return None, value

            soft_min_chars = TTS_STREAM_FIRST_CHARS if emitted_chunks == 0 else TTS_STREAM_SOFT_CHARS
            soft_boundary_end = None
            for match in soft_boundary.finditer(value):
                if match.end() >= soft_min_chars:
                    soft_boundary_end = match.end()
                    break

            natural_boundary = min(
                end for end in (boundary, soft_boundary_end) if end is not None
            ) if boundary is not None or soft_boundary_end is not None else None
            if natural_boundary is not None:
                return value[:natural_boundary], value[natural_boundary:]

            if emitted_chunks == 0 and len(value) >= soft_min_chars:
                cut_window = min(len(value), max(soft_min_chars + 32, soft_min_chars))
                cut = value.rfind(" ", 0, cut_window)
                if cut >= TTS_STREAM_MIN_CHARS:
                    return value[: cut + 1], value[cut + 1 :]

            if len(value) >= TTS_STREAM_MAX_CHARS:
                cut = max(value.rfind(" ", 0, TTS_STREAM_MAX_CHARS), value.rfind(",", 0, TTS_STREAM_MAX_CHARS))
                if cut < TTS_STREAM_MIN_CHARS:
                    cut = TTS_STREAM_MAX_CHARS
                return value[:cut], value[cut:]

            return None, value

        async for chunk in text:
            buffer += chunk
            while True:
                emit, buffer = split_emit(buffer)
                if not emit:
                    break
                contextual_cleaned = self._strip_and_select_contextual_motion(emit)
                intent_cleaned = (
                    self._gesture_intent_runtime.strip_and_queue(contextual_cleaned)
                    if self._gesture_intent_runtime is not None
                    else contextual_cleaned
                )
                stripped = self._gesture_processor.strip(intent_cleaned, trigger=True)
                if self._gesture_processor.consume_dispatch_flag():
                    # A preset gesture just fired for this turn. Never also let
                    # the LinkCraft speaking-motion auto-trigger claim the same
                    # arms right after it — LinkCraft would physically override
                    # the requested preset before it finishes.
                    self._rag_speaking_motion_pending = False
                if self._claim_grounding_active:
                    stripped, dropped, added_abstention = _filter_host_grounded_tts_segment(
                        stripped,
                        self._claim_grounding_context,
                        language=self._last_query_language,
                        abstention_already_added=self._claim_grounding_abstention_added,
                    )
                    if dropped:
                        logger.info(
                            "claim_grounding dropped unsupported segment query=%r segment=%r replacement=%s",
                            self._claim_grounding_query,
                            emit.strip(),
                            added_abstention,
                        )
                    if added_abstention:
                        self._claim_grounding_abstention_added = True
                cleaned = _clean_spoken_text(stripped, language=self._last_query_language)
                if cleaned:
                    if (
                        self._gesture_intent_runtime is not None
                        and sentence_boundary.search(emit)
                    ):
                        self._gesture_intent_runtime.on_clause_boundary()
                    maybe_schedule_explanation_gestures(cleaned)
                    emitted_chunks += 1
                    yield cleaned + (" " if emit.endswith((" ", "\n")) else "")

        emit, _ = split_emit(buffer, force=True)
        if emit:
            contextual_cleaned = self._strip_and_select_contextual_motion(emit)
            intent_cleaned = (
                self._gesture_intent_runtime.strip_and_queue(contextual_cleaned)
                if self._gesture_intent_runtime is not None
                else contextual_cleaned
            )
            stripped = self._gesture_processor.strip(intent_cleaned, trigger=True)
            if self._gesture_processor.consume_dispatch_flag():
                self._rag_speaking_motion_pending = False
            if self._claim_grounding_active:
                stripped, dropped, added_abstention = _filter_host_grounded_tts_segment(
                    stripped,
                    self._claim_grounding_context,
                    language=self._last_query_language,
                    abstention_already_added=self._claim_grounding_abstention_added,
                )
                if dropped:
                    logger.info(
                        "claim_grounding dropped unsupported final segment query=%r segment=%r replacement=%s",
                        self._claim_grounding_query,
                        emit.strip(),
                        added_abstention,
                    )
                if added_abstention:
                    self._claim_grounding_abstention_added = True
            cleaned = _clean_spoken_text(stripped, language=self._last_query_language)
            if cleaned:
                if self._gesture_intent_runtime is not None:
                    self._gesture_intent_runtime.on_clause_boundary()
                maybe_schedule_explanation_gestures(cleaned)
                emitted_chunks += 1
                yield cleaned

    async def tts_node(self, text, model_settings):
        tts_started = time.perf_counter()
        first_clean_chunk_emitted = False
        first_audio_frame_emitted = False
        # This buffer feeds the duration estimate when actual playback enters
        # the speaking state. Reset it for every synthesized response.
        self._current_tts_text = ""
        self._rag_speaking_motion_dispatched = False
        self._contextual_motion_selected_for_response = False
        # A semantic marker at the start of a compliant LLM reply replaces
        # this fallback before playback. If the model omits its
        # marker, the answer still gets one varied speaking motion. Explicit
        # physical-command turns remain blocked.
        if RAG_SPEAKING_MOTION_ENABLED and not self._contextual_motion_blocked:
            self._rag_speaking_motion_pending = True
        trim_enabled = self._reply_shape_active and POSTGEN_TRIM_ENABLED
        allow_social_reciprocity = (
            SOCIAL_RECIPROCITY_ENABLED
            and self._llm_role in {"question_social", "chitchat"}
        )
        shaped_text = (
            _trim_trailing_reply_filler_stream(
                text,
                allow_social_reciprocity=allow_social_reciprocity,
            )
            if trim_enabled
            else text
        )

        async def remember_spoken_text():
            nonlocal first_clean_chunk_emitted
            async for chunk in self._clean_tts_text_stream(shaped_text):
                if not first_clean_chunk_emitted:
                    first_clean_chunk_emitted = True
                    latency_tracer.emit(
                        "tts.first_text_chunk",
                        duration_ms=(time.perf_counter() - tts_started) * 1000.0,
                        critical_path=True,
                        chars=len(str(chunk)),
                    )
                self._current_tts_text = (
                    self._current_tts_text + " " + str(chunk)
                )[-1200:]
                yield chunk

        try:
            async for frame in super().tts_node(
                remember_spoken_text(), model_settings
            ):
                if not first_audio_frame_emitted:
                    first_audio_frame_emitted = True
                    latency_tracer.emit(
                        "tts.first_audio_frame",
                        duration_ms=(time.perf_counter() - tts_started) * 1000.0,
                        critical_path=True,
                    )
                yield frame
        finally:
            final_duration_s = _estimate_tts_duration_s(self._current_tts_text)
            if self._rag_speaking_motion_dispatched and final_duration_s is not None:
                self._track_task(
                    asyncio.create_task(
                        self._extend_rag_speaking_motion(final_duration_s),
                        name="rag-speaking-motion:extend-final-duration",
                    )
                )
            latency_tracer.emit(
                "tts.total",
                duration_ms=(time.perf_counter() - tts_started) * 1000.0,
                critical_path=True,
                emitted_audio=first_audio_frame_emitted,
                chars=len(self._current_tts_text),
            )
            if self._reply_shape_active:
                spoken_text = self._current_tts_text.strip()
                logger.info(
                    "REPLY_SHAPE_METRIC role=%s language=%s words=%d chars=%d "
                    "terminal_question=%s known_filler=%s trim_enabled=%s "
                    "max_tokens=%d",
                    self._llm_role,
                    self._last_query_language,
                    len(re.findall(r"\b[\w'-]+\b", spoken_text, flags=re.UNICODE)),
                    len(spoken_text),
                    spoken_text.endswith(("?", "？")),
                    _trim_trailing_reply_filler(
                        spoken_text,
                        allow_social_reciprocity=allow_social_reciprocity,
                    )
                    != spoken_text,
                    trim_enabled,
                    HOST_REPLY_MAX_TOKENS,
                )

    async def llm_node(self, chat_ctx: ChatContext, tools: list, model_settings):
        if self._suppress_next_reply:
            self._suppress_next_reply = False
            logger.info("Suppressed LLM reply for control/backchannel turn")
            return

        if time.monotonic() <= self._face_enrollment_authorized_until:
            model_settings.tool_choice = {
                "type": "function",
                "function": {"name": "remember_face"},
            }
            logger.info("FACE_ENROLLMENT tool_choice=remember_face source=voice_trigger")

        use_fast = (
            LLM_ROUTING
            and self._fast_llm is not None
            and self._llm_role in {"question_social", "chitchat", "query_rewrite", "command"}
        )
        deployment = (
            VOICE_SETTINGS.llm.fast_deployment
            if use_fast
            else AZURE_OPENAI_DEPLOYMENT
        )
        reply_max_tokens = (
            HOST_REPLY_MAX_TOKENS if self._reply_shape_active else None
        )
        logger.info(
            "LLM route: role=%s model=%s deployment=%s routing=%s "
            "reply_shape=%s max_completion_tokens=%s",
            self._llm_role,
            (VOICE_SETTINGS.llm.fast_model or deployment) if use_fast else CHOSEN_COMPLETION_MODEL,
            deployment,
            use_fast,
            self._reply_shape_active,
            reply_max_tokens if reply_max_tokens is not None else "provider_default",
        )
        activity = self._get_activity_or_raise()
        selected_llm = self._fast_llm if use_fast else activity.llm
        cap_supported = (
            reply_max_tokens is not None
            and isinstance(selected_llm, openai.LLM)
        )
        if use_fast or cap_supported:
            chat_kwargs = {
                "chat_ctx": chat_ctx,
                "tools": tools,
                "tool_choice": model_settings.tool_choice,
                "conn_options": activity.session.conn_options.llm_conn_options,
            }
            if cap_supported:
                chat_kwargs["extra_kwargs"] = {
                    "max_completion_tokens": reply_max_tokens,
                }
            result = selected_llm.chat(**chat_kwargs)
        else:
            result = super().llm_node(chat_ctx, tools, model_settings)
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, str):
            yield result
            return
        if hasattr(result, "__aiter__"):
            async for chunk in result:
                yield chunk
            return
        if result is not None:
            yield result

    async def transcription_node(self, text, model_settings):
        def clean_transcription_segment(segment: str) -> str:
            contextual_cleaned, _ = (
                self._gesture_processor.strip_contextual_motion_tokens(
                    segment,
                    allow_selection=False,
                )
            )
            return _guard_name(contextual_cleaned)

        async for seg in super().transcription_node(
            self._clean_text_stream(text, clean_transcription_segment), model_settings
        ):
            yield seg

    async def _say_with_retry(self, session: AgentSession, text: str) -> None:
        last_exc: Exception | None = None
        for delay in (0.0, 0.05, 0.2, 0.5):
            if delay:
                await asyncio.sleep(delay)
            try:
                await session.say(
                    text=text,
                    allow_interruptions=True,
                    add_to_chat_ctx=True,
                )
                return
            except RuntimeError as exc:
                last_exc = exc
        logger.warning("Unable to enqueue command speech: %s", last_exc)

    def _schedule_gesture_api_call(
        self,
        gesture: str,
        *,
        requested: bool,
        force_gesture: bool = False,
    ) -> None:
        task = asyncio.create_task(
            self._call_gesture_api(
                gesture,
                requested=requested,
                force_gesture=force_gesture,
            ),
            name=f"gesture-api:{gesture}",
        )
        self._track_task(task)

    def _schedule_gesture_idle_signal(
        self,
        signal: str,
        *,
        active: bool | None = None,
    ) -> None:
        if not GESTURES_ENABLED:
            return
        task = asyncio.create_task(
            self._call_gesture_idle_signal(signal, active=active),
            name=f"gesture-idle:{signal}",
        )
        self._track_task(task)

    async def _call_gesture_idle_signal(
        self,
        signal: str,
        *,
        active: bool | None = None,
    ) -> None:
        if signal == "conversation":
            query = urllib.parse.urlencode(
                {"active": "true" if active else "false"}
            )
            url = f"{GESTURE_API_URL}/idle-state/conversation?{query}"
        elif signal == "activity":
            url = f"{GESTURE_API_URL}/idle-state/activity"
        else:
            return
        try:
            await self._http_call(url, method="POST", timeout_s=0.5)
        except Exception as exc:
            logger.debug("Gesture idle signal failed: signal=%s error=%s", signal, exc)

    async def _close_gesture_idle_session(self) -> None:
        await self._call_gesture_idle_signal("conversation", active=False)

    def _offer_enrollment_handshake(self) -> bool:
        """Offer a hand only when the current bridge and safety pool allow it."""

        if not self._gesture_processor.reachable:
            logger.info("FACE_ENROLLMENT handshake=skipped reason=bridge_unavailable")
            return False
        if "shake hand" not in self._gesture_processor.active_gestures:
            logger.info("FACE_ENROLLMENT handshake=skipped reason=gesture_not_allowed")
            return False
        dispatched = self._gesture_processor.maybe_dispatch("shake hand")
        logger.info(
            "FACE_ENROLLMENT handshake=%s",
            "dispatched" if dispatched else "skipped",
        )
        return dispatched

    async def _vision_api_json(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        timeout_s: float = 5.0,
    ) -> dict:
        url = f"{SUPERVISOR_API_URL}{path}"

        def _do_request() -> dict:
            data = None
            headers = {}
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"

            req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    body = resp.read().decode("utf-8", "replace")
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                try:
                    parsed = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    parsed = {"detail": body}
                raise VisionApiError(f"Vision API returned HTTP {exc.code}", status=exc.code, payload=parsed) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise VisionApiError(f"Vision API request failed: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise VisionApiError("Vision API returned invalid JSON") from exc

        return await asyncio.to_thread(_do_request)

    async def _resolve_initial_greeting(self) -> InitialGreetingDecision:
        """Resolve one initial greeting within a bounded identity wait."""

        started_at = time.monotonic()
        configured_greeting = (
            self._initial_greeting_override
            or get_initial_greeting_override()
            or build_initial_greeting().strip()
        )
        if self._suppress_initial_greeting:
            return InitialGreetingDecision(state="suppressed")

        deadline = started_at + FACE_IDENTITY_CHECK_TIMEOUT_S
        last_identity: dict = {}
        saw_person = False
        face_recognition_enabled = False
        scan_visual_shown = False
        while True:
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0:
                break
            try:
                status = await self._vision_api_json(
                    "GET",
                    "/api/vision",
                    timeout_s=max(0.05, min(0.15, remaining_s)),
                )
            except VisionApiError as exc:
                logger.info("Initial greeting identity check unavailable: %s", exc)
                break
            except Exception:
                logger.exception("Unexpected initial greeting identity check failure")
                break

            face_recognition_enabled = bool(
                ((status.get("features") or {}).get("face_recognition") or {}).get("enabled")
            )
            last_detected_at = status.get("last_detected_at")
            detected_recently = (
                last_detected_at is not None and time.time() - float(last_detected_at) < 10.0
            )
            person_ready = bool(status.get("person_present")) and detected_recently
            saw_person = saw_person or person_ready
            identity = status.get("identity") or {}
            if person_ready:
                last_identity = identity
                if not scan_visual_shown:
                    self._track_task(
                        asyncio.create_task(
                            self._show_face_scan_visual("face_identity_check"),
                            name="initial-face-scan-visual",
                        )
                    )
                    scan_visual_shown = True
                if identity.get("status") == "known" and identity.get("name"):
                    return InitialGreetingDecision(
                        state="known",
                        identity=identity,
                        configured_greeting=configured_greeting,
                        waited_ms=(time.monotonic() - started_at) * 1000.0,
                    )

            if not face_recognition_enabled or time.monotonic() >= deadline:
                break
            await asyncio.sleep(FACE_IDENTITY_CHECK_POLL_S)

        waited_ms = (time.monotonic() - started_at) * 1000.0
        if saw_person or face_recognition_enabled:
            return InitialGreetingDecision(
                state="unknown",
                identity=last_identity,
                configured_greeting=configured_greeting,
                waited_ms=waited_ms,
            )
        return InitialGreetingDecision(
            state="generic",
            configured_greeting=configured_greeting,
            waited_ms=waited_ms,
        )

    async def _emit_initial_greeting(self, decision: InitialGreetingDecision) -> None:
        if decision.state == "suppressed" or self._initial_greeting_started:
            return

        self._initial_greeting_started = True
        language_mode = get_stt_language_mode()
        try:
            if decision.state == "known" and decision.identity:
                identity = decision.identity
                self._pending_unknown_face = False
                self._introduced_identity_name = ""
                await self._apply_person_identity(identity, greet=False)
                self._last_greeted_face_key = str(
                    identity.get("face_id") or identity.get("name") or ""
                )
                instructions = _face_identity_greeting_instructions(
                    str(identity.get("display_name") or identity["name"]),
                    str(identity.get("fun_fact") or ""),
                    language_mode=language_mode,
                )
            elif decision.state == "unknown":
                self._pending_unknown_face = True
                instructions = _unknown_face_greeting_instructions(language_mode)
            else:
                instructions = _configured_greeting_instructions(
                    decision.configured_greeting,
                    language_mode,
                )

            if (
                INITIAL_GREETING_SPEAKING_MOTION_ENABLED
                and RAG_SPEAKING_MOTION_ENABLED
                and len(decision.configured_greeting) >= INITIAL_GREETING_SPEAKING_MOTION_MIN_CHARS
            ):
                self._rag_speaking_motion_pending = True
                self._rag_speaking_motion_resource_name = INITIAL_GREETING_SPEAKING_MOTION_NAME
            self.session.generate_reply(
                instructions=instructions,
                allow_interruptions=True,
            )
            logger.info(
                "Initial greeting state=%s language_mode=%s identity_wait_ms=%.1f llm_calls=1",
                decision.state,
                language_mode,
                decision.waited_ms,
            )
        finally:
            self._initial_greeting_completed = True

    async def _apply_person_identity(self, identity: dict, *, greet: bool = False) -> None:
        name = str(identity.get("name") or "").strip()
        if not name:
            return
        display_name = str(identity.get("display_name") or name).strip() or name
        resolved_known_person = resolve_known_person_identity(name)
        raw_aliases = identity.get("aliases") or ()
        aliases = tuple(
            str(alias).strip()
            for alias in raw_aliases
            if str(alias).strip()
        )
        raw_canonical_name = str(identity.get("canonical_name") or "").strip()
        if (
            resolved_known_person.known
            and (
                not raw_canonical_name
                or raw_canonical_name.lower() in {name.lower(), display_name.lower()}
            )
        ):
            canonical_name = resolved_known_person.canonical_name
        else:
            canonical_name = raw_canonical_name or resolved_known_person.canonical_name or display_name
        known_person = KnownPersonIdentity(
            recognized_name=display_name,
            canonical_name=canonical_name,
            aliases=aliases or resolved_known_person.aliases,
            public_hint=resolved_known_person.public_hint,
        )
        alias_text = ", ".join(known_person.aliases)
        source = str(identity.get("source") or "face_recognition")
        source_hint = runtime_prompt(
            "identity_context.introduced_by_voice"
            if source == "voice_introduction"
            else "identity_context.recognized_by_face",
            display_name=display_name,
        )
        self._current_identity = identity
        self._current_known_person = known_person
        self._pending_unknown_face = False
        self._introduced_identity_name = display_name
        identity_hint_parts = [
            source_hint,
            runtime_prompt("identity_context.canonical", canonical_name=canonical_name),
            runtime_prompt("identity_context.self_question", canonical_name=canonical_name),
        ]
        if alias_text:
            identity_hint_parts.append(runtime_prompt("identity_context.aliases", aliases=alias_text))
        group_name = str(identity.get("group_name") or "").strip()
        fun_fact = str(identity.get("fun_fact") or "").strip()
        if group_name:
            identity_hint_parts.append(runtime_prompt("identity_context.group", group_name=group_name))
        if fun_fact:
            identity_hint_parts.append(
                runtime_prompt("identity_context.fun_fact", fun_fact=fun_fact)
            )
        if known_person.public_hint:
            identity_hint_parts.append(known_person.public_hint)
        person_name_prompt = (
            " ".join(identity_hint_parts)
            + " "
            + runtime_prompt(
                "identity_context.greeting",
                display_name=display_name,
                greeting_name=first_name_vocative(display_name),
            )
        )
        self._base_instructions = _build_base_instructions(
            self._gesture_processor,
            person_name_prompt=person_name_prompt,
        )
        await self._refresh_runtime_instructions()
        if greet:
            self._initial_greeting_override = serbian_greeting_with_vocative(display_name)
            self._last_greeted_face_key = str(identity.get("face_id") or name)

    async def _apply_spoken_known_identity(self, query: str) -> None:
        known_person = extract_spoken_known_person_identity(query)
        if known_person is None:
            return
        identity = {
            "status": "known",
            "name": known_person.canonical_name,
            "source": "voice_introduction",
        }
        await self._apply_person_identity(identity)
        logger.info(
            "Known person identity bound from voice: spoken=%r canonical=%s",
            query,
            known_person.canonical_name,
        )

    async def _start_face_enrollment(
        self,
        *,
        initial_name: str | None = None,
        consent_already_given: bool = False,
    ) -> dict:
        if self._face_enrollment_running:
            return {"status": "already_running"}
        self._face_enrollment_running = True
        try:
            result = await FaceEnrollmentTask(
                agent=self,
                initial_name=initial_name,
                consent_already_given=consent_already_given,
            )
            if isinstance(result, dict) and result.get("status") == "known":
                await self._apply_person_identity(result)
                self._last_greeted_face_key = str(result.get("face_id") or result.get("name") or "")
            return result if isinstance(result, dict) else {"status": "unknown"}
        except Exception:
            logger.exception("Face enrollment flow failed")
            return {"status": "failed"}
        finally:
            self._face_enrollment_running = False

    async def _monitor_face_identity(self) -> None:
        """Pick up unknown->known transitions produced by the vision retry loop."""

        while True:
            await asyncio.sleep(FACE_IDENTITY_MONITOR_INTERVAL_S)
            if self._face_enrollment_running:
                continue
            try:
                status = await self._vision_api_json("GET", "/api/vision", timeout_s=0.75)
            except VisionApiError as exc:
                logger.debug("Face identity monitor poll failed: %s", exc)
                continue
            if not status.get("person_present"):
                continue
            identity = status.get("identity") or {}
            if identity.get("status") != "known" or not identity.get("name"):
                continue
            face_key = str(identity.get("face_id") or identity.get("name"))
            if not face_key or face_key == self._last_greeted_face_key:
                continue
            if str(getattr(self.session, "agent_state", "")) == "speaking":
                continue
            await self._apply_person_identity(identity)
            self._last_greeted_face_key = face_key
            if self._initial_greeting_completed:
                logger.info(
                    "Face identity updated after initial greeting without a second greeting: %s",
                    identity.get("name"),
                )
                continue
            display_name = str(identity.get("display_name") or identity["name"])
            self.session.generate_reply(
                instructions=_face_identity_greeting_instructions(
                    display_name,
                    str(identity.get("fun_fact") or ""),
                    language_mode=get_stt_language_mode(),
                ),
                allow_interruptions=True,
            )

    @staticmethod
    async def _http_call(url: str, *, method: str = "GET", timeout_s: float) -> tuple[int, str]:
        """Run a blocking urllib request off the event loop.

        Raises urllib.error.HTTPError/URLError like a direct urlopen() call;
        callers keep their own endpoint-specific error handling.
        """

        def _do_request() -> tuple[int, str]:
            data = b"" if method == "POST" else None
            req = urllib.request.Request(url, data=data, method=method)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")

        return await asyncio.to_thread(_do_request)

    async def _call_gesture_api(
        self,
        gesture: str,
        *,
        requested: bool,
        force_gesture: bool = False,
    ) -> None:
        query = urllib.parse.urlencode(
            {
                "requested": "true" if requested else "false",
                "force_gesture": "true" if force_gesture else "false",
            }
        )
        url = f"{GESTURE_API_URL}/gesture/{urllib.parse.quote(gesture)}?{query}"
        try:
            status, body = await self._http_call(url, timeout_s=2.0)
            logger.info("Gesture API response (%s): %s", status, body)
        except Exception as exc:
            logger.warning("Gesture API call failed for %s: %s", gesture, exc)

    async def _call_rag_speaking_motion(
        self,
        estimated_duration_s: float | None = None,
        *,
        resource_name: str = "",
    ) -> None:
        params = {}
        if estimated_duration_s is not None:
            params["estimated_duration_s"] = f"{estimated_duration_s:.3f}"
        if resource_name:
            params["resource_name"] = resource_name
        query = urllib.parse.urlencode(params)
        url = f"{GESTURE_API_URL}/conversation-motion/rag-speaking"
        if query:
            url = f"{url}?{query}"
        try:
            status, body = await self._http_call(
                url, method="POST", timeout_s=LINKCRAFT_SPEAKING_MOTION_TIMEOUT_S
            )
            logger.info("RAG speaking motion response (%s): %s", status, body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 429:
                logger.info(
                    "RAG speaking motion skipped by bridge (%s): %s",
                    exc.code,
                    body,
                )
            else:
                logger.warning(
                    "RAG speaking motion call failed: HTTP %s %s",
                    exc.code,
                    body,
                )
        except Exception as exc:
            # Motion is best-effort and must never delay or fail the answer.
            logger.warning("RAG speaking motion call failed: %s", exc)

    async def _extend_rag_speaking_motion(self, estimated_duration_s: float) -> None:
        query = urllib.parse.urlencode(
            {"estimated_duration_s": f"{estimated_duration_s:.3f}"}
        )
        url = f"{GESTURE_API_URL}/conversation-motion/rag-speaking/extend?{query}"
        try:
            status, body = await self._http_call(
                url, method="POST", timeout_s=LINKCRAFT_SPEAKING_MOTION_TIMEOUT_S
            )
            logger.info("RAG speaking motion extension (%s): %s", status, body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            logger.debug("RAG speaking motion extension skipped: HTTP %s %s", exc.code, body)
        except Exception as exc:
            logger.debug("RAG speaking motion extension failed: %s", exc)

    async def _call_explicit_linkcraft_motion(self, gesture_id: str) -> None:
        # Keep the HTTP surface closed: no LinkCraft key or caller-selected
        # path is ever forwarded. Only the catalog's explicit meme ID maps to
        # the fixed bridge endpoint.
        if gesture_id != "linkcraft_sixseven":
            logger.warning(
                "Blocked unsupported explicit LinkCraft gesture: %s",
                gesture_id,
            )
            return
        url = f"{GESTURE_API_URL}/conversation-motion/sixseven"
        try:
            status, body = await self._http_call(
                url, method="POST", timeout_s=LINKCRAFT_SPEAKING_MOTION_TIMEOUT_S
            )
            logger.info("Sixseven motion response (%s): %s", status, body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 429:
                logger.info("Sixseven motion skipped by bridge (%s): %s", exc.code, body)
            else:
                logger.warning("Sixseven motion call failed: HTTP %s %s", exc.code, body)
        except Exception as exc:
            logger.warning("Sixseven motion call failed: %s", exc)

def prewarm(proc: JobProcess) -> None:
    prewarm_started_at = time.perf_counter()
    logger.info("Agent process prewarm started")

    vad_started_at = time.perf_counter()
    proc.userdata["vad"] = _load_vad()
    logger.info(
        "Agent process prewarm VAD loaded in %.3fs",
        time.perf_counter() - vad_started_at,
    )

    try:
        truebar_started_at = time.perf_counter()
        proc.userdata["truebar_settings"] = _load_truebar_settings()
        logger.info(
            "Agent process prewarm Truebar settings loaded in %.3fs",
            time.perf_counter() - truebar_started_at,
        )
    except Exception as exc:
        logger.warning("Unable to prewarm Truebar settings: %s", exc, exc_info=True)
        proc.userdata["truebar_settings"] = None

    logger.info(
        "Agent process prewarm completed in %.3fs",
        time.perf_counter() - prewarm_started_at,
    )

def _link_audio_bridge(session: AgentSession) -> None:
    target_identity = VOICE_SETTINGS.misc.audio_bridge_identity
    if not target_identity:
        return
    try:
        session.room_io.set_participant(target_identity)
        logger.info("Linked RoomIO to participant identity '%s'", target_identity)
    except Exception as exc:
        logger.warning("Failed to link audio bridge participant '%s': %s", target_identity, exc)


def _prepare_background_audio_player() -> BackgroundAudioPlayer:
    config = get_background_audio_config()
    if not config.get("enabled"):
        logger.info("Thinking background audio disabled")
        return BackgroundAudioPlayer()

    source_type = str(config.get("source_type") or "").strip()
    source_name = str(config.get("source") or "").strip()
    volume = max(0.0, min(1.0, float(config.get("volume", 0.45))))

    if source_type == "builtin":
        local_source = resolve_builtin_background_audio_source(source_name)
        if local_source is not None:
            if not local_source.is_file():
                logger.warning("Built-in background audio file missing '%s'; disabling", local_source)
                return BackgroundAudioPlayer()
            source = str(local_source)
        else:
            try:
                source = BuiltinAudioClip[source_name]
            except KeyError:
                logger.warning("Invalid built-in background audio source '%s'; disabling", source_name)
                return BackgroundAudioPlayer()
    elif source_type == "upload":
        source_path = resolve_uploaded_audio_source(source_name)
        if not source_path.is_file():
            logger.warning("Uploaded background audio file missing '%s'; disabling", source_path)
            return BackgroundAudioPlayer()
        source = str(source_path)
    else:
        logger.warning("Invalid background audio source type '%s'; disabling", source_type)
        return BackgroundAudioPlayer()

    logger.info(
        "Thinking background audio enabled source_type=%s source=%s volume=%.2f",
        source_type,
        source_name,
        volume,
    )
    return BackgroundAudioPlayer(
        thinking_sound=AudioConfig(source=source, volume=volume)
    )


def compute_load(agent_server: AgentServer) -> float:
    return 0.01

def _load_vad():
    return silero.VAD.load(
        min_speech_duration=AGENT_VAD_MIN_SPEECH_DURATION_S,
        min_silence_duration=AGENT_VAD_MIN_SILENCE_DURATION_S,
        prefix_padding_duration=AGENT_VAD_PREFIX_PADDING_DURATION_S,
        activation_threshold=AGENT_VAD_ACTIVATION_THRESHOLD,
    )

def _register_voice_metrics(session: AgentSession) -> None:
    if not VOICE_METRICS_ENABLED:
        logger.info("Voice pipeline metrics disabled")
        return

    def _metrics_collected(ev) -> None:
        metrics = getattr(ev, "metrics", ev)
        try:
            payload = metrics.model_dump(exclude_none=True)
        except Exception:
            payload = {"repr": repr(metrics)}

        payload["event"] = "voice_pipeline_metric"
        for field in (
            "duration",
            "ttft",
            "ttfb",
            "audio_duration",
            "acquire_time",
            "end_of_utterance_delay",
            "transcription_delay",
            "on_user_turn_completed_delay",
            "playback_latency",
            "total_duration",
            "detection_delay",
            "prediction_duration",
        ):
            value = payload.get(field)
            if isinstance(value, (int, float)):
                payload[f"{field}_ms"] = round(value * 1000.0, 3)

        logger.info(
            "VOICE_PIPELINE_METRIC %s",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        )

    session.on("metrics_collected", _metrics_collected)
    logger.info("Registered voice pipeline metrics collector")


async def entrypoint(ctx: JobContext) -> None:
    entrypoint_started_at = time.perf_counter()
    logger.info("ENTRYPOINT START %.6f room=%s", time.time(), ctx.room.name)
    logger.info(
        "VOICE_CANARY_FLAGS GATE_V2=%s RAG_ASYNC=%s LLM_ROUTING=%s "
        "STT_CONN_REUSE=%s ADDRESSEE_LLM_TIEBREAK=%s INTERRUPT_GATE=%s "
        "STT_SEGMENT_FIX=%s RECALL_BIAS=%s ENGAGE_WINDOW_20S=%s "
        "ROOM_SUMMARY=%s",
        GATE_V2,
        RAG_ASYNC,
        LLM_ROUTING,
        STT_CONN_REUSE,
        ADDRESSEE_LLM_TIEBREAK,
        INTERRUPT_GATE,
        STT_SEGMENT_FIX,
        RECALL_BIAS,
        ENGAGE_WINDOW_20S,
        ROOM_SUMMARY,
    )
    logger.info(
        "TURN_HANDLING_CONFIG detection=%s endpointing_mode=%s "
        "min_delay=%.3fs max_delay=%.3fs interruption_mode=%s "
        "preemptive_generation=%s preemptive_tts=%s",
        TURN_HANDLING_OPTIONS["turn_detection"],
        TURN_HANDLING_OPTIONS["endpointing"]["mode"],
        TURN_HANDLING_OPTIONS["endpointing"]["min_delay"],
        TURN_HANDLING_OPTIONS["endpointing"]["max_delay"],
        TURN_HANDLING_OPTIONS["interruption"]["mode"],
        TURN_HANDLING_OPTIONS["preemptive_generation"]["enabled"],
        TURN_HANDLING_OPTIONS["preemptive_generation"]["preemptive_tts"],
    )
    logger.info(
        "REPLY_SHAPE_CONFIG enabled=%s postgen_trim=%s max_tokens=%d "
        "social_reciprocity=%s",
        REPLY_SHAPE_ENABLED,
        POSTGEN_TRIM_ENABLED,
        HOST_REPLY_MAX_TOKENS,
        SOCIAL_RECIPROCITY_ENABLED,
    )
    logger.info(
        "FLOOR_HOLD_CONFIG enabled=%s min_speech_ms=%.0f "
        "min_content_words=%d client_aec=%s echo_text_guard=%s "
        "echo_similarity=%.2f primary_speaker_id_available=%s "
        "primary_speaker_id_configured=%s topic_overlap_min_words=%d",
        INTERRUPT_GATE,
        FLOOR_HOLD_MIN_SPEECH_DURATION_S * 1000.0,
        FLOOR_HOLD_MIN_CONTENT_WORDS,
        CLIENT_AEC_ENABLED,
        not CLIENT_AEC_ENABLED,
        FLOOR_HOLD_ECHO_SIMILARITY,
        PRIMARY_SPEAKER_ID_AVAILABLE,
        bool(PRIMARY_SPEAKER_ID),
        FLOOR_HOLD_TOPIC_OVERLAP_MIN_WORDS,
    )

    ctx.log_context_fields = {"room": ctx.room.name}

    vad = ctx.proc.userdata.get("vad")
    if vad is None:
        vad_started_at = time.perf_counter()
        logger.info("VAD missing from prewarm; loading in entrypoint")
        vad = _load_vad()
        ctx.proc.userdata["vad"] = vad
        logger.info("Entrypoint VAD loaded in %.3fs", time.perf_counter() - vad_started_at)

    session_started_at = time.perf_counter()
    primary_llm = openai.LLM.with_azure(
        model=CHOSEN_COMPLETION_MODEL,
        azure_deployment=AZURE_OPENAI_DEPLOYMENT,
        azure_endpoint=AZURE_OPENAI_BASE,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=OPENAI_API_VERSION,
    )
    fast_deployment = VOICE_SETTINGS.llm.fast_deployment.strip()
    fast_llm = None
    if (
        LLM_ROUTING
        or ADDRESSEE_LLM_TIEBREAK
        or RAG_QUERY_REWRITE
    ) and fast_deployment:
        fast_llm = openai.LLM.with_azure(
            model=VOICE_SETTINGS.llm.fast_model or fast_deployment,
            azure_deployment=fast_deployment,
            azure_endpoint=VOICE_SETTINGS.llm.fast_base or AZURE_OPENAI_BASE,
            api_key=VOICE_SETTINGS.llm.fast_api_key or AZURE_OPENAI_API_KEY,
            api_version=VOICE_SETTINGS.llm.fast_api_version or OPENAI_API_VERSION,
        )
    session = AgentSession(
        llm=primary_llm,
        stt=_prepare_truebar_stt(ctx),
        tts=_prepare_truebar_tts(ctx),
        vad=vad,
        turn_handling=TURN_HANDLING_OPTIONS,
        aec_warmup_duration=AGENT_AEC_WARMUP_DURATION_S,
    )
    _register_voice_metrics(session)
    visual_ui_runtime = AgentVisualUiRuntime(logger=logger)
    visual_ui_runtime.bind(session=session, ctx=ctx)
    visual_ui_prepare_started_at = time.perf_counter()
    visual_ui_ready = await visual_ui_runtime.prepare()
    logger.info(
        "Visual UI runtime prepare completed ready=%s in %.3fs",
        visual_ui_ready,
        time.perf_counter() - visual_ui_prepare_started_at,
    )
    if visual_ui_ready:
        await visual_ui_runtime.set_listening()
    speaking_head_motion_runtime = SpeakingHeadMotionRuntime(logger=logger)
    speaking_head_motion_runtime.bind(session=session, ctx=ctx)
    logger.info(
        "Agent session objects prepared in %.3fs",
        time.perf_counter() - session_started_at,
    )

    background_audio = _prepare_background_audio_player()

    gesture_processor = GestureTokenProcessor(
        bridge_url=GESTURE_API_URL,
        safe_gestures=ALLOWED_GESTURES,
        enabled=GESTURES_ENABLED,
        min_interval_s=GESTURE_TOKEN_RATE_LIMIT_S,
        request_timeout_s=GESTURE_BRIDGE_TIMEOUT_S,
        probe_interval_s=GESTURE_BRIDGE_PROBE_INTERVAL_S,
    )
    await gesture_processor.refresh_availability(force=True)

    start_started_at = time.perf_counter()
    agent = HumanoidAgent(
        visual_ui_runtime=visual_ui_runtime,
        gesture_processor=gesture_processor,
        fast_llm=fast_llm,
        room_summary_llm=primary_llm if ROOM_SUMMARY else None,
    )
    raw_dispatch_metadata = str(getattr(getattr(ctx, "job", None), "metadata", "") or "")
    if raw_dispatch_metadata:
        try:
            dispatch_metadata = json.loads(raw_dispatch_metadata)
        except json.JSONDecodeError:
            dispatch_metadata = {}
        if (
            isinstance(dispatch_metadata, dict)
            and dispatch_metadata.get("suppress_initial_greeting") is True
        ):
            agent._suppress_initial_greeting = True
            logger.info(
                "Initial greeting suppressed by dispatch metadata mode=%s",
                dispatch_metadata.get("mode"),
            )
    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(),
        room_output_options=RoomOutputOptions(transcription_enabled=True),
    )
    logger.info(
        "session.start finished in %.3fs",
        time.perf_counter() - start_started_at,
    )
    agent._schedule_gesture_idle_signal("conversation", active=True)
    ctx.add_shutdown_callback(agent._close_gesture_idle_session)

    await background_audio.start(
        room=ctx.room,
        agent_session=session,
    )

    _link_audio_bridge(session)

    logger.info("Agent session started in %.3fs.", time.perf_counter() - entrypoint_started_at)
    # The block below decided and spoke the initial greeting (generic, or the
    # face-identity override/suppression) right here in entrypoint(), after
    # session.start(). That was buggy: session.start() does not block until
    # on_enter() truly finishes running the face-identity check/enrollment
    # AgentTask flow, so this ran concurrently with on_enter() and produced a
    # double greeting (both the enrollment task's "Zdravo!..." and this
    # generic one) instead of one or the other. The same logic now lives at
    # the end of HumanoidAgent.on_enter(), sequenced correctly after the
    # identity check. --> DELETED TO TRY FACE RECOGNITION
    # if agent._suppress_initial_greeting:
    #     initial_greeting = ""
    # elif agent._initial_greeting_override:
    #     initial_greeting = agent._initial_greeting_override
    # else:
    #     initial_greeting = build_initial_greeting().strip()
    # if initial_greeting:
    #     try:
    #         chat_ctx = agent.chat_ctx.copy()
    #         chat_ctx.add_message(role="assistant", content=initial_greeting)
    #         await agent.update_chat_ctx(chat_ctx)
    #     except Exception as exc:
    #         logger.warning("Failed to seed greeting into chat history: %s", exc)
    #     await session.say(text=initial_greeting, allow_interruptions=True)

print(
    f"agent_main_led.py import completed in {time.perf_counter() - _AGENT_MODULE_BOOT_TS:.3f}s",
    flush=True,
)

if __name__ == "__main__":
    server = AgentServer(
        load_threshold=VOICE_SETTINGS.misc.livekit_worker_load_threshold,
        num_idle_processes=VOICE_SETTINGS.misc.livekit_num_idle_processes,
        load_fnc=compute_load,
    )
    server.setup_fnc = prewarm
    server.rtc_session(
        entrypoint,
        agent_name=os.environ["LIVEKIT_AGENT_NAME"],
        type=ServerType.ROOM,
    )
    cli.run_app(server)
