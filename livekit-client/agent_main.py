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
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
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
    llm,
    StopResponse,
)
from livekit.agents.types import TimedString
from livekit.agents.worker import ServerType
from livekit.agents.llm import ImageContent
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
from livekit_config.truebar_config import (
    load_truebar_settings as _load_truebar_settings,
    prepare_truebar_settings as _prepare_truebar_settings,
    prepare_truebar_stt as _prepare_truebar_stt,
    prepare_truebar_tts as _prepare_truebar_tts,
)
from livekit_config.voice_canary import english_voice_enabled, voice_canary_enabled, voice_locale
from livekit_config.tts_normalizer import normalize_tts_text
from robot_supervisor_v2.app.speech_config import (
    get_background_audio_config,
    normalize_text_for_tts,
    resolve_uploaded_audio_source,
    sanitize_spoken_text,
    tts_normalizer_language,
)
from utils.env_vars import( 
        AZURE_OPENAI_BASE, 
        AZURE_OPENAI_API_KEY, 
        OPENAI_API_VERSION,
        CHOSEN_COMPLETION_MODEL,
        AZURE_OPENAI_DEPLOYMENT,
        env_bool,
        env_int,
    )
from utils.console_safe import configure_utf8_output
from robot_services.gestures import normalize_gesture
from robot_services.gestures import (
    build_available_gesture_text,
    build_gesture_policy_prompt,
    get_allowed_gestures,
    get_configured_gesture_safety_pool,
)
from robot_services.question_answer import get_time
from robot_services.question_answer.qa_intents import DEFAULT_CITY as DEFAULT_TIME_CITY
from robot_services.screen_manip import (
    DEFAULT_MESSAGE_DURATION_S,
    provision_screen,
    show_message_async,
)


# Robot visual UI runtime
from robot.visual_ui_runtime import AgentVisualUiRuntime

# RAG client
from rag import (
    PanelGestureSync,
    PanelTourState,
    RAGServiceClient,
    RagConfig,
    build_rag_developer_context,
    is_backchannel,
    panel_explanation_gesture,
)
from startup_greeting import speak_startup_greeting
from referee_mode import RefereeMode, describe_match, parse_referee_command

# Survey / Quiz flow
from survey_flow import SurveyFlowTask, load_survey_state
from quiz_flow import QuizFlowTask, load_quiz_state

# Face identity (recognize + greet by name, consent-gated enrollment, forget me)
from face_identity_flow import (
    FaceApiError,
    FaceEnrollmentTask,
    SAY_FORGET_DONE,
    SAY_FORGET_FAILED,
    SAY_FORGET_NOT_FOUND,
    SAY_KNOWN_GREETING,
    SAY_UNKNOWN_GREETING,
    build_enrolled_person_instructions,
    build_known_person_instructions,
    fetch_vision_identity,
    forget_face,
)
from voice_metrics import bind_voice_metrics
from self_echo_guard import SpeechEchoWindow, contains_stop_command, looks_like_self_echo


load_dotenv()
configure_utf8_output()
logger = logging.getLogger("basic-agent-truebar")

AGENT_AEC_WARMUP_DURATION_S = float(os.getenv("AGENT_AEC_WARMUP_DURATION_S", "1.0"))
AGENT_MIN_INTERRUPTION_DURATION_S = float(os.getenv("AGENT_MIN_INTERRUPTION_DURATION_S", "0.4"))
# How long on_enter waits for entrypoint to finish wiring audio before greeting.
GREETING_AUDIO_READY_TIMEOUT_S = float(os.getenv("GREETING_AUDIO_READY_TIMEOUT_S", "5.0"))
# Hard ceiling on the face-enrollment sub-conversation so an unresponsive
# visitor cannot leave on_enter awaiting forever.
FACE_ENROLLMENT_MAX_DURATION_S = float(os.getenv("FACE_ENROLLMENT_MAX_DURATION_S", "120.0"))

TURN_HANDLING_OPTIONS: TurnHandlingOptions  = {
    "turn_detection": "vad",
    "endpointing": {
        "mode": "fixed",
        "min_delay": 0.5,
        "max_delay": 3.0,
    },
    "interruption": {
        "enabled": True,
        "discard_audio_if_uninterruptible": True,
        "min_duration": AGENT_MIN_INTERRUPTION_DURATION_S,
        "min_words": 1,
        "resume_false_interruption": True,
        "false_interruption_timeout": 2.0,
    },
    "preemptive_generation": {
        "enabled": False,
    },
}


# Spoken-segment buffering. A segment is flushed to TTS at the first sentence end
# past _MIN, else the first comma/colon past _SOFT, else a word break at _MAX, so a
# pronunciation replacement always sees whole words. Lower _MIN for snappier first
# audio, at the cost of shorter (choppier) synthesis units.
_TTS_SEGMENT_MIN_CHARS = env_int("TTS_SEGMENT_MIN_CHARS", 45)
_TTS_SEGMENT_SOFT_CHARS = env_int("TTS_SEGMENT_SOFT_CHARS", 90)
_TTS_SEGMENT_MAX_CHARS = env_int("TTS_SEGMENT_MAX_CHARS", 220)

_SENTENCE_BOUNDARY_RE = re.compile(r"([.!?…]+[\"')\]]?(?:\s+|$)|\n+)")
_SOFT_BOUNDARY_RE = re.compile(r"([,;:]+[\"')\]]?\s+|\s+[–—-]\s+)")


def _split_spoken_segment(value: str, *, force: bool = False) -> tuple[str, str]:
    """Split off the next segment that is safe to clean, and return the remainder."""
    if not value:
        return "", ""
    if force:
        return value, ""

    sentence_end = None
    for match in _SENTENCE_BOUNDARY_RE.finditer(value):
        if match.end() >= _TTS_SEGMENT_MIN_CHARS:
            sentence_end = match.end()
            break
    soft_end = None
    for match in _SOFT_BOUNDARY_RE.finditer(value):
        if match.end() >= _TTS_SEGMENT_SOFT_CHARS:
            soft_end = match.end()
            break

    ends = [end for end in (sentence_end, soft_end) if end is not None]
    if ends:
        boundary = min(ends)
        return value[:boundary], value[boundary:]

    if len(value) >= _TTS_SEGMENT_MAX_CHARS:
        cut = max(
            value.rfind(" ", 0, _TTS_SEGMENT_MAX_CHARS),
            value.rfind(",", 0, _TTS_SEGMENT_MAX_CHARS),
        )
        if cut < _TTS_SEGMENT_MIN_CHARS:
            cut = _TTS_SEGMENT_MAX_CHARS
        return value[:cut], value[cut:]

    return "", value


def _clean_spoken_segment(segment: str) -> str:
    """Everything applied to a complete segment on its way to TTS, in order."""
    # A blank line is often the only sentence break in a multi-paragraph greeting
    # ("...kompanije Comtrade\n\nDobrodosli na..."). Normalization collapses
    # whitespace, so make the break audible before it is lost.
    segment = re.sub(r"([^.!?\u2026:;,\s])\s*\n\s*\n\s*", r"\1. ", segment)
    cleaned = sanitize_spoken_text(segment)
    if env_bool("TTS_NUMBER_NORMALIZATION", True):
        cleaned = normalize_tts_text(cleaned, language=tts_normalizer_language())
    # Last, so the operator's phonetic spellings are never re-processed. Applies in
    # every language: the list is per-locale operator config, not English respellings.
    if env_bool("TTS_ENGLISH_PRONUNCIATION", True):
        cleaned = normalize_text_for_tts(cleaned, locale=voice_locale())
    return cleaned


def _active_turn_handling_options() -> TurnHandlingOptions:
    if not voice_canary_enabled() or (os.getenv("TURN_DETECTION") or "vad").strip().lower() == "vad":
        return TURN_HANDLING_OPTIONS
    turn_detection = (os.getenv("TURN_DETECTION") or "vad").strip().lower()
    if turn_detection != "stt":
        raise ValueError("TURN_DETECTION must be 'vad' or 'stt'")
    return {
        "turn_detection": "stt",
        "interruption": TURN_HANDLING_OPTIONS["interruption"],
        "preemptive_generation": TURN_HANDLING_OPTIONS["preemptive_generation"],
    }


# Gesture bridge configuration TODO - move to env vars
ACTIVE_GESTURE_SAFETY_POOL = get_configured_gesture_safety_pool()
ACTIVE_GESTURE_CATALOG_ID = os.getenv("GESTURE_CATALOG_ID") or None
logger.info(
    "Active gesture safety pool: '%s' (catalog=%s)",
    ACTIVE_GESTURE_SAFETY_POOL,
    ACTIVE_GESTURE_CATALOG_ID,
)
ALLOWED_GESTURES = get_allowed_gestures(ACTIVE_GESTURE_SAFETY_POOL, ACTIVE_GESTURE_CATALOG_ID)
ALLOWED_GESTURE_SET = frozenset(ALLOWED_GESTURES)
GESTURE_LIST = build_available_gesture_text(ACTIVE_GESTURE_SAFETY_POOL, ACTIVE_GESTURE_CATALOG_ID)
GESTURE_API_URL = os.getenv("GESTURE_API_URL", "http://127.0.0.1:8090").rstrip("/")
AGENT_COMMAND_TOPIC = os.getenv("AGENT_COMMAND_TOPIC", "agent-command")
WRAP_UP_COMMAND = "__WRAP_UP__"
PROMPT_RELOAD_COMMAND = "__PROMPT_RELOAD__"
GOODBYE_TEXT = os.getenv("AGENT_GOODBYE_TEXT", "Hvala za obisk. Lep dan še naprej.")
PRODUCT_DRAW_HOOK = "product_draw_hook"
PRODUCT_HUNT_DEFAULT_PREFIX = "Sta pripravljena? Tri, štiri… Petrol GO! Vajina izdelka sta"
PRODUCT_HUNT_OPTIONS = (
    "Piškoti Na poti",
    "Q drink",
    "Ledena kava Na poti",
    "Voda Petrol",
    "Vitrex limeta",
    "Protein Q bar",
)

# Camera bridge configuration
MAX_CONTEXT_IMAGES = int(os.getenv("MAX_CONTEXT_IMAGES", "2"))

# Head screen configuration: how long a readout stays up before the default
# face comes back. Long enough to read, short enough to stay inside the answer.
HEAD_SCREEN_FLASH_DURATION_S = float(
    os.getenv("HEAD_SCREEN_FLASH_DURATION_S", str(DEFAULT_MESSAGE_DURATION_S))
)
HEAD_SCREEN_ENABLED = env_bool("HEAD_SCREEN_ENABLED", True)

# Agent configuration
BASE_INSTRUCTIONS = build_system_prompt(
    extra_variables={
        "gesture_policy_prompt": build_gesture_policy_prompt(
            ACTIVE_GESTURE_SAFETY_POOL, ACTIVE_GESTURE_CATALOG_ID
        ),
    }
)

# Dataclasses for agent state
@dataclass(frozen=True)
class CommandStep:
    text: str = ""
    gesture: str | None = None
    pause_after_ms: int | None = None
    force_gesture: bool = False

class HumanoidAgent(Agent):

    def __init__(self, chat_ctx: ChatContext | None = None) -> None:

        self._base_instructions = BASE_INSTRUCTIONS
        self._stop_requested: asyncio.Event = asyncio.Event()
        self._tasks = []
        self._latest_image: Optional[ImageContent] = None
        self._latest_image_react = False
        self._image_lock = asyncio.Lock()
        self._handler_registered = False
        self._image_history: Deque[tuple[object, ImageContent]] = deque()
        self._max_context_images = MAX_CONTEXT_IMAGES
        self._command_lock = asyncio.Lock()
        self._rag_config = RagConfig()
        self._rag_client: RAGServiceClient | None = None
        self._rag_next_retry_ts = 0.0
        self._rag_init_lock = asyncio.Lock()
        self._rag_message_id: str | None = None
        self._panel_tour_state = PanelTourState(
            persona=str(get_prompt_builder().get_active().get("persona") or "")
        )
        self._panel_gesture_sync = PanelGestureSync()
        self._rag_turn_lock = asyncio.Lock()
        self._rag_runtime_enabled: bool | None = None  
        self._survey_running = False
        self._quiz_running = False
        self._quiz_default_enabled = env_bool("QUIZ_RUNTIME_ENABLED", False)
        self._survey_default_enabled = env_bool("SURVEY_RUNTIME_ENABLED", False)
        self._quiz_runtime_enabled: bool | None = None
        self._survey_runtime_enabled: bool | None = None
        # Face identity state, resolved once in on_enter().
        self._identity_name: str | None = None
        self._identity_face_id: str | None = None
        self._identity_instructions: str | None = None
        self._greeted = False
        # on_enter() runs concurrently with the tail of entrypoint(), which is
        # where RoomIO gets pointed at the audio-bridge participant. Speaking
        # before that is fine (output is unaffected), but *listening* is not, and
        # the enrollment flow has to hear a name and a yes/no. Wait for the
        # release signal so the sub-conversation can actually hear replies.
        self._audio_ready: asyncio.Event = asyncio.Event()
        self._current_tts_text = ""
        self._current_tts_updated_at = 0.0
        self._speech_echo_window = SpeechEchoWindow()
        # Table tennis referee mode (TitanSudija), switched by the table tennis backend.
        self._referee = RefereeMode()

        super().__init__(
            instructions=self._render_instructions(),
            chat_ctx=chat_ctx,
        )

    def llm_node(self, chat_ctx, tools, model_settings):
        source = super().llm_node(chat_ctx, tools, model_settings)

        async def sanitized_output():
            async for chunk in source:
                if isinstance(chunk, str):
                    yield sanitize_spoken_text(chunk)
                    continue
                if isinstance(chunk, llm.ChatChunk) and chunk.delta and chunk.delta.content:
                    chunk = chunk.model_copy(deep=True)
                    chunk.delta.content = sanitize_spoken_text(chunk.delta.content)
                yield chunk

        return sanitized_output()

    def transcription_node(self, text, model_settings):
        async def sanitized_transcription():
            async for chunk in text:
                sanitized = sanitize_spoken_text(str(chunk))
                if isinstance(chunk, TimedString):
                    yield TimedString(
                        sanitized,
                        start_time=chunk.start_time,
                        end_time=chunk.end_time,
                        confidence=chunk.confidence,
                        start_time_offset=chunk.start_time_offset,
                        speaker_id=chunk.speaker_id,
                    )
                else:
                    yield sanitized

        return super().transcription_node(sanitized_transcription(), model_settings)

    def tts_node(self, text, model_settings):
        self._current_tts_text = ""
        self._current_tts_updated_at = 0.0

        async def prepared_text():
            # The LLM streams partial words, so cleaning each raw chunk would let a
            # replacement straddle a chunk boundary and never match ("Com" + "trade").
            # Accumulate to a clause boundary first, then clean the whole segment.
            carry = ""
            async for chunk in text:
                carry += chunk
                while True:
                    emit, carry = _split_spoken_segment(carry)
                    if not emit:
                        break
                    cleaned = _clean_spoken_segment(emit)
                    if cleaned:
                        self._current_tts_text = (
                            self._current_tts_text + " " + cleaned
                        )[-1200:]
                        self._current_tts_updated_at = time.monotonic()
                        yield cleaned + (" " if emit.endswith((" ", "\n")) else "")
            if carry:
                emit, _ = _split_spoken_segment(carry, force=True)
                cleaned = _clean_spoken_segment(emit)
                if cleaned:
                    self._current_tts_text = (
                        self._current_tts_text + " " + cleaned
                    )[-1200:]
                    self._current_tts_updated_at = time.monotonic()
                    yield cleaned

        return super().tts_node(prepared_text(), model_settings)

    def _track_task(self, task: asyncio.Task) -> asyncio.Task:
        self._tasks.append(task)
        task.add_done_callback(self._discard_task)
        return task

    def _discard_task(self, task: asyncio.Task) -> None:
        try:
            self._tasks.remove(task)
        except ValueError:
            pass

    def bind_panel_gesture_sync(self, session: AgentSession) -> None:
        self._speech_echo_window.bind(session)
        self._panel_gesture_sync.bind(
            session,
            lambda gesture: self._schedule_gesture_api_call(
                gesture,
                requested=False,
                force_gesture=False,
            ),
        )


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
                    timeout_s=cfg.timeout_s,
                    top_k=cfg.top_k,
                    include_scores=cfg.include_scores,
                )
                if cfg.healthcheck_on_init:
                    await asyncio.to_thread(client.healthcheck)

                self._rag_client = client
                return self._rag_client
            except Exception as exc:
                self._rag_client = None
                self._rag_next_retry_ts = now + cfg.retry_seconds
                logger.warning("RAG client init failed: %s (retry in %ss)", exc, cfg.retry_seconds)
                return None

    
    async def on_enter(self):
        """Called when agent joins the room.

        Registers the byte stream handlers, then decides and speaks the very
        first thing of the conversation. The greeting lives here rather than in
        entrypoint() on purpose: session.start() does not block on on_enter()
        finishing, so anything that must be said *first* has to be sequenced
        inside on_enter itself - especially now that on_enter may run a
        multi-turn enrollment sub-conversation.
        """
        if not self._handler_registered:
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

            try:
                get_job_context().room.register_byte_stream_handler("images", _image_received_handler)
                get_job_context().room.register_byte_stream_handler(AGENT_COMMAND_TOPIC, _command_received_handler)
                self._handler_registered = True
                logger.info(
                    "Registered byte stream handlers for topics 'images' and '%s'",
                    AGENT_COMMAND_TOPIC,
                )
            except Exception as exc:
                logger.error("Failed to register byte stream handler: %s", exc)

        if self._greeted:
            return
        self._greeted = True

        # Bounded wait: if entrypoint never signals (e.g. audio bridge disabled),
        # still greet rather than sitting silent forever.
        try:
            await asyncio.wait_for(
                self._audio_ready.wait(),
                timeout=GREETING_AUDIO_READY_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Audio wiring was not signalled within %.1fs; greeting anyway",
                GREETING_AUDIO_READY_TIMEOUT_S,
            )

        await self._greet_and_resolve_identity()

    def release_greeting(self) -> None:
        """Signal from entrypoint that audio input/output wiring is complete."""
        self._audio_ready.set()

    async def _greet_and_resolve_identity(self) -> None:
        """Greet by name if recognized, otherwise greet and maybe enroll.

        Identity was already resolved synchronously by the vision service at the
        moment the person was locked, and rides along on the person_detected
        event; here we just read the result off the supervisor.
        """
        snapshot = None
        try:
            snapshot = await fetch_vision_identity()
        except Exception:
            logger.exception("Face identity lookup failed; falling back to the standard greeting")

        default_greeting = build_initial_greeting().strip()

        if snapshot is not None and snapshot.should_greet_by_name:
            self._identity_name = snapshot.name
            self._identity_face_id = snapshot.face_id
            self._identity_instructions = build_known_person_instructions(
                snapshot.name, snapshot.canonical_name, snapshot.aliases
            )
            # Refresh instructions BEFORE speaking so the LLM has the name in
            # context for every subsequent turn of this conversation.
            await self._refresh_runtime_instructions()
            known_greeting = SAY_KNOWN_GREETING.format(name=snapshot.name)
            logger.info(
                "Greeting recognized visitor name=%r face_id=%s detection_age_s=%s text=%r",
                snapshot.name,
                snapshot.face_id,
                f"{snapshot.age_s:.1f}" if snapshot.age_s is not None else "n/a",
                known_greeting,
            )
            say_started_at = time.perf_counter()
            await speak_startup_greeting(
                self.session,
                known_greeting,
                capture_gate_enabled=env_bool("STARTUP_GREETING_CAPTURE_GATE", False),
                tail_ms=max(0, int(os.getenv("STARTUP_GREETING_CAPTURE_TAIL_MS", "120"))),
            )
            logger.info(
                "Name greeting finished after %.2fs", time.perf_counter() - say_started_at
            )
            return

        will_enroll = snapshot is not None and snapshot.should_enroll

        # On the enrollment path use the shorter lead-in: the standard greeting
        # ends by asking how it can help, which reads badly right before the
        # enrollment flow asks for a name.
        greeting = SAY_UNKNOWN_GREETING if will_enroll else default_greeting
        if greeting:
            # Log around the greeting explicitly. session.say() blocks until
            # playout finishes, so without these two lines a slow or
            # interrupted greeting is indistinguishable from one that never
            # happened - which is exactly the ambiguity this removes.
            logger.info("Speaking greeting (%s): %r", "enroll lead-in" if will_enroll else "standard", greeting)
            say_started_at = time.perf_counter()
            capture_gate_enabled = env_bool("STARTUP_GREETING_CAPTURE_GATE", False)
            await speak_startup_greeting(
                self.session,
                greeting,
                capture_gate_enabled=capture_gate_enabled,
                tail_ms=max(0, int(os.getenv("STARTUP_GREETING_CAPTURE_TAIL_MS", "120"))),
            )
            logger.info(
                "Greeting finished after %.2fs (startup capture gate=%s)",
                time.perf_counter() - say_started_at,
                capture_gate_enabled,
            )
        else:
            logger.warning("No greeting text configured; saying nothing on entry")

        if not will_enroll:
            if snapshot is not None:
                logger.info(
                    "Skipping face enrollment: feature=%s active=%s present=%s status=%s fresh=%s",
                    snapshot.feature_enabled,
                    snapshot.vision_active,
                    snapshot.person_present,
                    snapshot.status,
                    snapshot.is_fresh,
                )
            return

        await self._run_face_enrollment()

    async def _run_face_enrollment(self) -> None:
        """Run the consent-gated enrollment sub-conversation.

        Awaited directly from on_enter (or from a tool's own call stack), and
        never from a detached asyncio task. AgentTask.__await_impl checks
        `_get_activity_task_info(asyncio.current_task()).inline_task` and raises
        RuntimeError("...should only be awaited inside tool_functions or the
        on_enter/on_exit methods of an Agent") otherwise. on_enter is registered
        with inline_task=True, and plain `await` calls stay on that same task, so
        routing through these helper coroutines is safe - wrapping any of it in
        create_task() would not be. Note that the RuntimeError surfaces only in
        the log (it is caught below), so this failure mode is quiet: check for
        that message rather than assuming success from the absence of a crash.
        """
        task = FaceEnrollmentTask()

        # Watchdog: if the visitor never answers, AgentTask.cancel() resolves the
        # task with a ToolError instead of leaving on_enter awaiting forever.
        # cancel() is the library's own abort path; wrapping the await in
        # wait_for would instead trip its "asyncio.Task finished before
        # AgentTask completed" error path.
        async def _watchdog() -> None:
            try:
                await asyncio.sleep(FACE_ENROLLMENT_MAX_DURATION_S)
            except asyncio.CancelledError:
                return
            if not task.done():
                logger.warning(
                    "Face enrollment exceeded %.0fs with no resolution; cancelling",
                    FACE_ENROLLMENT_MAX_DURATION_S,
                )
                task.cancel()

        watchdog = asyncio.create_task(_watchdog())
        try:
            result = await task
        except Exception:
            logger.exception("Face enrollment task did not complete")
            return
        finally:
            watchdog.cancel()

        logger.info(
            "Face enrollment finished outcome=%s name=%r face_id=%s distances=%s detail=%s",
            result.outcome,
            result.name,
            result.face_id,
            result.captured_distances,
            result.detail,
        )
        if result.outcome == "enrolled" and result.name:
            self._identity_name = result.name
            self._identity_face_id = result.face_id
            self._identity_instructions = build_enrolled_person_instructions(result.name)
            await self._refresh_runtime_instructions()


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
            react_to_visuals = str(
                getattr(getattr(reader, "info", None), "attributes", {}).get(
                    "react_to_visuals", "false"
                )
            ).lower() in {"1", "true", "yes", "on"}
            async with self._image_lock:
                self._latest_image = image_content
                self._latest_image_react = react_to_visuals
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
            referee = (
                parse_referee_command(steps[0].text)
                if len(steps) == 1 and not steps[0].gesture
                else None
            )
            if referee is not None:
                self._referee.set(*referee)
                await self._refresh_runtime_instructions()
                return

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

            if self._is_prompt_reload_command(steps):
                self._base_instructions = build_system_prompt(
                    extra_variables={
                        "gesture_policy_prompt": build_gesture_policy_prompt(
                            ACTIVE_GESTURE_SAFETY_POOL, ACTIVE_GESTURE_CATALOG_ID
                        ),
                    }
                )
                self._panel_tour_state.set_persona(
                    str(get_prompt_builder().get_active().get("persona") or "")
                )
                await self._refresh_runtime_instructions()
                logger.info("Prompt instructions reloaded at runtime by %s", participant_identity)
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
        try:
            goodbye_text = build_goodbye_text().strip()
        except Exception as exc:
            logger.warning("Failed to build prompt-backed goodbye text: %s", exc)
            goodbye_text = ""
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
    def _parse_rag_toggle_command(steps: list[CommandStep]) -> bool | None:
        if len(steps) != 1 or steps[0].gesture:
            return None
        cmd = steps[0].text.strip().upper()
        if cmd == "__RAG_ON__":
            return True
        if cmd == "__RAG_OFF__":
            return False
        return None

    @staticmethod
    def _parse_quiz_toggle_command(steps: list[CommandStep]) -> bool | None:
        if len(steps) != 1 or steps[0].gesture:
            return None
        cmd = steps[0].text.strip().upper()
        if cmd == "__QUIZ_ON__":
            return True
        if cmd == "__QUIZ_OFF__":
            return False
        return None

    def _is_quiz_enabled(self) -> bool:
        if self._quiz_runtime_enabled is None:
            return self._quiz_default_enabled
        return self._quiz_runtime_enabled

    def _is_survey_enabled(self) -> bool:
        if self._survey_runtime_enabled is None:
            return self._survey_default_enabled
        return self._survey_runtime_enabled

    @staticmethod
    def _is_prompt_reload_command(steps: list[CommandStep]) -> bool:
        return (
            len(steps) == 1
            and not steps[0].gesture
            and steps[0].text.strip().upper() == PROMPT_RELOAD_COMMAND
        )

    async def _refresh_runtime_instructions(self) -> None:
        try:
            await self.update_instructions(self._render_instructions())
        except Exception as exc:
            logger.warning("Failed to refresh runtime instructions: %s", exc)

    @staticmethod
    def _parse_survey_toggle_command(steps: list[CommandStep]) -> bool | None:
        if len(steps) != 1 or steps[0].gesture:
            return None
        cmd = steps[0].text.strip().upper()
        if cmd == "__SURVEY_ON__":
            return True
        if cmd == "__SURVEY_OFF__":
            return False
        return None

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
        """Handle user turn completion: attach cached images and detect gesture requests."""
        query = (getattr(new_message, "text_content", "") or "").strip()
        echo_memory_seconds = float(os.getenv("STT_SELF_ECHO_MEMORY_SECONDS", "8.0"))
        echo_tail_seconds = float(os.getenv("STT_SELF_ECHO_TAIL_SECONDS", "1.5"))
        echo_similarity = float(os.getenv("STT_SELF_ECHO_SIMILARITY", "0.82"))
        recent_tts = self._speech_echo_window.active(tail_seconds=echo_tail_seconds) or (
            bool(self._current_tts_text)
            and self._current_tts_updated_at > 0.0
            and time.monotonic() - self._current_tts_updated_at <= echo_memory_seconds
        )
        if (
            env_bool("STT_SEGMENT_FIX", False)
            and recent_tts
            and not contains_stop_command(query)
            and looks_like_self_echo(
                query,
                self._current_tts_text,
                similarity_threshold=echo_similarity,
            )
        ):
            logger.info(
                "SELF_ECHO_FILTER action=drop heard_chars=%d tts_chars=%d",
                len(query),
                len(self._current_tts_text),
            )
            raise StopResponse()

        if self._referee.active and await asyncio.to_thread(self._referee.should_stay_silent):
            # A rally is being played: the referee does not talk over the game.
            logger.info("REFEREE_MODE silent during rally, dropping turn: %r", query[:80])
            raise StopResponse()

        cached_image: Optional[ImageContent] = None
        react_to_visuals = False
        async with self._image_lock:
            if self._latest_image:
                cached_image = self._latest_image
                react_to_visuals = self._latest_image_react
                self._latest_image = None
                self._latest_image_react = False

        if cached_image:
            if self._max_context_images <= 0:
                logger.debug("Dropping cached image because attachments are disabled.")
            else:
                new_message.content.append(cached_image)
                if react_to_visuals:
                    new_message.content.append(
                        "If the image clearly suggests a safe, relevant social gesture, you may use "
                        "the gesture tool once. Ignore ambiguous visuals and prioritize the user's request."
                    )
                self._remember_image_attachment(new_message, cached_image)
                logger.debug("Attached cached image to user turn.")
        
        await self._run_rag(turn_ctx, new_message)

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
        async with self._rag_turn_lock:
            await self._run_rag_serialized(turn_ctx, new_message)

    async def _run_rag_serialized(self, turn_ctx: ChatContext, new_message) -> None:
        self._panel_gesture_sync.clear()
        
        if not self._is_rag_enabled():
            self._remove_previous_rag_message(turn_ctx)
            logger.info("External RAG disabled (runtime toggle or config).")
            return

        query = (getattr(new_message, "text_content", "") or "").strip()
        if not query:
            return

        # Acknowledgements carry no question: retrieving for them costs a full embed
        # plus vector search and can only leave stale context in the next answer.
        if env_bool("RAG_SKIP_BACKCHANNELS", True) and is_backchannel(query):
            self._remove_previous_rag_message(turn_ctx)
            logger.info("RAG skipped for a backchannel turn: %r", query)
            return

        rag_start = time.perf_counter()
        rag_context = ""
        search_query, reuse_last = self._panel_tour_state.resolve(query)
        if not search_query:
            self._remove_previous_rag_message(turn_ctx)
            logger.info("Hall of Fame RAG skipped for a control/filler turn.")
            return

        if reuse_last:
            rag_context = self._panel_tour_state.last_context
        else:
            kb_index = await self._ensure_rag_client()
            if kb_index is None:
                logger.warning("External RAG client not available.")
            else:
                try:
                    rag_context = await asyncio.to_thread(kb_index.search_wrapper, search_query)
                except Exception as exc:
                    logger.warning("External RAG search failed: %s", exc)
                    rag_context = ""

        if self._panel_tour_state.remember_context(rag_context):
            panel_gesture = panel_explanation_gesture(rag_context)
            self._panel_gesture_sync.queue(panel_gesture)
            logger.info(
                "Hall of Fame panel state confirmed: panel=%d pending_gesture=%s",
                self._panel_tour_state.current_sequence,
                panel_gesture,
            )

        rag_ms = (time.perf_counter() - rag_start) * 1000.0

        self._remove_previous_rag_message(turn_ctx)
        self._rag_message_id = self._add_rag_message(
            turn_ctx=turn_ctx,
            new_message=new_message,
            rag_context=rag_context,
            rag_ms=rag_ms,
        )

        logger.info(
            "External RAG: search_ms=%.2f context_chars=%d msg_id=%s",
            rag_ms,
            len(rag_context or ""),
            getattr(new_message, "id", None),
        )

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

    def _add_rag_message(
        self,
        *,
        turn_ctx: ChatContext,
        new_message,
        rag_context: str,
        rag_ms: float,
    ) -> str | None:
        created_at = getattr(new_message, "created_at", None)
        if not isinstance(created_at, (int, float)):
            created_at = time.time()
        created_at = created_at - 0.001

        content = build_rag_developer_context(rag_context)

        message = turn_ctx.add_message(
            role="developer",
            content=[content],
            created_at=created_at,
            extra={
                "rag_search_ms": rag_ms,
            },
        )
        return getattr(message, "id", None)

        
    
    @function_tool(
        name="trigger_gesture",
        description=(
            "Trigger a specific gesture via the gesture API. "
            "Always use this tool when the visitor explicitly asks the robot to perform "
            "any gesture in the active catalog; a physical gesture request takes priority "
            "over retrieved Hall of Fame content. "
            f"Available gestures: {GESTURE_LIST}"
        ),
    )
    async def trigger_gesture(
        self,
        gesture: str,
        requested: bool = False,
    ):
        """Dispatch a gesture to the external gesture API."""
        if self._referee.active:
            # During a match the referee gestures belong to the table tennis robot adapter.
            return "Gestovi su isključeni dok traje meč."
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

        # A direct physical command supersedes the automatic panel narration
        # gesture that may have been queued while retrieval was being prepared.
        self._panel_gesture_sync.clear()
        logger.info("Dispatching gesture via API: %s", normalized_gesture)
        self._schedule_gesture_api_call(
            normalized_gesture,
            requested=requested,
            force_gesture=False,
        )
        return f"Gesture '{normalized_gesture}' triggered."

    @function_tool(
        name="get_table_tennis_match",
        description=(
            "Read the live table tennis match you are refereeing: players, score, who serves, "
            "who leads, winner. Call it before saying anything about the score. Read-only."
        ),
    )
    async def get_table_tennis_match(self):
        """Current match facts from the table tennis backend (never invent a score)."""
        snap = await asyncio.to_thread(self._referee.current_match, 0.0)
        return describe_match(snap)

    @function_tool(
        name="get_current_time",
        description=(
            "Get the current time for a city. Call this whenever the visitor asks "
            "what time it is, in any phrasing and in any language, instead of "
            "guessing the time yourself. Pass the city they named; omit it to "
            "default to Belgrade. The time is also flashed on the robot's head "
            "screen automatically, so do not describe the screen."
        ),
    )
    async def get_current_time(self, city: str = DEFAULT_TIME_CITY) -> str:
        """Answer a time question from the deterministic time service."""

        requested_city = (city or DEFAULT_TIME_CITY).strip() or DEFAULT_TIME_CITY
        # tell_time keys its lookup off the tz database, where multi-word city
        # names are underscored ("New York" -> "New_York").
        resolved_city = requested_city.title().replace(" ", "_")
        result = get_time(resolved_city)

        unknown_city = False
        if result is None:
            unknown_city = True
            resolved_city = DEFAULT_TIME_CITY
            result = get_time(DEFAULT_TIME_CITY)
            if result is None:
                logger.warning("TIME_TOOL time service unavailable city=%r", requested_city)
                return "The time service is unavailable right now. Say so briefly."

        hour, minute, written = result
        spoken_city = resolved_city.replace("_", " ")
        logger.info(
            "TIME_TOOL requested=%r resolved=%r time=%s unknown_city=%s",
            requested_city,
            spoken_city,
            written,
            unknown_city,
        )

        # Flashed in the background so the spoken answer is not held up by the
        # render; the clip lands on screen while the sentence is still playing.
        self._track_task(
            asyncio.create_task(self._flash_head_screen(written, spoken_city))
        )

        speech_rule = (
            "Say the hour and the minute as two separate spoken numbers, never "
            "as HH:MM."
        )
        if unknown_city:
            return (
                f"There is no known city called '{requested_city}'. The time in "
                f"{spoken_city} is hour {hour}, minute {minute}. Tell the visitor "
                f"their city was not recognized and give the {spoken_city} time "
                f"instead. {speech_rule}"
            )
        return (
            f"The current time in {spoken_city} is hour {hour}, minute {minute}. "
            f"{speech_rule}"
        )

    async def _flash_head_screen(self, primary: str, secondary: str = "") -> None:
        """Show a short readout on the head screen, then restore the face."""

        if not HEAD_SCREEN_ENABLED:
            return

        try:
            result = await show_message_async(
                primary, secondary, duration_s=HEAD_SCREEN_FLASH_DURATION_S
            )
        except Exception:
            logger.warning(
                "HEAD_SCREEN flash failed primary=%r", primary, exc_info=True
            )
            return
        logger.info(
            "HEAD_SCREEN primary=%r secondary=%r status=%s reason=%s",
            primary,
            secondary,
            result.get("status"),
            result.get("reason"),
        )

    @function_tool(
        name="start_survey",
        description=(
            "Start the spoken survey only when the user explicitly asks to begin the survey or anketa. "
            "This pauses normal conversation, runs the full survey flow, and then returns to normal conversation."
        ),
    )
    async def start_survey(self) -> str:
        """Start the survey flow when the user explicitly asks for it."""
        if not self._is_survey_enabled():
            return (
                'Survey is currently unavailable. Reply with: '
                '"Anketa trenutno nije dostupna." Do not start the survey and do not add extra suggestions.'
            )

        if self._survey_running:
            return "A survey is already in progress. Do not start another one."

        self._survey_running = True
        try:
            await SurveyFlowTask(survey_state=load_survey_state())
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
            "This pauses normal conversation, runs the full quiz flow, and then returns to normal conversation."
        ),
    )
    async def start_quiz(self) -> str:
        """Start the quiz flow when the user explicitly asks for it."""
        if not self._is_quiz_enabled():
            return (
                'Quiz is currently unavailable. Reply with: '
                '"Kviz trenutno nije dostupan." Do not start the quiz and do not add extra suggestions.'
            )

        if self._quiz_running:
            return "A quiz is already in progress. Do not start another one."

        self._quiz_running = True
        try:
            await QuizFlowTask(quiz_state=load_quiz_state())
        finally:
            self._quiz_running = False

        return (
            "The quiz flow has finished. Resume normal conversation. "
            "Do not repeat the quiz closing message unless the user asks."
        )

    @function_tool(
        name="forget_me",
        description=(
            "Delete the stored face data for the person you are talking to. Call this when they "
            "ask you to forget them, forget their face, or delete their data."
        ),
    )
    async def forget_me(self) -> str:
        """Delete this visitor's stored face on request."""
        # Prefer the specific face_id: names are not unique in the face store, so
        # deleting by name could remove a different person who shares the name.
        face_id = self._identity_face_id
        name = self._identity_name

        if not face_id and not name:
            return (
                "I do not have stored face data for this person, so there is nothing to delete. "
                "Tell them that in one short sentence."
            )

        try:
            status = await forget_face(face_id=face_id, name=None if face_id else name)
        except FaceApiError as exc:
            logger.warning("Face forget failed for face_id=%s name=%r: %s", face_id, name, exc)
            await self._say_with_retry(self.session, SAY_FORGET_FAILED)
            return "Face deletion failed. Do not claim the data was deleted."

        logger.info("Face forget completed status=%s face_id=%s name=%r", status, face_id, name)

        if status == "deleted":
            self._identity_name = None
            self._identity_face_id = None
            self._identity_instructions = None
            await self._refresh_runtime_instructions()
            await self._say_with_retry(self.session, SAY_FORGET_DONE)
            return (
                "The stored face was deleted. Do not use their name anymore, and do not offer to "
                "remember them again unless they ask."
            )

        if status == "not_found":
            await self._say_with_retry(self.session, SAY_FORGET_NOT_FOUND)
            return "There was no stored face to delete. Continue the conversation normally."

        await self._say_with_retry(self.session, SAY_FORGET_FAILED)
        return "Face deletion failed. Do not claim the data was deleted."

    @function_tool(
        name="remember_my_face",
        description=(
            "Start the face-enrollment flow when the visitor asks you to remember them or their "
            "face, and you have not already stored it in this conversation."
        ),
    )
    async def remember_my_face(self) -> str:
        """Run enrollment on explicit request (e.g. after an earlier decline)."""
        if self._identity_name and self._identity_face_id:
            return (
                f"You already have stored face data for {self._identity_name}. "
                "Tell them they are already remembered, in one short sentence."
            )

        snapshot = None
        try:
            snapshot = await fetch_vision_identity()
        except Exception:
            logger.exception("Face identity lookup failed during remember_my_face")

        if snapshot is None or not snapshot.feature_enabled:
            return (
                "Face memory is currently unavailable. Say so in one short sentence and do not "
                "promise to remember them."
            )

        # Awaited inside a tool call - one of the two contexts where LiveKit
        # permits an AgentTask to run.
        await self._run_face_enrollment()
        return (
            "The face enrollment flow has finished. Resume normal conversation and do not repeat "
            "its closing message."
        )

    def _render_instructions(self) -> str:
        quiz_enabled = self._is_quiz_enabled()
        survey_enabled = self._is_survey_enabled()
        flow_rules = [
            "Flow routing rules:",
            f"- Quiz runtime is currently {'enabled' if quiz_enabled else 'disabled'}.",
            f"- Survey runtime is currently {'enabled' if survey_enabled else 'disabled'}.",
        ]

        if quiz_enabled:
            flow_rules.append("- If the user explicitly asks to start a quiz or kviz, call `start_quiz`.")
        else:
            flow_rules.append("- Do not call `start_quiz` while quiz runtime is disabled.")
            flow_rules.append('- If the user asks to start a quiz or kviz while disabled, reply exactly: "Kviz trenutno nije dostupan."')

        if survey_enabled:
            flow_rules.append("- If the user explicitly asks to start a survey or anketa, call `start_survey`.")
        else:
            flow_rules.append("- Do not call `start_survey` while survey runtime is disabled.")
            flow_rules.append('- If the user asks to start a survey or anketa while disabled, reply exactly: "Anketa trenutno nije dostupna."')

        flow_rules.extend(
            [
                "- Do not continue normal conversation when an enabled quiz or survey should be started.",
                "- If the user asks about quizzes in general but does not ask to begin, answer briefly without starting one.",
                "- If the user asks about surveys in general but does not ask to begin, answer briefly without starting one.",
                "- Keep disabled quiz or survey replies to one short sentence with no apology and no extra offer to continue chatting.",
                "- If the user asks you to forget them, forget their face, or delete their data, call `forget_me`.",
                "- If the user asks you to remember them or their face, call `remember_my_face`.",
                "- Never claim to remember or to have deleted a face unless the matching tool confirmed it.",
            ]
        )

        sections = [self._base_instructions, "\n".join(flow_rules)]
        if self._identity_instructions:
            sections.append(self._identity_instructions)
        referee = getattr(self, "_referee", None)
        if referee is not None and referee.active:
            sections.append(referee.instructions(
                str(get_prompt_builder().default_variables.get("robot_name") or "Titan")
            ))

        return "\n\n".join(sections) + "\n"

    async def _say_with_retry(self, session: AgentSession, text: str) -> None:
        last_exc: Exception | None = None
        for delay in (0.0, 0.05, 0.2, 0.5):
            if delay:
                await asyncio.sleep(delay)
            try:
                await session.say(
                    text=text,
                    allow_interruptions=False,
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

        def _do_request() -> tuple[int, str]:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                body = resp.read().decode("utf-8", "replace")
                return resp.status, body

        try:
            status, body = await asyncio.to_thread(_do_request)
            logger.info("Gesture API response (%s): %s", status, body)
        except Exception as exc:
            logger.warning("Gesture API call failed for %s: %s", gesture, exc)

def prewarm(proc: JobProcess) -> None:
    prewarm_started_at = time.perf_counter()
    logger.info("Agent process prewarm started")

    vad_started_at = time.perf_counter()
    proc.userdata["vad"] = silero.VAD.load()
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
    target_identity = os.getenv("AUDIO_BRIDGE_IDENTITY", "audio-streamer")
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
        return BackgroundAudioPlayer()

    source_type = str(config.get("source_type") or "").strip()
    source_name = str(config.get("source") or "").strip()
    volume = max(0.0, min(1.0, float(config.get("volume", 0.45))))

    if source_type == "builtin":
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

    return BackgroundAudioPlayer(
        thinking_sound=AudioConfig(source=source, volume=volume)
    )


def compute_load(agent_server: AgentServer) -> float:
    return 0.01


async def entrypoint(ctx: JobContext) -> None:
    entrypoint_started_at = time.perf_counter()
    logger.info("ENTRYPOINT START %.6f room=%s", time.time(), ctx.room.name)

    ctx.log_context_fields = {"room": ctx.room.name}

    vad = ctx.proc.userdata.get("vad")
    if vad is None:
        vad_started_at = time.perf_counter()
        logger.info("VAD missing from prewarm; loading in entrypoint")
        vad = silero.VAD.load()
        ctx.proc.userdata["vad"] = vad
        logger.info("Entrypoint VAD loaded in %.3fs", time.perf_counter() - vad_started_at)

    session_started_at = time.perf_counter()
    session = AgentSession(
        llm=openai.LLM.with_azure(
            model=CHOSEN_COMPLETION_MODEL,
            azure_deployment=AZURE_OPENAI_DEPLOYMENT,
            azure_endpoint=AZURE_OPENAI_BASE,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=OPENAI_API_VERSION,
        ),
        stt=_prepare_truebar_stt(ctx),
        tts=_prepare_truebar_tts(ctx),
        vad=vad,
        turn_handling=_active_turn_handling_options(),
        aec_warmup_duration=AGENT_AEC_WARMUP_DURATION_S,
    )
    metrics_collector = bind_voice_metrics(
        session,
        enabled=voice_canary_enabled() and env_bool("VOICE_METRICS_ENABLED", False),
    )
    visual_ui_runtime = AgentVisualUiRuntime(logger=logger)
    visual_ui_runtime.bind(session=session, ctx=ctx)
    visual_ui_prepare_started_at = time.perf_counter()
    visual_ui_ready = await visual_ui_runtime.prepare()
    logger.info(
        "Visual UI runtime prepare completed ready=%s in %.3fs",
        visual_ui_ready,
        time.perf_counter() - visual_ui_prepare_started_at,
    )
    if HEAD_SCREEN_ENABLED:
        # Registers the reusable emoticon slot and opens the multiplexed ssh
        # connection to the face host, so the first on-screen readout of the
        # session is as fast as every later one. Off the loop: it does blocking
        # IO and must never delay session start.
        async def _prepare_head_screen() -> None:
            started_at = time.perf_counter()
            slot = await asyncio.to_thread(provision_screen)
            logger.info(
                "Head screen prepare completed slot=%s in %.3fs",
                slot,
                time.perf_counter() - started_at,
            )

        asyncio.create_task(_prepare_head_screen())
    logger.info(
        "Agent session objects prepared in %.3fs",
        time.perf_counter() - session_started_at,
    )

    background_audio = _prepare_background_audio_player()

    agent = HumanoidAgent()
    agent.bind_panel_gesture_sync(session)

    start_started_at = time.perf_counter()
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

    await background_audio.start(
        room=ctx.room,
        agent_session=session,
    )

    _link_audio_bridge(session)

    logger.info("Agent session started in %.3fs.", time.perf_counter() - entrypoint_started_at)
    # The greeting is intentionally NOT spoken here. session.start() spawns
    # on_enter() as its own task and does not block on it, so anything that must
    # be the first utterance has to live inside on_enter - which it now does,
    # sequenced after the face-identity lookup. Speaking here would race with it
    # and could talk over the greet-by-name. Releasing the gate below lets
    # on_enter greet now that RoomIO is pointed at the audio bridge, so the
    # enrollment sub-conversation can actually hear the visitor's replies.
    agent.release_greeting()

print(
    f"agent_main_led.py import completed in {time.perf_counter() - _AGENT_MODULE_BOOT_TS:.3f}s",
    flush=True,
)

if __name__ == "__main__":
    server = AgentServer(
        load_threshold=float(os.getenv("LIVEKIT_WORKER_LOAD_THRESHOLD", "1.1")),
        num_idle_processes=int(os.getenv("LIVEKIT_NUM_IDLE_PROCESSES", "1")),
        load_fnc=compute_load,
    )
    server.setup_fnc = prewarm
    server.rtc_session(
        entrypoint,
        agent_name=os.environ["LIVEKIT_AGENT_NAME"],
        type=ServerType.ROOM,
    )
    cli.run_app(server)
