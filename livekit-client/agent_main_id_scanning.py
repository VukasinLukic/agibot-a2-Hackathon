import time

_AGENT_MODULE_BOOT_TS = time.perf_counter()
print("agent_main.py import started", flush=True)

# standard library imports
import asyncio
import base64
import json
import logging
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Optional

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
    RunContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    get_job_context,
    BackgroundAudioPlayer,
    AudioConfig,
    BuiltinAudioClip
)


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
from robot_supervisor_v2.app.speech_config import (
    get_background_audio_config,
    resolve_uploaded_audio_source,
)
from utils.env_vars import( 
        AZURE_OPENAI_BASE, 
        AZURE_OPENAI_API_KEY, 
        OPENAI_API_VERSION,
        CHOSEN_COMPLETION_MODEL,
        AZURE_OPENAI_DEPLOYMENT,
        env_bool,
    )
from utils.console_safe import configure_utf8_output
from robot_services.gestures import normalize_gesture
from robot_services.gestures import (
    build_available_gesture_text,
    build_gesture_policy_prompt,
    get_allowed_gestures,
    get_configured_gesture_safety_pool,
)


# Robot visual UI runtime (disabled; event-driven via LiveKit agent_state_changed)
# from robot.visual_ui_runtime import AgentVisualUiRuntime

# RAG client
from rag import (
    PanelGestureSync,
    PanelTourState,
    RAGServiceClient,
    RagConfig,
    build_rag_developer_context,
    panel_explanation_gesture,
)
from startup_greeting import speak_startup_greeting

# Survey / Quiz flow
from survey_flow import SurveyFlowTask, load_survey_state
from quiz_flow import QuizFlowTask, load_quiz_state


load_dotenv()
configure_utf8_output()
logger = logging.getLogger("basic-agent-truebar")

AGENT_AEC_WARMUP_DURATION_S = float(os.getenv("AGENT_AEC_WARMUP_DURATION_S", "1.0"))
AGENT_MIN_INTERRUPTION_DURATION_S = float(os.getenv("AGENT_MIN_INTERRUPTION_DURATION_S", "0.8"))
STARTUP_ID_CAPTURE_ALLOW_INTERRUPTION = env_bool("STARTUP_ID_CAPTURE_ALLOW_INTERRUPTION", True)

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
SUPERVISOR_API_URL = os.getenv("SUPERVISOR_API_URL", "http://127.0.0.1:8080").rstrip("/")
ID_CAPTURE_TIMEOUT_S = float(os.getenv("ID_CAPTURE_TIMEOUT_S", "15.0"))
ID_CAPTURE_POLL_INTERVAL_S = float(os.getenv("ID_CAPTURE_POLL_INTERVAL_S", "0.5"))
ID_CAPTURE_HTTP_TIMEOUT_S = float(os.getenv("ID_CAPTURE_HTTP_TIMEOUT_S", "3.0"))
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


class VisionApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        payload: dict[str, Any] | None = None,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {}
        self.user_message = user_message or (
            "Zajem osebne izkaznice trenutno ni na voljo. Prosim, poskusite znova kasneje."
        )


class HumanoidAgent(Agent):

    def __init__(self, chat_ctx: ChatContext | None = None) -> None:

        self._base_instructions = BASE_INSTRUCTIONS
        self._stop_requested: asyncio.Event = asyncio.Event()
        self._tasks = []
        self._latest_image: Optional[ImageContent] = None
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
        self._id_capture_lock = asyncio.Lock()
        self._startup_id_capture_released = asyncio.Event()
        self._startup_id_capture_task: asyncio.Task | None = None

        super().__init__(
            instructions=self._render_instructions(),
            chat_ctx=chat_ctx,
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
        """Called when agent joins the room. Register image and command handlers."""
        if self._handler_registered:
            self._ensure_startup_id_capture_task()
            return
        
        def _image_received_handler(reader, participant_identity):
            task = asyncio.create_task(
                self._image_received(reader, participant_identity)
            )
            self._tasks.append(task)
            task.add_done_callback(lambda t: self._tasks.remove(t))

        def _command_received_handler(reader, participant_identity):
            task = asyncio.create_task(
                self._command_received(reader, participant_identity)
            )
            self._tasks.append(task)
            task.add_done_callback(lambda t: self._tasks.remove(t))
        


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

        self._ensure_startup_id_capture_task()

    def release_startup_id_capture(self) -> None:
        """Allow the on-enter startup ID capture task to run after greeting."""
        self._startup_id_capture_released.set()

    def _ensure_startup_id_capture_task(self) -> None:
        if self._startup_id_capture_task is not None:
            return

        task = asyncio.create_task(self._run_startup_id_capture())
        self._startup_id_capture_task = task
        self._tasks.append(task)

        def _cleanup(done_task: asyncio.Task) -> None:
            if done_task in self._tasks:
                self._tasks.remove(done_task)

        task.add_done_callback(_cleanup)

    async def _run_startup_id_capture(self) -> None:
        await self._startup_id_capture_released.wait()
        try:
            result = await self._run_id_capture_flow(source="livekit_agent_on_enter")
            logger.info("Startup ID capture finished: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Startup ID capture failed unexpectedly")
    
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
                await self._call_gesture_api(
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
        cached_image: Optional[ImageContent] = None
        async with self._image_lock:
            if self._latest_image:
                cached_image = self._latest_image
                self._latest_image = None

        if cached_image:
            if self._max_context_images <= 0:
                logger.debug("Dropping cached image because attachments are disabled.")
            else:
                new_message.content.append(cached_image)
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
        self._remove_previous_rag_message(turn_ctx)

        if not self._is_rag_enabled():
            logger.info("External RAG disabled (runtime toggle or config).")
            return

        query = (getattr(new_message, "text_content", "") or "").strip()
        if not query:
            return

        rag_start = time.perf_counter()
        rag_context = ""
        search_query, reuse_last = self._panel_tour_state.resolve(query)
        if not search_query:
            logger.info("Hall of Fame RAG skipped for a control/filler turn.")
            return

        if reuse_last:
            rag_context = self._panel_tour_state.last_context
        else:
            kb_index = await self._ensure_rag_client()
            if kb_index is None:
                logger.warning("External RAG client not available.")
                return

            try:
                rag_context = await asyncio.to_thread(kb_index.search_wrapper, search_query)
            except Exception as exc:
                cfg = self._rag_config
                self._rag_client = None
                self._rag_next_retry_ts = time.monotonic() + cfg.retry_seconds
                logger.warning("External RAG search failed: %s (retry in %ss)", exc, cfg.retry_seconds)
                return

        rag_ms = (time.perf_counter() - rag_start) * 1000.0
        rag_context = rag_context.strip()
        if self._panel_tour_state.remember_context(rag_context):
            panel_gesture = panel_explanation_gesture(rag_context)
            self._panel_gesture_sync.queue(panel_gesture)
            logger.info(
                "Hall of Fame panel state confirmed: panel=%d pending_gesture=%s",
                self._panel_tour_state.current_sequence,
                panel_gesture,
            )
        if not rag_context:
            logger.info(
                "External RAG: search_ms=%.2f context_chars=0 msg_id=%s",
                rag_ms,
                getattr(new_message, "id", None),
            )
            return

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
        name="start_id_capture",
        description=(
            "Retry or manually start the ID capture flow, which prompts the user to show their ID card to the camera and captures relevant information. "
        ),
    )
    async def start_id_capture(
        self,
        context: RunContext,
    ) -> str:
        """Retry or manually start the ID capture flow."""
        context.disallow_interruptions()
        return await self._run_id_capture_flow(source="livekit_agent_tool")

    async def _run_id_capture_flow(self, *, source: str) -> str:
        speech_allow_interruptions = (
            STARTUP_ID_CAPTURE_ALLOW_INTERRUPTION
            if source == "livekit_agent_on_enter"
            else False
        )

        if self._id_capture_lock.locked():
            await self._say_with_retry(
                self.session,
                "Zajem osebne izkaznice je že v teku. Prosim, počakajte trenutek.",
                allow_interruptions=speech_allow_interruptions,
            )
            return "ID capture already running."

        async with self._id_capture_lock:
            # Arm card capture before prompting the user to move. Otherwise the
            # vision tracker can treat the card-showing movement as person_left
            # and wrap the conversation before suppression is active.
            request_id: str | None = None
            try:
                logger.info("Starting ID capture request source=%s", source)
                request = await self._start_vision_card_capture(
                    ID_CAPTURE_TIMEOUT_S,
                    source=source,
                )
                request_id = str(request["request_id"])
                logger.info("ID capture request started request_id=%s source=%s", request_id, source)
                await self._say_with_retry(
                    self.session,
                    "Prosim, pokažite svojo osebno izkaznico kameri.",
                    allow_interruptions=speech_allow_interruptions,
                )
                result = await self._wait_for_vision_card_capture(
                    request_id,
                    timeout_s=ID_CAPTURE_TIMEOUT_S,
                )
                logger.info(
                    "ID capture request completed request_id=%s state=%s status=%s",
                    request_id,
                    result.get("state"),
                    result.get("status"),
                )
            except VisionApiError as exc:
                logger.warning("ID capture vision API error: %s", exc)
                if request_id:
                    await self._cancel_vision_card_capture(request_id)
                await self._say_with_retry(
                    self.session,
                    exc.user_message,
                    allow_interruptions=speech_allow_interruptions,
                )
                return f"ID capture failed: {exc}"
            except Exception as exc:
                logger.exception("Unexpected ID capture failure")
                if request_id:
                    await self._cancel_vision_card_capture(request_id)
                await self._say_with_retry(
                    self.session,
                    "Zajem osebne izkaznice trenutno ni na voljo. Prosim, poskusite znova kasneje.",
                    allow_interruptions=speech_allow_interruptions,
                )
                return f"ID capture failed unexpectedly: {exc}"

            if result.get("state") != "captured":
                state = str(result.get("state") or "unknown")
                await self._say_with_retry(
                    self.session,
                    "Zajem osebne izkaznice ni uspel. Prosim, poskusimo znova.",
                    allow_interruptions=speech_allow_interruptions,
                )
                return f"ID capture did not complete successfully: {state}"

            # 3. Wait for the ID capture result and process it (this part depends on the specific vision API and is left as a placeholder here).
            # add say name?
            await self._say_with_retry(
                self.session,
                "Hvala. Svojo izkaznico lahko sedaj umaknete s kamere.",
                allow_interruptions=speech_allow_interruptions,
            )

            # 4. With the captured image, call an external api with the image. The api will process it and return instructions (where to direct user, what to say, etc.). This part is also left as a placeholder.
            # call external PETROL_RECEPTION_API with the captured image

            await self._say_with_retry(
                self.session,
                "Vaš prihod je bil sporočen ustreznemu osebju. Prosimo počakajte trenutek, da dobim povratne informacije.",
                allow_interruptions=speech_allow_interruptions,
            )

            await asyncio.sleep(3.0)
            # 5. Wait for response
            #mock for now
            response = "Hvala za potrpežljivost. V kratkem vas bo sprejel naš zaposleni. Do takrat pa vas vabim, da se udobno namestite v čakalnici in uživate v naši ponudbi na avtomatu za osvežitev."


            # 6. Relay the response to the user
            await self._say_with_retry(
                self.session,
                response,
                allow_interruptions=speech_allow_interruptions,
            )

            return "ID capture completed and mock reception response delivered."
        
        
    
    @function_tool(
        name="trigger_gesture",
        description=(
            "Trigger a specific gesture via the gesture API. "
            f"Available gestures: {GESTURE_LIST}"
        ),
    )
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
        await self._call_gesture_api(
            normalized_gesture,
            requested=requested,
            force_gesture=False,
        )
        return f"Gesture '{normalized_gesture}' triggered."
    
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
                '"Anketa trenutno ni na voljo." Do not start the survey and do not add extra suggestions.'
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
                '"Kviz trenutno ni na voljo." Do not start the quiz and do not add extra suggestions.'
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
            flow_rules.append('- If the user asks to start a quiz or kviz while disabled, reply exactly: "Kviz trenutno ni na voljo."')

        if survey_enabled:
            flow_rules.append("- If the user explicitly asks to start a survey or anketa, call `start_survey`.")
        else:
            flow_rules.append("- Do not call `start_survey` while survey runtime is disabled.")
            flow_rules.append('- If the user asks to start a survey or anketa while disabled, reply exactly: "Anketa trenutno ni na voljo."')

        flow_rules.extend(
            [
                "- Do not continue normal conversation when an enabled quiz or survey should be started.",
                "- If the user asks about quizzes in general but does not ask to begin, answer briefly without starting one.",
                "- If the user asks about surveys in general but does not ask to begin, answer briefly without starting one.",
                "- Keep disabled quiz or survey replies to one short sentence with no apology and no extra offer to continue chatting.",
            ]
        )

        id_capture_context = [
            "Reception ID capture context:",
            "- You may be used in a reception setting to welcome visitors and notify head office or reception staff when someone arrives.",
            "- ID capture starts automatically after the startup greeting.",
            "- Do not call `start_id_capture` just because a user introduces themselves, says they have arrived, or says they are here for a meeting or visit.",
            "- Only call `start_id_capture` if the user asks to retry ID capture or if a manual retry is clearly needed after a failed capture.",
            "- The ID capture flow helps staff identify the arrival and send instructions or someone to greet the visitor.",
            "- Do not describe technical details of the capture system. The startup flow and `start_id_capture` tool handle the capture.",
        ]

        return (
            f"{self._base_instructions}\n\n"
            + "\n".join(flow_rules)
            + "\n\n"
            + "\n".join(id_capture_context)
            + "\n"
        )

    async def _say_with_retry(
        self,
        session: AgentSession,
        text: str,
        *,
        allow_interruptions: bool = False,
    ) -> None:
        last_exc: Exception | None = None
        for delay in (0.0, 0.05, 0.2, 0.5):
            if delay:
                await asyncio.sleep(delay)
            try:
                await session.say(
                    text=text,
                    allow_interruptions=allow_interruptions,
                    add_to_chat_ctx=True,
                )
                return
            except RuntimeError as exc:
                last_exc = exc
        logger.warning("Unable to enqueue command speech: %s", last_exc)

    async def _vision_api_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_s: float = ID_CAPTURE_HTTP_TIMEOUT_S,
    ) -> dict[str, Any]:
        url = f"{SUPERVISOR_API_URL}{path}"

        def _do_request() -> dict[str, Any]:
            data = None
            headers = {}
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"

            req = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method=method.upper(),
            )
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
                raise VisionApiError(
                    f"Vision API returned HTTP {exc.code}",
                    status=exc.code,
                    payload=parsed,
                    user_message=self._vision_api_user_message(exc.code, parsed),
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise VisionApiError(
                    f"Vision API request failed: {exc}",
                    user_message=(
                        "Zajem osebne izkaznice trenutno ni na voljo. "
                        "Prosim, poskusite znova kasneje."
                    ),
                ) from exc
            except json.JSONDecodeError as exc:
                raise VisionApiError(
                    "Vision API returned invalid JSON",
                    user_message=(
                        "Zajem osebne izkaznice trenutno ni na voljo. "
                        "Prosim, poskusite znova kasneje."
                    ),
                ) from exc

        result = await asyncio.to_thread(_do_request)
        logger.info("Vision API %s %s response: %s", method.upper(), path, result)
        return result

    def _vision_api_user_message(
        self,
        status: int,
        payload: dict[str, Any] | None = None,
    ) -> str:
        if status == 403:
            return "Zajem osebne izkaznice trenutno ni omogočen."
        if status == 409:
            return "Drug zajem osebne izkaznice je že v teku. Prosim, poskusite znova čez trenutek."
        if status == 404:
            return "Zahteve za zajem osebne izkaznice ni bilo mogoče najti. Prosim, poskusimo znova."
        return "Zajem osebne izkaznice trenutno ni na voljo. Prosim, poskusite znova kasneje."

    async def _start_vision_card_capture(
        self,
        timeout_s: float,
        *,
        source: str,
    ) -> dict[str, Any]:
        result = await self._vision_api_json(
            "POST",
            "/api/vision/card-capture/request",
            {
                "timeout_s": timeout_s,
                "target_type": "id_card",
                "source": source,
            },
        )
        if not result.get("request_id"):
            raise VisionApiError(
                "Vision API start response did not include request_id",
                payload=result,
            )
        return result

    async def _get_vision_card_capture(self, request_id: str) -> dict[str, Any]:
        quoted_request_id = urllib.parse.quote(request_id, safe="")
        return await self._vision_api_json(
            "GET",
            f"/api/vision/card-capture/{quoted_request_id}",
        )

    async def _cancel_vision_card_capture(self, request_id: str) -> None:
        quoted_request_id = urllib.parse.quote(request_id, safe="")
        try:
            await self._vision_api_json(
                "POST",
                f"/api/vision/card-capture/{quoted_request_id}/cancel",
            )
        except Exception as exc:
            logger.warning("Best-effort ID capture cancel failed for %s: %s", request_id, exc)

    async def _wait_for_vision_card_capture(
        self,
        request_id: str,
        *,
        timeout_s: float,
    ) -> dict[str, Any]:
        terminal_states = {"captured", "failed", "cancelled", "expired"}
        deadline = time.monotonic() + timeout_s + 2.0

        while True:
            result = await self._get_vision_card_capture(request_id)
            state = str(result.get("state") or "").lower()
            if state in terminal_states:
                return result

            if time.monotonic() >= deadline:
                await self._cancel_vision_card_capture(request_id)
                raise VisionApiError(
                    "Vision card capture timed out while polling",
                    payload=result,
                    user_message=(
                        "Zajem osebne izkaznice je potekel. "
                        "Prosim, poskusimo znova."
                    ),
                )

            await asyncio.sleep(ID_CAPTURE_POLL_INTERVAL_S)

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
        self._tasks.append(task)
        task.add_done_callback(
            lambda done: self._tasks.remove(done) if done in self._tasks else None
        )

    def bind_panel_gesture_sync(self, session: AgentSession) -> None:
        self._panel_gesture_sync.bind(
            session,
            lambda gesture: self._schedule_gesture_api_call(
                gesture,
                requested=False,
                force_gesture=False,
            ),
        )

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
        turn_handling=TURN_HANDLING_OPTIONS,
        aec_warmup_duration=AGENT_AEC_WARMUP_DURATION_S,
    )
    # visual_ui_runtime = AgentVisualUiRuntime(logger=logger)
    # visual_ui_runtime.bind(session=session, ctx=ctx)
    # await visual_ui_runtime.prepare()
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
    initial_greeting = build_initial_greeting().strip()
    if initial_greeting:
        await speak_startup_greeting(
            session,
            initial_greeting,
            capture_gate_enabled=env_bool("STARTUP_GREETING_CAPTURE_GATE", False),
            tail_ms=max(0, int(os.getenv("STARTUP_GREETING_CAPTURE_TAIL_MS", "120"))),
        )
    agent.release_startup_id_capture()

print(
    f"agent_main.py import completed in {time.perf_counter() - _AGENT_MODULE_BOOT_TS:.3f}s",
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
