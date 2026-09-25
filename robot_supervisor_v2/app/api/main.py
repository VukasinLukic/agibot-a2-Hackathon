"""
FastAPI application for Robot Supervisor V2.

Provides REST API for:
1. Service management (start/stop/status)
2. Conversation control (dispatch/wrap)
3. Agent selection
4. System status
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
import asyncio
import io
import ipaddress
import json
import logging
import shlex
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from dotenv import load_dotenv
from livekit.api import AccessToken, VideoGrants
import httpx
from pydantic import BaseModel, Field

_repo_root = Path(__file__).resolve().parents[3]  # go up to Humanoid Livekit/
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from robot_services.gestures import (
    DEFAULT_GESTURE_SAFETY_POOL,
    GESTURE_SAFETY_POOLS,
    get_allowed_gestures,
    get_catalog,
    is_gesture_safety_pool,
    normalize_gesture_safety_pool,
    resolve_gesture_catalog_id_for_robot_context,
)
from content_service.content_store.prompts.service import PromptService
from livekit_config.prompt_builder import PromptBuilder
from livekit_config.voice_canary import english_voice_enabled

# Deterministic env loading order:
# 1) repo .env (optional)
# 2) supervisor app/.env (preferred for local supervisor runs)
_repo_env = _repo_root / ".env"
_supervisor_env = _repo_root / "robot_supervisor_v2" / "app" / ".env"
if _repo_env.exists():
    load_dotenv(_repo_env, override=False)
if _supervisor_env.exists():
    load_dotenv(_supervisor_env, override=True)

from ..services.registry import ServiceConflictError, ServiceRegistry, ServiceManager
from ..services.base import BaseService, ServiceState
from ..services.camera_bridge import CameraBridgeService
from ..services.vision_controller import VisionControllerService
from ..services.video_recording import (
    DEFAULT_DEVICE as DEFAULT_RECORDING_DEVICE,
    DEFAULT_FRAMERATE as DEFAULT_RECORDING_FRAMERATE,
    DEFAULT_OUTPUT_DIR as DEFAULT_RECORDING_OUTPUT_DIR,
    DEFAULT_RESOLUTION as DEFAULT_RECORDING_RESOLUTION,
    VideoRecordingService,
)
from robot_services.vision.detection.defaults import (
    DEFAULT_CAMERA_FOURCC,
    DEFAULT_CAMERA_FPS,
    DEFAULT_CARD_CAPTURE_FPS,
    DEFAULT_DETECTION_FPS,
)
from ..runtime_environment import (
    EnvironmentConfigError,
    apply_runtime_environment_to_process,
    runtime_environment_status,
    validate_runtime_environment,
    write_selected_environment,
)
from ..supervisor_config import (
    SERVICE_ROBOT_CONTEXT_KEY,
    SupervisorConfigError,
    load_startup_config,
)
from ..speech_config import (
    ACCEPTED_AUDIO_EXTENSIONS,
    background_audio_options,
    english_locale_active,
    get_audio_upload_dir,
    get_speech_state_file,
    get_custom_transformations,
    load_speech_config,
    save_speech_config,
    voice_identity,
)

from ..controllers.manual import ManualController
from ..controllers.face_identity import FaceIdentityController
from ..controllers.vision import VisionController
from ..models.conversation import (
    AgentCommandRequest,
    GestureCatalogResponse,
    VisionCardCaptureRequest,
    VisionCardCaptureResult,
    VisionFaceCaptureRequest,
    VisionFaceCaptureResult,
    VisionFaceForgetRequest,
    VisionFaceForgetResult,
    VisionPresenceEvent,
)
from ..utils.logging import archive_logs
from ..utils.agent_commands import send_agent_command
from ..transcript_store import TranscriptStore
from ..transcript_manager import TranscriptManager
from ..video_stream import StreamConfig, mjpeg_stream, parse_resolution
import os

from ..models.conference import (
    ConferenceActiveEventResponse,
    ConferenceEventDeleteResponse,
    ConferenceEventDocument,
    ConferenceEventListResponse,
    ConferenceProgram,
)
from ..services.conference_scripts import ConferenceScriptService
from .prompts import router as prompts_router
from .quizzes import router as quizzes_router
from .surveys import router as surveys_router
from .lidar_costmap import router as lidar_costmap_router
from .nav_missions import router as nav_missions_router
from .system_clock import router as system_clock_router


# Global state
service_manager: Optional[ServiceManager] = None
manual_controller: Optional[ManualController] = None
face_identity_controller: Optional[FaceIdentityController] = None
vision_controller: Optional[VisionController] = None
transcript_store: Optional[TranscriptStore] = None
transcript_manager: Optional[TranscriptManager] = None
conference_script_service: Optional[ConferenceScriptService] = None
command_presets: list[dict[str, Any]] = []
active_robot_context: Dict[str, str] = {}
logger = logging.getLogger(__name__)


def _service_startup_config(
    service_def: Dict[str, Any],
    robot_context: Dict[str, str],
) -> Dict[str, Any]:
    """Return legacy service config plus explicit migrated context."""

    config = dict(service_def.get("config") or {})
    if service_def.get("type") in {"audio-bridge", "robot-temperature-monitor", "gesture-bridge", "voice-agent"}:
        config[SERVICE_ROBOT_CONTEXT_KEY] = dict(robot_context)
    return config


def _robot_context_payload() -> Dict[str, str]:
    """Return resolved robot identity for API responses."""

    return dict(active_robot_context)


_NETWORK_STATUS_CACHE_SECONDS = 5.0
_network_status_cache: Optional[Dict[str, Any]] = None
_network_status_cache_at = 0.0
_rag_runtime_default_enabled = os.getenv("RAG_EXTERNAL_ENABLE", "").strip().lower() in {"1", "true", "yes", "on"}
_rag_runtime_state: Dict[str, Any] = {
    "enabled": _rag_runtime_default_enabled,
    "default_enabled": _rag_runtime_default_enabled,
    "available": False,
    "source": "env",
    "updated_at": time.time(),
}
_rag_runtime_job_id: Optional[str] = None
_knowledge_query_slugs: Optional[list[str]] = None

MAIN_KNOWLEDGE_INDEX_SLUG = "main"


class CommandPresetRunRequest(BaseModel):
    confirmed: bool = False


class EnvironmentUpdateRequest(BaseModel):
    environment: str


class SpeechVoiceConfig(BaseModel):
    provider: str
    model: str = ""
    voice_id: str = ""
    label: str
    language: str = ""


class SpeechBackgroundAudioConfig(BaseModel):
    enabled: bool = False
    source_type: str = "builtin"
    source: str = "KEYBOARD_TYPING"
    volume: float = 0.45


class SpeechConfigUpdateRequest(BaseModel):
    active_voice: SpeechVoiceConfig
    voices: list[SpeechVoiceConfig]
    custom_transformations: Dict[str, str]
    english_transformations: Dict[str, str] = Field(default_factory=dict)
    background_audio: SpeechBackgroundAudioConfig = Field(default_factory=SpeechBackgroundAudioConfig)
    initial_greeting: str | None = None
    goodbye_text: str | None = None


class VideoRecordingRenameRequest(BaseModel):
    filename: str


class PeopleFaceUpdateRequest(BaseModel):
    display_name: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    notes: str = ""
    match_images: str = "both"


def _voice_options_with_active(
    voices: list[dict[str, Any]],
    active_voice: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    options = [dict(voice) for voice in voices if isinstance(voice, dict) and voice.get("provider")]
    active_identity = voice_identity(active_voice)
    if active_identity and active_identity not in {voice_identity(voice) for voice in options}:
        options.insert(0, dict(active_voice or {}))
    return options


def _default_camera_device_for_platform() -> str:
    return "/dev/video0" if sys.platform.startswith("linux") else "0"


def _normalize_example_camera_config(services_config: list[dict[str, Any]]) -> None:
    if sys.platform.startswith("linux"):
        return

    default_device = _default_camera_device_for_platform()
    for service_def in services_config:
        name = service_def.get("name")
        if name != "conversation-camera-stream" and not str(name).startswith("camera-bridge"):
            continue

        config = service_def.setdefault("config", {})
        configured_device = str(config.get("device", "")).strip()
        if not configured_device:
            config["device"] = default_device
            continue

        candidate_path = Path(configured_device)
        is_index = configured_device.isdigit()
        is_linux_video_path = configured_device.startswith("/dev/video")
        if not is_index and not is_linux_video_path and not candidate_path.exists():
            config["device"] = default_device


def _service_config_value(service, key: str, default: Any = None) -> Any:
    if not service:
        return default
    return service.get_config().get(key, default)


def _get_camera_bridge_service(service_name: str = "camera-bridge") -> CameraBridgeService:
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    service = service_manager.get(service_name)
    if not isinstance(service, CameraBridgeService):
        raise HTTPException(status_code=404, detail=f"Camera bridge service '{service_name}' not found")
    return service


def _list_camera_bridge_services() -> list[CameraBridgeService]:
    if not service_manager:
        return []
    return [
        service
        for service in service_manager.list_all()
        if isinstance(service, CameraBridgeService)
    ]


def _camera_bridge_stream_info_payload(service: CameraBridgeService) -> dict[str, Any]:
    config = service.get_config()
    return {
        "service_name": service.name,
        "display_name": service.display_name,
        "running": service.get_status().state == ServiceState.RUNNING,
        "room": config.get("room") or os.getenv("LIVEKIT_ROOM", "g1-lab"),
        "identity": service.stream_identity,
        "track_name": service.stream_track_name,
        "topic": service.stream_topic,
    }


def _is_service_state_camera_active(state: ServiceState | str) -> bool:
    return state in {ServiceState.STARTING, ServiceState.RUNNING, "starting", "running"}


def _camera_device_owner_active() -> bool:
    if not service_manager:
        return False

    for service in service_manager.list_all():
        status = service.get_status()
        if isinstance(service, (CameraBridgeService, VisionControllerService, VideoRecordingService)) and _is_service_state_camera_active(status.state):
            return True
        if service.name == "conversation-camera-stream" and _is_service_state_camera_active(status.state):
            return True

    return False


def _lightweight_video_devices_payload(default_device: Optional[str] = None) -> dict[str, Any]:
    if sys.platform.startswith("linux"):
        devices = sorted(Path("/dev").glob("video*"))
        if devices:
            return {
                "devices": [
                    {
                        "index": idx,
                        "path": str(dev),
                        "name": str(dev),
                        "is_default": idx == 0,
                        "width": None,
                        "height": None,
                        "resolution": None,
                        "has_valid_resolution": False,
                    }
                    for idx, dev in enumerate(devices)
                ]
            }

    fallback_device = default_device or _default_camera_device_for_platform()
    return {
        "devices": [
            {
                "index": 0,
                "path": fallback_device,
                "name": fallback_device,
                "is_default": True,
                "width": None,
                "height": None,
                "resolution": None,
                "has_valid_resolution": False,
            }
        ]
    }


def _get_gesture_bridge_pool() -> str:
    if not service_manager:
        return DEFAULT_GESTURE_SAFETY_POOL

    gesture_service = service_manager.get("gesture-bridge")
    return normalize_gesture_safety_pool(
        _service_config_value(gesture_service, "safety_pool", DEFAULT_GESTURE_SAFETY_POOL)
    )


def _active_gesture_catalog_id() -> str:
    return resolve_gesture_catalog_id_for_robot_context(active_robot_context)


def _get_voice_agent_pool() -> str:
    if not service_manager:
        return DEFAULT_GESTURE_SAFETY_POOL

    voice_agent = service_manager.get("voice-agent")
    return normalize_gesture_safety_pool(
        _service_config_value(voice_agent, "gesture_safety_pool", DEFAULT_GESTURE_SAFETY_POOL)
    )


def _sync_voice_agent_gesture_pool() -> str:
    if not service_manager:
        return DEFAULT_GESTURE_SAFETY_POOL

    pool = _get_gesture_bridge_pool()

    gesture_service = service_manager.get("gesture-bridge")
    if gesture_service:
        gesture_service._config["safety_pool"] = pool

    voice_agent = service_manager.get("voice-agent")
    if voice_agent:
        voice_agent._config["gesture_safety_pool"] = pool

    return pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Handles startup and shutdown of core components.
    """
    global service_manager, manual_controller, vision_controller, face_identity_controller, command_presets, active_robot_context

    # Startup: Load config and register services
    logger.info("Starting Robot Supervisor V2")

    # Load configuration
    try:
        loaded_config = load_startup_config()
    except SupervisorConfigError:
        logger.exception("Supervisor config is invalid")
        raise

    supervisor_config = loaded_config.config
    config_path = loaded_config.path
    if loaded_config.using_example:
        logger.info("Using example config (config.yaml not found)")
    else:
        logger.info("Using config.yaml")

    logger.info("Config path: %s", config_path)
    logger.info(
        "Robot config: id=%s name=%s platform=%s model=%s",
        supervisor_config.robot.id,
        supervisor_config.robot.name,
        supervisor_config.robot.platform.value,
        supervisor_config.robot.model.value,
    )
    active_robot_context = supervisor_config.robot.to_service_context()

    command_presets = _load_command_presets(
        {"command_presets": supervisor_config.command_presets}
    )
    logger.info("Loaded %s command presets", len(command_presets))

    services_config = list(supervisor_config.services)
    if loaded_config.using_example:
        _normalize_example_camera_config(services_config)

    # Create service manager
    service_manager = ServiceManager()

    # Register all services from config
    for service_def in services_config:
        service = ServiceRegistry.create_service(
            service_type=service_def["type"],
            name=service_def["name"],
            config=_service_startup_config(service_def, active_robot_context)
        )
        service_manager.register(service)

    if not service_manager.get("video-recording-service"):
        service_manager.register(
            ServiceRegistry.create_service(
                service_type="video-recording-service",
                name="video-recording-service",
                config={
                    "display_name": "Video Recording Service",
                    "device": DEFAULT_RECORDING_DEVICE,
                    "resolution": DEFAULT_RECORDING_RESOLUTION,
                    "framerate": DEFAULT_RECORDING_FRAMERATE,
                    "output_dir": DEFAULT_RECORDING_OUTPUT_DIR,
                    "record_overlay_enabled": False,
                    "cv_debug_enabled": False,
                    "cv_debug_mode": "id_capture",
                    "id_debug_enabled": False,
                    "id_debug_overlay_enabled": False,
                    "id_debug_record_captures_enabled": False,
                    "id_debug_mode": "capture",
                    "id_debug_capture_image_mode": "crop",
                    "manual_only": True,
                    "optional": True,
                },
            )
        )
        logger.info("Registered default Video Recording Service")

    logger.info("Registered %s services", len(service_manager.list_all()))

    try:
        active_runtime_env = apply_runtime_environment_to_process()
        logger.info("Runtime Azure environment: %s", active_runtime_env.environment)
    except EnvironmentConfigError as exc:
        logger.warning("Runtime Azure environment is not fully configured: %s", exc)

    # Create manual controller
    manual_controller = ManualController(service_manager)
    logger.info("Manual controller initialized")

    vision_controller = VisionController(manual_controller, service_manager)
    logger.info("Vision controller initialized")
    face_identity_controller = FaceIdentityController(service_manager)
    logger.info("Face identity controller initialized")
    vision_service = service_manager.get("vision-controller")
    if vision_service and hasattr(vision_service, "set_vision_controller"):
        vision_service.set_vision_controller(vision_controller)

    active_gesture_pool = _sync_voice_agent_gesture_pool()
    app.state.service_manager = service_manager
    app.state.manual_controller = manual_controller
    app.state.vision_controller = vision_controller
    app.state.robot_context = dict(active_robot_context)
    logger.info("Gesture safety pool synchronized to '%s'", active_gesture_pool)

    # Initialize transcript system
    livekit_url = os.getenv("LIVEKIT_URL")
    livekit_api_key = os.getenv("LIVEKIT_API_KEY")
    livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")

    global transcript_store, transcript_manager
    if livekit_url and livekit_api_key and livekit_api_secret:
        transcript_store = TranscriptStore(max_entries=500)
        transcript_manager = TranscriptManager(
            store=transcript_store,
            livekit_url=livekit_url,
            livekit_api_key=livekit_api_key,
            livekit_api_secret=livekit_api_secret,
            supervisor_identity=os.getenv("ROBOT_SUPERVISOR_CONVERSATION_ID", "supervisor-monitor"),
            agent_name=os.environ["LIVEKIT_AGENT_NAME"]
        )
        logger.info("Transcript manager initialized")
    else:
        logger.warning("Transcript manager disabled (missing LiveKit credentials)")

    logger.info("Robot Supervisor V2 ready")

    global conference_script_service
    conference_script_service = ConferenceScriptService(_repo_root / "conference_scripts")
    logger.info("Conference script service initialized")

    
    try:
        yield
    finally:
        # Shutdown: Stop all services (always runs, even on error)
        logger.info("Shutting down Robot Supervisor V2")

        # Disconnect transcript manager
        if transcript_manager:
            try:
                await transcript_manager.disconnect()
            except Exception:
                logger.exception("Error disconnecting transcript manager")

        # Clean up knowledge service
        try:
            #knowledge = await get_knowledge_service()
            #await knowledge.close()
            logger.info("Knowledge service cleaned up")
        except Exception:
            logger.exception("Error cleaning up knowledge service")

        # Wrap any active conversation
        if manual_controller:
            try:
                status = manual_controller.get_status()
                if status["state"] == "engaged":
                    logger.info("Wrapping active conversation")
                    await manual_controller.wrap_conversation()
            except Exception:
                logger.exception("Error wrapping conversation")

        # Stop all services
        if service_manager:
            try:
                logger.info("Stopping all services")
                await service_manager.stop_all()
            except Exception:
                logger.exception("Error stopping services")

        # Archive logs
        logs_dir = Path(__file__).parent.parent.parent / "logs"
        try:
            archive_dir = archive_logs(logs_dir)
            if archive_dir:
                logger.info("Archived logs to %s", archive_dir.relative_to(logs_dir.parent))
            else:
                logger.info("No logs to archive")
        except Exception:
            logger.exception("Error archiving logs")

        logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Robot Supervisor V2 API",
    description="Service orchestration and conversation management for humanoid robot",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(prompts_router)
app.include_router(quizzes_router)
app.include_router(surveys_router)
app.include_router(lidar_costmap_router)
app.include_router(nav_missions_router)
app.include_router(system_clock_router)

def _get_robot_temperature_state() -> Optional[Dict[str, Any]]:
    if not service_manager:
        return None

    service = service_manager.get("robot-temperature-monitor")
    if not service or not hasattr(service, "get_temperature_state"):
        return None

    return service.get_temperature_state()


def _run_status_command(args: list[str], timeout: float = 1.5) -> str:
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ""

    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _split_nmcli_fields(line: str) -> list[str]:
    fields: list[str] = []
    current = []
    escaped = False

    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)

    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def _get_interface_ip(interface: Optional[str]) -> Optional[str]:
    """Return the IPv4 address assigned to a network interface, or None."""
    if not interface:
        return None
    # `ip -4 -o addr show <iface>` gives a single line like:
    #   3: wlan0    inet 10.242.17.28/24 brd 10.242.17.255 scope global dynamic wlan0\...
    output = _run_status_command(["ip", "-4", "-o", "addr", "show", interface], timeout=1.0)
    for token in output.split():
        if "/" in token:
            try:
                return str(ipaddress.IPv4Interface(token).ip)
            except ValueError:
                pass
    return None


def _linux_wifi_state() -> Optional[Dict[str, Any]]:
    # Fast path: iwgetid gives us the SSID in ~0.1 s.
    # Run it without -r as well to get the interface name for IP lookup.
    iwgetid_ssid = _run_status_command(["iwgetid", "-r"], timeout=0.7)
    if iwgetid_ssid:
        # "iwgetid" (no flags) → "wlan0     ESSID:\"SSID\""
        iwgetid_full = _run_status_command(["iwgetid"], timeout=0.5)
        interface = iwgetid_full.split()[0] if iwgetid_full else None
        return {
            "connected": True,
            "ssid": iwgetid_ssid,
            "interface": interface,
            "ip": _get_interface_ip(interface),
            "error": None,
        }

    nmcli_wifi = _run_status_command(
        ["nmcli", "-t", "-f", "ACTIVE,SSID,DEVICE", "dev", "wifi", "list", "--rescan", "no"]
    )
    for line in nmcli_wifi.splitlines():
        fields = _split_nmcli_fields(line)
        if len(fields) >= 3 and fields[0] == "yes":
            ssid = fields[1].strip() or None
            interface = fields[2].strip() or None
            return {
                "connected": True,
                "ssid": ssid,
                "interface": interface,
                "ip": _get_interface_ip(interface),
                "error": None,
            }

    nmcli_device = _run_status_command(
        ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"]
    )
    for line in nmcli_device.splitlines():
        fields = _split_nmcli_fields(line)
        if len(fields) >= 4 and fields[1] == "wifi" and fields[2] == "connected":
            interface = fields[0].strip() or None
            return {
                "connected": True,
                "ssid": fields[3].strip() or None,
                "interface": interface,
                "ip": _get_interface_ip(interface),
                "error": None,
            }

    return None


def _macos_wifi_device() -> Optional[str]:
    output = _run_status_command(["networksetup", "-listallhardwareports"])
    current_port = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Hardware Port:"):
            current_port = line.partition(":")[2].strip()
        elif line.startswith("Device:") and current_port in {"Wi-Fi", "AirPort"}:
            return line.partition(":")[2].strip() or None

    return None


def _macos_wifi_state() -> Optional[Dict[str, Any]]:
    checked_interfaces: set[str] = set()
    candidates = [_macos_wifi_device(), "en0", "en1"]

    for interface in candidates:
        if not interface or interface in checked_interfaces:
            continue
        checked_interfaces.add(interface)
        output = _run_status_command(["networksetup", "-getairportnetwork", interface])
        if not output:
            continue
        if "Current Wi-Fi Network:" in output:
            ssid = output.partition("Current Wi-Fi Network:")[2].strip() or None
            return {
                "connected": bool(ssid),
                "ssid": ssid,
                "interface": interface,
                "error": None,
            }
        if "not associated" in output.lower() or "power is currently off" in output.lower():
            return {
                "connected": False,
                "ssid": None,
                "interface": interface,
                "error": None,
            }

    return None


def _detect_network_state() -> Dict[str, Any]:
    state: Optional[Dict[str, Any]] = None
    if sys.platform.startswith("linux"):
        state = _linux_wifi_state()
    elif sys.platform == "darwin":
        state = _macos_wifi_state()

    if state:
        return state

    return {
        "connected": False,
        "ssid": None,
        "interface": None,
        "error": "wifi_status_unavailable",
    }


def _get_network_state() -> Dict[str, Any]:
    global _network_status_cache, _network_status_cache_at

    now = time.monotonic()
    if (
        _network_status_cache is not None
        and now - _network_status_cache_at < _NETWORK_STATUS_CACHE_SECONDS
    ):
        return _network_status_cache

    _network_status_cache = _detect_network_state()
    _network_status_cache_at = now
    return _network_status_cache


def _conversation_value(conversation_status: Dict[str, Any], key: str) -> Any:
    value = conversation_status.get(key)
    return getattr(value, "value", value)


def _reset_rag_runtime_state(*, available: bool, job_id: Optional[str]) -> None:
    global _rag_runtime_job_id

    changed = (
        _rag_runtime_state["enabled"] != _rag_runtime_default_enabled
        or _rag_runtime_state["available"] != available
        or _rag_runtime_state["source"] != "env"
        or _rag_runtime_job_id != job_id
    )

    _rag_runtime_state.update(
        {
            "enabled": _rag_runtime_default_enabled,
            "default_enabled": _rag_runtime_default_enabled,
            "available": available,
            "source": "env",
        }
    )
    _rag_runtime_job_id = job_id
    if changed:
        _rag_runtime_state["updated_at"] = time.time()


def _sync_rag_runtime_state(conversation_status: Dict[str, Any]) -> Dict[str, Any]:
    state = _conversation_value(conversation_status, "state")
    job_id = _conversation_value(conversation_status, "job_id")

    if state != "engaged" or not job_id:
        _reset_rag_runtime_state(available=False, job_id=None)
    elif _rag_runtime_job_id != str(job_id):
        _reset_rag_runtime_state(available=True, job_id=str(job_id))
    elif not _rag_runtime_state["available"]:
        _rag_runtime_state["available"] = True
        _rag_runtime_state["updated_at"] = time.time()

    return dict(_rag_runtime_state)


def _get_runtime_state(conversation_status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rag": _sync_rag_runtime_state(conversation_status),
    }


def _parse_rag_runtime_toggle(text: str, gesture: Optional[str], steps: list) -> Optional[bool]:
    candidates: list[str] = []
    if text.strip() and not gesture and not steps:
        candidates.append(text)
    elif not text.strip() and len(steps) == 1:
        step = steps[0]
        step_gesture = getattr(step, "gesture", None)
        step_text = getattr(step, "text", "")
        if not step_gesture:
            candidates.append(step_text)

    if len(candidates) != 1:
        return None

    command = candidates[0].strip().upper()
    if command == "__RAG_ON__":
        return True
    if command == "__RAG_OFF__":
        return False
    return None


def _record_rag_runtime_toggle(enabled: bool) -> None:
    _rag_runtime_state.update(
        {
            "enabled": enabled,
            "default_enabled": _rag_runtime_default_enabled,
            "available": bool(_rag_runtime_state["available"]),
            "source": "operator",
            "updated_at": time.time(),
        }
    )


def _coerce_command_args(value: Any) -> Optional[list[str]]:
    if not isinstance(value, list) or not value:
        return None

    args: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        if item == "":
            return None
        args.append(item)

    return args


def _coerce_timeout_seconds(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = 60.0
    return min(max(timeout, 1.0), 600.0)


def _coerce_max_output_chars(value: Any) -> int:
    try:
        max_chars = int(value)
    except (TypeError, ValueError):
        max_chars = 12000
    return min(max(max_chars, 1000), 50000)


def _load_command_presets(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_presets = config.get("command_presets", [])
    if not isinstance(raw_presets, list):
        logger.warning("Ignoring command_presets because it is not a list")
        return []

    loaded: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in raw_presets:
        if not isinstance(raw, dict):
            logger.warning("Skipping invalid command preset entry: %r", raw)
            continue

        preset_id = str(raw.get("id", "")).strip()
        if not preset_id or preset_id in seen_ids:
            logger.warning("Skipping command preset with missing or duplicate id: %r", preset_id)
            continue

        steps: list[list[str]] = []
        command = _coerce_command_args(raw.get("command"))
        if command:
            steps = [command]
        elif isinstance(raw.get("steps"), list):
            for raw_step in raw["steps"]:
                step = _coerce_command_args(raw_step)
                if not step:
                    steps = []
                    break
                steps.append(step)

        if not steps:
            logger.warning("Skipping command preset '%s' because it has no valid command or steps", preset_id)
            continue

        preset = {
            "id": preset_id,
            "label": str(raw.get("label") or preset_id),
            "description": str(raw.get("description") or ""),
            "cwd": str(raw["cwd"]) if raw.get("cwd") else None,
            "steps": steps,
            "timeout_seconds": _coerce_timeout_seconds(raw.get("timeout_seconds")),
            "requires_confirmation": bool(raw.get("requires_confirmation", False)),
            "detached": bool(raw.get("detached", False)),
            "max_output_chars": _coerce_max_output_chars(raw.get("max_output_chars")),
        }
        loaded.append(preset)
        seen_ids.add(preset_id)

    return loaded


def _resolve_command_cwd(cwd: Optional[str]) -> Optional[str]:
    if not cwd:
        return None

    expanded = os.path.expandvars(os.path.expanduser(cwd))
    path = Path(expanded)
    if not path.is_absolute():
        path = _repo_root / path
    return str(path)


def _format_command(command: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in command)


def _public_command_preset(preset: dict[str, Any]) -> dict[str, Any]:
    commands = [_format_command(step) for step in preset["steps"]]
    return {
        "id": preset["id"],
        "label": preset["label"],
        "description": preset["description"],
        "cwd": preset["cwd"],
        "commands": commands,
        "timeout_seconds": preset["timeout_seconds"],
        "requires_confirmation": preset["requires_confirmation"],
        "detached": preset["detached"],
    }


def _find_command_preset(preset_id: str) -> Optional[dict[str, Any]]:
    return next((preset for preset in command_presets if preset["id"] == preset_id), None)


def _truncate_command_output(output: str, max_chars: int) -> str:
    if len(output) <= max_chars:
        return output
    return f"[output truncated to last {max_chars} characters]\n{output[-max_chars:]}"


def _command_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    env["SUDO_ASKPASS"] = "/bin/false"
    return env


async def _run_command_step(
    command: list[str],
    cwd: Optional[str],
    timeout_seconds: float,
    max_output_chars: int,
) -> dict[str, Any]:
    started = time.monotonic()
    display = _format_command(command)

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_command_env(),
        )
    except FileNotFoundError as exc:
        return {
            "command": display,
            "exit_code": None,
            "timed_out": False,
            "duration_seconds": time.monotonic() - started,
            "stdout": "",
            "stderr": str(exc),
        }
    except Exception as exc:
        return {
            "command": display,
            "exit_code": None,
            "timed_out": False,
            "duration_seconds": time.monotonic() - started,
            "stdout": "",
            "stderr": f"failed_to_start: {exc}",
        }

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()

    stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
    if timed_out:
        stderr = f"{stderr}\ncommand timed out after {timeout_seconds:.1f}s".strip()

    return {
        "command": display,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "duration_seconds": time.monotonic() - started,
        "stdout": _truncate_command_output(stdout, max_output_chars),
        "stderr": _truncate_command_output(stderr, max_output_chars),
    }


def _get_vision_state() -> Optional[Dict[str, Any]]:
    if not vision_controller:
        return None
    return vision_controller.get_status()



# ============================================================================
# Command Preset Endpoints
# ============================================================================

@app.get("/api/command-presets")
async def list_command_presets():
    """List operator command presets configured for this supervisor."""
    return {
        "presets": [_public_command_preset(preset) for preset in command_presets]
    }


@app.post("/api/command-presets/{preset_id}/run")
async def run_command_preset(
    preset_id: str,
    request: Optional[CommandPresetRunRequest] = Body(default=None),
):
    """Run one configured command preset by id."""
    preset = _find_command_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail=f"Unknown command preset: {preset_id}")

    confirmed = request.confirmed if request else False
    if preset["requires_confirmation"] and not confirmed:
        raise HTTPException(status_code=400, detail="Command preset requires confirmation")

    cwd = _resolve_command_cwd(preset.get("cwd"))
    if cwd and not Path(cwd).exists():
        raise HTTPException(status_code=400, detail=f"Working directory does not exist: {cwd}")

    if preset["detached"]:
        command = preset["steps"][0]
        started = time.monotonic()
        try:
            subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=_command_env(),
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed_to_start: {exc}") from exc

        return {
            "preset_id": preset["id"],
            "status": "accepted",
            "exit_code": None,
            "duration_seconds": time.monotonic() - started,
            "stdout": "",
            "stderr": "",
            "steps": [
                {
                    "command": _format_command(command),
                    "exit_code": None,
                    "timed_out": False,
                    "duration_seconds": time.monotonic() - started,
                    "stdout": "",
                    "stderr": "",
                }
            ],
        }

    started = time.monotonic()
    step_results: list[dict[str, Any]] = []
    status = "success"

    for command in preset["steps"]:
        result = await _run_command_step(
            command=command,
            cwd=cwd,
            timeout_seconds=preset["timeout_seconds"],
            max_output_chars=preset["max_output_chars"],
        )
        step_results.append(result)

        if result["timed_out"]:
            status = "timeout"
            break
        if result["exit_code"] != 0:
            status = "failed"
            break

    stdout = "\n".join(result["stdout"] for result in step_results if result["stdout"])
    stderr = "\n".join(result["stderr"] for result in step_results if result["stderr"])
    return {
        "preset_id": preset["id"],
        "status": status,
        "exit_code": step_results[-1]["exit_code"] if step_results else None,
        "duration_seconds": time.monotonic() - started,
        "stdout": _truncate_command_output(stdout, preset["max_output_chars"]),
        "stderr": _truncate_command_output(stderr, preset["max_output_chars"]),
        "steps": step_results,
    }


# ============================================================================
# Network Manager Endpoints
# ============================================================================
#
# Lets the operator add WiFi connections via nmcli, bring them up/down, and
# remove connections that were added through this supervisor.  Connections
# that existed before (wrobo, COMTRADE GUEST, …) are visible but cannot be
# deleted through these endpoints.
#
# Security design
# ───────────────
# • Every user-supplied string (connection name, SSID, password) is validated
#   with a strict allow-list regex BEFORE it is passed to nmcli.
# • Commands are built as argv lists and executed with asyncio.create_subprocess_exec
#   (never shell=True), so injection via argument values is structurally impossible.
# • Passwords are never logged; they are replaced with "***" in the audit log.
# • Only connections registered in the managed-connections registry may be deleted.
# • The registry survives restarts (stored as JSON next to config.yaml).

import re as _re
import threading as _threading

_NM_REGISTRY_PATH = _repo_root / "robot_supervisor_v2" / "managed_connections.json"
_NM_REGISTRY_LOCK = _threading.Lock()

# Strict allow-list patterns — connection name, SSID, and WPA-PSK password
_NM_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-\.]{0,62}$")  # 1-63 chars
_NM_SSID_RE = _re.compile(r"^[\x20-\x7E]{1,32}$")   # printable ASCII, ≤32 (WiFi spec)
_NM_PASS_RE = _re.compile(r"^[\x20-\x7E]{8,63}$")   # WPA-PSK: 8-63 printable ASCII


class NetworkConnectionAddRequest(BaseModel):
    name: str           # nmcli connection name
    ssid: str           # WiFi SSID to connect to
    password: str = ""  # WPA-PSK passphrase; empty string = open network


def _nm_load_registry() -> set:
    """Return the set of connection names managed by the supervisor."""
    with _NM_REGISTRY_LOCK:
        if not _NM_REGISTRY_PATH.exists():
            return set()
        try:
            data = json.loads(_NM_REGISTRY_PATH.read_text())
            if isinstance(data, list):
                return {s for s in data if isinstance(s, str)}
        except Exception:
            pass
        return set()


def _nm_save_registry(names: set) -> None:
    with _NM_REGISTRY_LOCK:
        _NM_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _NM_REGISTRY_PATH.write_text(json.dumps(sorted(names), indent=2))


def _nm_validate_name(name: str) -> None:
    """Raise HTTPException 400 if the connection name contains unsafe characters."""
    if not _NM_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid connection name. Use 1–63 characters: letters, digits, "
                "spaces, hyphens, underscores, or periods. Must start with a letter or digit."
            ),
        )


def _nm_validate_ssid(ssid: str) -> None:
    if not _NM_SSID_RE.match(ssid):
        raise HTTPException(
            status_code=400,
            detail="Invalid SSID. Use 1–32 printable ASCII characters.",
        )


def _nm_validate_password(password: str) -> None:
    if password and not _NM_PASS_RE.match(password):
        raise HTTPException(
            status_code=400,
            detail="Invalid password. WPA-PSK password must be 8–63 printable ASCII characters.",
        )


_NM_WIFI_TYPES = frozenset({"802-11-wireless", "wifi"})


def _nm_assert_wifi(conn_name: str) -> None:
    """
    Raise HTTPException if conn_name is not a WiFi connection.

    Uses `nmcli -t -f NAME,TYPE connection show` (the same table-mode call
    used by the list endpoint — this is the form that reliably returns TYPE
    as a short token like '802-11-wireless').  The per-connection detail view
    (`nmcli connection show <name>`) uses the full property path
    'connection.type' which the -g/--get-values shorthand does not resolve
    the same way, causing false 404s.

    No sudo required — read-only list query.
    """
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5.0,
            check=False,
        )
        raw = result.stdout.strip()
    except Exception:
        raise HTTPException(status_code=500, detail="Could not query NetworkManager connection list.")

    for line in raw.splitlines():
        fields = _split_nmcli_fields(line)
        if len(fields) >= 2 and fields[0] == conn_name:
            conn_type = fields[1].strip().lower()
            if conn_type not in _NM_WIFI_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"'{conn_name}' is a '{conn_type}' connection, not WiFi. "
                        "Only 802-11-wireless connections may be controlled here."
                    ),
                )
            return  # found and is WiFi — proceed

    raise HTTPException(
        status_code=404,
        detail=f"Connection '{conn_name}' not found in NetworkManager.",
    )


async def _nm_run(args: list, timeout: float = 20.0) -> dict:
    """Run an nmcli command and return {exit_code, stdout, stderr}."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_command_env(),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return {"exit_code": None, "stdout": "", "stderr": "timed out"}
    except FileNotFoundError:
        return {"exit_code": None, "stdout": "", "stderr": "nmcli not found"}

    return {
        "exit_code": process.returncode,
        "stdout": stdout_b.decode(errors="replace").strip(),
        "stderr": stderr_b.decode(errors="replace").strip(),
    }


@app.get("/api/network/connections")
async def list_network_connections():
    """List all NetworkManager connections, flagging supervisor-managed ones."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5.0,
            check=False,
        )
        raw = result.stdout.strip()
    except Exception:
        raw = ""

    managed = _nm_load_registry()
    connections = []
    for line in raw.splitlines():
        fields = _split_nmcli_fields(line)
        if len(fields) < 4:
            continue
        name = fields[0]
        connections.append({
            "name": name,
            "uuid": fields[1],
            "type": fields[2],
            "device": fields[3] or None,
            "managed": name in managed,
        })

    return {"connections": connections, "managed_names": sorted(managed)}


@app.get("/api/network/scan")
async def scan_wifi_networks(rescan: bool = False):
    """
    List WiFi networks visible to the robot.

    Pass ?rescan=true to trigger a fresh scan (takes ~3-5 s).
    Without it the cached results from NetworkManager are returned instantly.

    Results are deduplicated by SSID, keeping the entry with the strongest signal.
    Hidden networks (empty SSID) are excluded.
    No sudo required.
    """
    rescan_arg = "yes" if rescan else "no"
    timeout = 30.0 if rescan else 5.0

    result = await _nm_run(
        ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list",
         "--rescan", rescan_arg],
        timeout=timeout,
    )

    if result["exit_code"] is None:
        raise HTTPException(status_code=500, detail=f"WiFi scan failed: {result['stderr']}")

    # Deduplicate by SSID, keep strongest signal
    best: dict = {}   # ssid -> entry dict
    for line in result["stdout"].splitlines():
        fields = _split_nmcli_fields(line)
        if len(fields) < 4:
            continue
        ssid = fields[0].strip()
        if not ssid:
            continue  # skip hidden networks
        try:
            signal = int(fields[1])
        except ValueError:
            signal = 0
        security = fields[2].strip() or "open"
        in_use = fields[3].strip() == "*"

        if ssid not in best or signal > best[ssid]["signal"]:
            best[ssid] = {
                "ssid": ssid,
                "signal": signal,
                "security": security,
                "in_use": in_use,
            }

    networks = sorted(best.values(), key=lambda x: -x["signal"])
    return {"networks": networks, "rescanned": rescan}


@app.post("/api/network/connections")
async def add_network_connection(payload: NetworkConnectionAddRequest):
    """
    Add a new WiFi connection via nmcli and register it as supervisor-managed.
    Pass an empty password string for open networks.
    """
    _nm_validate_name(payload.name)
    _nm_validate_ssid(payload.ssid)
    _nm_validate_password(payload.password)

    managed = _nm_load_registry()
    if payload.name in managed:
        raise HTTPException(
            status_code=409,
            detail=f"A connection named '{payload.name}' is already in the supervisor registry.",
        )

    cmd = [
        "sudo", "-n", "nmcli", "connection", "add",
        "type", "wifi",
        "con-name", payload.name,
        "ssid", payload.ssid,
    ]
    if payload.password:
        cmd += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", payload.password]

    # Log command with password redacted
    safe_cmd = [a if a != payload.password else "***" for a in cmd]
    logger.info("Network manager: add connection: %s", " ".join(safe_cmd))

    result = await _nm_run(cmd)
    if result["exit_code"] != 0:
        logger.warning("nmcli add failed: %s", result["stderr"])
        raise HTTPException(
            status_code=500,
            detail=f"nmcli failed: {result['stderr'] or result['stdout']}",
        )

    managed.add(payload.name)
    _nm_save_registry(managed)
    logger.info("Network manager: registered connection '%s'", payload.name)

    return {"name": payload.name, "ssid": payload.ssid, "status": "added", "stdout": result["stdout"]}


@app.post("/api/network/connections/{conn_name}/up")
async def network_connection_up(conn_name: str):
    """Bring a NetworkManager connection up (nmcli conn up). WiFi only."""
    _nm_validate_name(conn_name)
    _nm_assert_wifi(conn_name)   # hard guard — refuses eth0, docker, VPN, etc.
    logger.info("Network manager: conn up '%s'", conn_name)

    result = await _nm_run(["sudo", "-n", "nmcli", "conn", "up", conn_name])

    global _network_status_cache
    _network_status_cache = None

    if result["exit_code"] != 0:
        raise HTTPException(
            status_code=500,
            detail=f"nmcli conn up failed: {result['stderr'] or result['stdout']}",
        )
    return {"name": conn_name, "status": "up", "stdout": result["stdout"]}


@app.post("/api/network/connections/{conn_name}/down")
async def network_connection_down(conn_name: str):
    """Bring a NetworkManager connection down (nmcli conn down). WiFi only."""
    _nm_validate_name(conn_name)
    _nm_assert_wifi(conn_name)   # hard guard — refuses eth0, docker, VPN, etc.
    logger.info("Network manager: conn down '%s'", conn_name)

    result = await _nm_run(["sudo", "-n", "nmcli", "conn", "down", conn_name])

    global _network_status_cache
    _network_status_cache = None

    if result["exit_code"] != 0:
        raise HTTPException(
            status_code=500,
            detail=f"nmcli conn down failed: {result['stderr'] or result['stdout']}",
        )
    return {"name": conn_name, "status": "down", "stdout": result["stdout"]}


@app.delete("/api/network/connections/{conn_name}")
async def delete_network_connection(conn_name: str):
    """
    Delete a supervisor-managed connection.

    Only connections added via POST /api/network/connections may be deleted.
    Pre-existing connections (wrobo, COMTRADE GUEST, …) are protected.
    """
    _nm_validate_name(conn_name)

    managed = _nm_load_registry()
    if conn_name not in managed:
        raise HTTPException(
            status_code=403,
            detail=(
                f"'{conn_name}' was not added by the supervisor and cannot be removed here. "
                "Use the system network manager to delete pre-existing connections."
            ),
        )

    logger.info("Network manager: deleting connection '%s'", conn_name)
    result = await _nm_run(["sudo", "-n", "nmcli", "conn", "delete", conn_name])

    if result["exit_code"] != 0:
        raise HTTPException(
            status_code=500,
            detail=f"nmcli conn delete failed: {result['stderr'] or result['stdout']}",
        )

    managed.discard(conn_name)
    _nm_save_registry(managed)
    logger.info("Network manager: removed '%s' from registry", conn_name)

    global _network_status_cache
    _network_status_cache = None

    return {"name": conn_name, "status": "deleted"}


# ============================================================================
# Runtime Environment Endpoints
# ============================================================================

@app.get("/api/environment")
async def get_runtime_environment():
    """Return selected Azure environment and sanitized configuration summary."""
    return runtime_environment_status(restart_required=False)


@app.patch("/api/environment")
async def update_runtime_environment(payload: EnvironmentUpdateRequest):
    """Switch Azure environment and restart idle voice agent if it is running."""
    if not manual_controller:
        raise HTTPException(status_code=500, detail="Manual controller not initialized")
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    conversation_status = manual_controller.get_status()
    if conversation_status.get("state") != "idle":
        raise HTTPException(
            status_code=409,
            detail="Cannot change environment during an active or transitioning conversation.",
        )

    try:
        runtime_env = validate_runtime_environment(payload.environment)
        write_selected_environment(runtime_env.environment)
        apply_runtime_environment_to_process(runtime_env.environment)
    except EnvironmentConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    restarted_services: list[str] = []
    voice_agent = service_manager.get("voice-agent")
    if voice_agent and voice_agent.get_status().state == ServiceState.RUNNING:
        try:
            await service_manager.restart_service("voice-agent")
            restarted_services.append("voice-agent")
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Environment updated, but failed to restart voice-agent: {exc}",
            )

    return {
        **runtime_environment_status(restart_required=False),
        "restarted_services": restarted_services,
    }


# ============================================================================
# Speech Configuration Endpoints
# ============================================================================

def _speech_config_response(config: dict[str, Any], restarted_services: list[str] | None = None) -> dict[str, Any]:
    voice_agent = service_manager.get("voice-agent") if service_manager else None
    voice_status = voice_agent.get_status() if voice_agent else None
    config = {
        **config,
        "voices": _voice_options_with_active(config.get("voices") or [], config.get("active_voice")),
    }
    prompt_builder = PromptBuilder(include_examples=False)
    return {
        **config,
        "initial_greeting": prompt_builder.build_initial_greeting(),
        "goodbye_text": prompt_builder.build_goodbye_text(),
        "effective_transformations": get_custom_transformations(),
        "english_locale_active": english_locale_active(),
        "state_file": str(get_speech_state_file()),
        "service_state": voice_status.state.value if voice_status else "unregistered",
        "service_running": voice_status.state == ServiceState.RUNNING if voice_status else False,
        "restarted_services": restarted_services or [],
    }


@app.get("/api/speech/config")
async def get_speech_config():
    """Return the provider-aware voice and pronunciation configuration."""
    return _speech_config_response(load_speech_config())


@app.get("/api/speech/voices")
async def get_speech_voices():
    """Return configured provider voices without querying a legacy vendor catalog."""
    config = load_speech_config()
    return {
        "voices": _voice_options_with_active(config.get("voices") or [], config.get("active_voice")),
        "source": "configured",
        "error": None,
    }


@app.get("/api/speech/background-audio/options")
async def get_speech_background_audio_options():
    """Return built-in and uploaded options for thinking background audio."""
    return background_audio_options()


@app.post("/api/speech/background-audio/upload")
async def upload_speech_background_audio(file: UploadFile = File(...)):
    """Upload a local audio file for use as thinking background audio."""
    original_name = Path(file.filename or "audio")
    suffix = original_name.suffix.lower()
    if suffix not in ACCEPTED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio extension. Use one of: {', '.join(sorted(ACCEPTED_AUDIO_EXTENSIONS))}",
        )

    stem = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in original_name.stem
    ).strip("_")[:80] or "audio"
    filename = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
    upload_dir = get_audio_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / filename

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty")

    try:
        target.write_bytes(content)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded audio: {exc}")
    finally:
        await file.close()

    options = background_audio_options()
    uploaded = next(
        (item for item in options["uploads"] if item["filename"] == filename),
        {
            "label": filename,
            "source": str(target),
            "filename": filename,
            "size_bytes": target.stat().st_size,
        },
    )
    return {
        "file": uploaded,
        "options": options,
    }


@app.patch("/api/speech/config")
async def update_speech_config(payload: SpeechConfigUpdateRequest):
    """Save speech configuration and restart idle running voice agent."""
    if not manual_controller:
        raise HTTPException(status_code=500, detail="Manual controller not initialized")
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    conversation_status = manual_controller.get_status()
    if conversation_status.get("state") != "idle":
        raise HTTPException(
            status_code=409,
            detail="Cannot change speech settings during an active or transitioning conversation.",
        )

    try:
        payload_data = payload.model_dump()
        config = save_speech_config(payload_data)
        if payload.initial_greeting is not None or payload.goodbye_text is not None:
            prompt_service = PromptService(include_examples=False)
            active = prompt_service.get_active_selection()
            persona_slug = active.get("persona") or ""
            if persona_slug:
                english = english_voice_enabled()
                prompt_item = next(
                    item
                    for item in (
                        prompt_service.list_english_personas(include_archived=True)
                        if english
                        else prompt_service.list_personas(include_archived=True)
                    )
                    if item["slug"] == persona_slug
                )
                update_persona = (
                    prompt_service.update_english_persona
                    if english
                    else prompt_service.update_speaking_style
                )
                update_persona(
                    prompt_item["id"],
                    slug=prompt_item["slug"],
                    title=prompt_item["title"],
                    prompt_text=prompt_item["prompt_text"],
                    initial_greeting=(
                        payload.initial_greeting
                        if payload.initial_greeting is not None
                        else prompt_item["initial_greeting"]
                    ),
                    goodbye_text=(
                        payload.goodbye_text
                        if payload.goodbye_text is not None
                        else prompt_item["goodbye_text"]
                    ),
                    is_archived=prompt_item["is_archived"],
                )
            else:
                prompt_item = prompt_service.get_main_prompt()
                prompt_service.update_main_prompt(
                    slug="main",
                    title=prompt_item["title"],
                    prompt_text=prompt_item["prompt_text"],
                    initial_greeting=(
                        payload.initial_greeting
                        if payload.initial_greeting is not None
                        else prompt_item["initial_greeting"]
                    ),
                    goodbye_text=(
                        payload.goodbye_text
                        if payload.goodbye_text is not None
                        else prompt_item["goodbye_text"]
                    ),
                    is_archived=False,
                )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save speech config: {exc}")

    restarted_services: list[str] = []
    voice_agent = service_manager.get("voice-agent")
    if voice_agent and voice_agent.get_status().state == ServiceState.RUNNING:
        try:
            await service_manager.restart_service("voice-agent")
            restarted_services.append("voice-agent")
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Speech config saved, but failed to restart voice-agent: {exc}",
            )

    return _speech_config_response(config, restarted_services=restarted_services)


# ============================================================================
# Service Management Endpoints
# ============================================================================

@app.get("/api/services")
async def list_services():
    """
    List all registered services with their status.

    Returns:
        List of service status objects
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    return {
        "services": service_manager.get_all_status()
    }


@app.get("/api/services/{service_name}")
async def get_service_status(service_name: str):
    """
    Get status for a specific service.

    Args:
        service_name: Name of the service

    Returns:
        Service status object
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    service = service_manager.get(service_name)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not found")

    return service.get_status()


@app.post("/api/services/start-all")
async def start_all_services():
    """
    Start all services in dependency order.
    Services at the same level start in parallel.

    Returns:
        List of service statuses after startup
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    try:
        statuses = await service_manager.start_all()
        return {
            "status": "success",
            "services": statuses
        }
    except ServiceConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start services: {str(e)}")


@app.post("/api/services/start-speech")
async def start_speech_services():
    """
    Start only the speech-stack services using the existing per-service startup flow.

    Returns:
        Speech service statuses and any skipped services
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    try:
        result = await service_manager.start_speech_services()
        return {
            "status": "success",
            **result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start speech services: {str(e)}")


@app.post("/api/services/{service_name}/start")
async def start_service(service_name: str, start_dependencies: bool = True):
    """
    Start a specific service.

    Args:
        service_name: Name of the service
        start_dependencies: If True, start dependencies first

    Returns:
        Service status after startup
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    try:
        status = await service_manager.start_service(service_name, start_dependencies)
        return {
            "status": "success",
            "service": status
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start service: {str(e)}")


@app.post("/api/services/{service_name}/stop")
async def stop_service(service_name: str):
    """
    Stop a specific service.

    Args:
        service_name: Name of the service

    Returns:
        Service status after stopping
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    try:
        status = await service_manager.stop_service(service_name)
        return {
            "status": "success",
            "service": status
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop service: {str(e)}")


@app.post("/api/services/stop-all")
async def stop_all_services():
    """
    Stop all services in reverse dependency order.

    Returns:
        List of service statuses after stopping
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    statuses = await service_manager.stop_all()
    return {
        "status": "success",
        "services": statuses
    }


@app.post("/api/services/{service_name}/restart")
async def restart_service(service_name: str):
    """
    Restart a specific service.

    Args:
        service_name: Name of the service

    Returns:
        Service status after restart
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    try:
        # Use the manager's restart method to include log dividers
        status = await service_manager.restart_service(service_name)
        return {
            "status": "success",
            "service": status
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restart service: {str(e)}")


@app.get("/api/teleoperation/status")
async def get_teleoperation_status():
    """Get runtime state for the XR teleoperation service."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    service = service_manager.get("xr-teleop")
    if not service:
        raise HTTPException(status_code=404, detail="Service 'xr-teleop' not found")
    if not hasattr(service, "get_runtime_state"):
        raise HTTPException(status_code=400, detail="Service 'xr-teleop' does not support teleoperation runtime status")

    return service.get_runtime_state()


@app.post("/api/teleoperation/start")
async def start_teleoperation():
    """Send the supervisor-side teleoperation start command ('r')."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    service = service_manager.get("xr-teleop")
    if not service:
        raise HTTPException(status_code=404, detail="Service 'xr-teleop' not found")
    if not hasattr(service, "start_tracking"):
        raise HTTPException(status_code=400, detail="Service 'xr-teleop' does not support teleoperation control")

    try:
        result = await service.start_tracking()
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start teleoperation: {str(e)}")


@app.post("/api/teleoperation/stop")
async def stop_teleoperation():
    """Send the supervisor-side teleoperation stop command ('q')."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    service = service_manager.get("xr-teleop")
    if not service:
        raise HTTPException(status_code=404, detail="Service 'xr-teleop' not found")
    if not hasattr(service, "stop_tracking"):
        raise HTTPException(status_code=400, detail="Service 'xr-teleop' does not support teleoperation control")

    try:
        result = await service.stop_tracking()
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop teleoperation: {str(e)}")


@app.get("/api/robot-temperature/status")
async def get_robot_temperature_status():
    """Get the latest robot temperature snapshot from the monitor service."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    state = _get_robot_temperature_state()
    if state is None:
        raise HTTPException(status_code=404, detail="Service 'robot-temperature-monitor' not found")

    return state


# ============================================================================
# Conversation Control Endpoints
# ============================================================================

async def _connect_transcript_manager(room: Optional[str]) -> None:
    if transcript_manager and room:
        try:
            logger.info("Connecting transcript manager to room %s", room)
            await transcript_manager.connect(room)
            logger.info("Transcript manager connected to room %s", room)
        except Exception:
            logger.exception("Failed to start transcript capture")
    else:
        logger.info(
            "Skipping transcript manager connection (enabled=%s, room=%s)",
            bool(transcript_manager),
            room,
        )


async def _disconnect_transcript_manager() -> None:
    if transcript_manager:
        await transcript_manager.disconnect()
        if transcript_store:
            transcript_store.clear()


async def _finalize_dispatch_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if result["status"] == "operation_in_progress":
        raise HTTPException(status_code=409, detail=result["message"])
    if result["status"] == "already_engaged":
        return result
    if result["status"] == "success":
        await _connect_transcript_manager(result.get("room"))
        return result
    raise HTTPException(status_code=500, detail=f"Unknown status: {result['status']}")


async def _finalize_wrap_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if result["status"] == "operation_in_progress":
        raise HTTPException(status_code=409, detail=result["message"])
    if result["status"] == "already_idle":
        return result
    if result["status"] == "success":
        await _disconnect_transcript_manager()
        return result
    raise HTTPException(status_code=500, detail=f"Unknown status: {result['status']}")


def _spawn_background_task(coro: "asyncio.Future[Any]", task_name: str) -> None:
    task = asyncio.create_task(coro, name=task_name)

    def _log_task_failure(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception:
            logger.exception("Background task failed: %s", task_name)

    task.add_done_callback(_log_task_failure)


async def _process_vision_detected_event(event_sequence: int) -> None:
    if not vision_controller:
        return

    result = await vision_controller.complete_person_detected(event_sequence)
    conversation_result = result.get("conversation")
    if result["status"] == "person_detected" and isinstance(conversation_result, dict):
        await _finalize_dispatch_result(conversation_result)


async def _process_vision_left_event(
    event_sequence: int,
    previous_track_id: Optional[str],
    process_delay_s: float = 0.0,
) -> None:
    if not vision_controller:
        return

    if process_delay_s > 0:
        await asyncio.sleep(process_delay_s)

    result = await vision_controller.complete_person_left(
        event_sequence,
        previous_track_id=previous_track_id,
    )
    conversation_result = result.get("conversation")
    if result["status"] == "person_left" and isinstance(conversation_result, dict):
        await _finalize_wrap_result(conversation_result)


@app.post("/api/conversation/dispatch")
async def dispatch_conversation(
    room: Optional[str] = None,
    agent_implementation: Optional[str] = None
):
    """
    Dispatch agent to start a conversation.
    Services must already be running.

    Args:
        room: LiveKit room name (default: from LIVEKIT_ROOM env var or "main-room")
        agent_implementation: Local agent implementation to select before dispatch

    Returns:
        Dispatch result with job_id
    """
    if not manual_controller:
        raise HTTPException(status_code=500, detail="Manual controller not initialized")

    try:
        result = await manual_controller.dispatch_conversation(room, agent_implementation)
        return await _finalize_dispatch_result(result)

    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to dispatch conversation: {str(e)}")


@app.post("/api/conversation/wrap")
async def wrap_conversation(graceful: bool = False):
    """
    Wrap (end) current conversation.
    Services remain running for quick re-dispatch.

    Args:
        graceful: If True, send goodbye event (future feature)

    Returns:
        Wrap result with duration
    """
    if not manual_controller:
        raise HTTPException(status_code=500, detail="Manual controller not initialized")

    try:
        result = await manual_controller.wrap_conversation(graceful)
        return await _finalize_wrap_result(result)

    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to wrap conversation: {str(e)}")


@app.get("/api/conversation/status")
async def get_conversation_status():
    """
    Get current conversation/engagement status.

    Returns:
        Engagement state, room, job_id, uptime, etc.
    """
    if not manual_controller:
        raise HTTPException(status_code=500, detail="Manual controller not initialized")

    return manual_controller.get_status()


# ============================================================================
# Vision Event Endpoints
# ============================================================================

@app.get("/api/vision")
async def get_vision_status():
    """Get the vision control state."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    return vision_controller.get_status()


@app.patch("/api/vision")
async def set_vision_status(enabled: bool = Body(..., embed=True)):
    """Set the operator vision toggle."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    return vision_controller.set_enabled(enabled)


@app.post("/api/vision/card-capture/request")
async def request_vision_card_capture(request: VisionCardCaptureRequest):
    """Start a request-gated card capture job for the vision service."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    vision_service = _get_vision_controller_service()
    if not vision_service.get_config().get("enable_id_scanning", False):
        raise HTTPException(status_code=403, detail="ID scanning is disabled")
    if not vision_controller.is_active():
        raise HTTPException(status_code=403, detail="Vision controller is inactive")

    result = await vision_controller.request_card_capture(
        timeout_s=request.timeout_s,
        target_type=request.target_type,
        source=request.source,
    )
    if result.get("status") == "already_active":
        raise HTTPException(status_code=409, detail=result)
    return result


@app.get("/api/vision/card-capture/{request_id}")
async def get_vision_card_capture(request_id: str):
    """Get card capture request status/result metadata."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    result = await vision_controller.get_card_capture(request_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    return result


@app.post("/api/vision/card-capture/{request_id}/cancel")
async def cancel_vision_card_capture(request_id: str):
    """Cancel an active card capture request."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    result = await vision_controller.cancel_card_capture(request_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    return result


@app.post("/api/vision/card-capture/result")
async def vision_card_capture_result(result_event: VisionCardCaptureResult):
    """Receive card capture result metadata from the vision service."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    result = await vision_controller.complete_card_capture(
        request_id=result_event.request_id,
        status=result_event.status,
        timestamp=result_event.timestamp,
        metadata=result_event.metadata,
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    if result.get("status") == "not_active":
        raise HTTPException(status_code=409, detail=result)
    return result


def _require_face_recognition_enabled() -> None:
    """Reject face endpoints unless the operator toggle is on and vision is live."""
    vision_service = _get_vision_controller_service()
    if not vision_service.get_config().get("enable_face_recognition", False):
        raise HTTPException(status_code=403, detail="Face recognition is disabled")
    if not vision_controller.is_active():
        raise HTTPException(status_code=403, detail="Vision controller is inactive")


@app.post("/api/vision/face-capture/request")
async def request_vision_face_capture(request: VisionFaceCaptureRequest):
    """Start one distance-gated face enrollment capture in the vision service."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    _require_face_recognition_enabled()

    distance = request.distance.strip().lower()
    if distance not in {"close", "far"}:
        raise HTTPException(status_code=422, detail="distance must be 'close' or 'far'")
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required to enroll a face")

    result = await vision_controller.request_face_capture(
        name=name,
        distance=distance,
        timeout_s=request.timeout_s,
    )
    if result.get("status") == "already_active":
        raise HTTPException(status_code=409, detail=result)
    return result


@app.get("/api/vision/face-capture/{request_id}")
async def get_vision_face_capture(request_id: str):
    """Get face capture request status/result metadata."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    result = await vision_controller.get_face_capture(request_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    return result


@app.post("/api/vision/face-capture/{request_id}/cancel")
async def cancel_vision_face_capture(request_id: str):
    """Cancel an active face capture request."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    result = await vision_controller.cancel_face_capture(request_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    return result


@app.post("/api/vision/face/result")
async def vision_face_capture_result(result_event: VisionFaceCaptureResult):
    """Receive face capture result metadata from the vision service.

    The vision service strips the raw embedding before posting; only the
    resulting face_id/name and quality metadata arrive here.
    """
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    result = await vision_controller.complete_face_capture(
        request_id=result_event.request_id,
        status=result_event.status,
        timestamp=result_event.timestamp,
        metadata=result_event.metadata,
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    if result.get("status") == "not_active":
        raise HTTPException(status_code=409, detail=result)
    return result


@app.post("/api/vision/face/forget")
async def request_vision_face_forget(request: VisionFaceForgetRequest):
    """Ask the vision service to delete a stored face by id or name."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    _require_face_recognition_enabled()

    face_id = (request.face_id or "").strip() or None
    name = (request.name or "").strip() or None
    if not face_id and not name:
        raise HTTPException(status_code=422, detail="Provide face_id or name to forget a face")

    try:
        return await vision_controller.request_face_forget(face_id=face_id, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/vision/face/forget/result")
async def vision_face_forget_result(result_event: VisionFaceForgetResult):
    """Receive a face-forget result from the vision service."""
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")

    result = await vision_controller.complete_face_forget(
        request_id=result_event.request_id,
        status=result_event.status,
        timestamp=result_event.timestamp,
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    if result.get("status") == "not_active":
        raise HTTPException(status_code=409, detail=result)
    return result


@app.get("/api/people/faces")
async def list_people_faces():
    if not face_identity_controller:
        raise HTTPException(status_code=500, detail="Face identity controller not initialized")
    try:
        return face_identity_controller.list_faces()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list face identities: {exc}") from exc


@app.patch("/api/people/faces/{face_id}")
async def update_people_face(face_id: str, payload: PeopleFaceUpdateRequest):
    if not face_identity_controller:
        raise HTTPException(status_code=500, detail="Face identity controller not initialized")
    try:
        item = face_identity_controller.update_face(face_id, payload.model_dump())
        return {"item": item}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/people/faces/{face_id}/image")
async def get_people_face_image(face_id: str, distance: str = Query(..., pattern="^(close|far)$")):
    if not face_identity_controller:
        raise HTTPException(status_code=500, detail="Face identity controller not initialized")
    try:
        content = face_identity_controller.get_image(face_id, distance)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not content:
        raise HTTPException(status_code=404, detail=f"No {distance} face image is stored")
    return Response(content=content, media_type="image/jpeg")


@app.delete("/api/people/faces/{face_id}")
async def delete_people_face(face_id: str):
    if not face_identity_controller:
        raise HTTPException(status_code=500, detail="Face identity controller not initialized")
    if not face_identity_controller.delete_face(face_id):
        raise HTTPException(status_code=404, detail="Face identity not found")
    return {"success": True}


@app.post("/api/vision/person_detected")
async def vision_person_detected(event: VisionPresenceEvent):
    """
    Receive a person-detected event from the vision service.
    Dispatches a conversation through the manual controller.
    """
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")
    if not event.locked:
        raise HTTPException(status_code=400, detail="person_detected event must have locked=true")

    try:
        received_at = time.time()
        receive_delay_s = (received_at - event.timestamp) if event.timestamp is not None else None
        logger.info(
            "VISION RECEIVE state=locked track_id=%s receive_delay_s=%s detect_s=%s face_s=%s total_s=%s",
            event.track_id,
            f"{receive_delay_s:.4f}" if receive_delay_s is not None else "n/a",
            event.timings.get("detect_s", "n/a"),
            event.timings.get("face_s", "n/a"),
            event.timings.get("total_s", "n/a"),
        )
        result = await vision_controller.queue_person_detected(
            track_id=event.track_id,
            timestamp=event.timestamp,
            identity=event.identity,
        )
        if result["status"] == "accepted":
            _spawn_background_task(
                _process_vision_detected_event(result["event_sequence"]),
                f"vision-person-detected-{result['event_sequence']}",
            )

        return result

    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to handle person_detected event: {str(e)}")


@app.post("/api/vision/person_left")
async def vision_person_left(event: VisionPresenceEvent):
    """
    Receive a person-left event from the vision service.
    Wraps the active conversation through the manual controller.
    """
    if not vision_controller:
        raise HTTPException(status_code=500, detail="Vision controller not initialized")
    if event.locked:
        raise HTTPException(status_code=400, detail="person_left event must have locked=false")

    try:
        received_at = time.time()
        receive_delay_s = (received_at - event.timestamp) if event.timestamp is not None else None
        logger.info(
            "VISION RECEIVE state=unlocked track_id=%s receive_delay_s=%s detect_s=%s face_s=%s total_s=%s",
            event.track_id,
            f"{receive_delay_s:.4f}" if receive_delay_s is not None else "n/a",
            event.timings.get("detect_s", "n/a"),
            event.timings.get("face_s", "n/a"),
            event.timings.get("total_s", "n/a"),
        )
        result = await vision_controller.queue_person_left(
            timestamp=event.timestamp,
            reason=event.reason,
        )
        if result["status"] == "accepted":
            _spawn_background_task(
                _process_vision_left_event(
                    result["event_sequence"],
                    result.get("previous_track_id"),
                    float(result.get("process_delay_s") or 0.0),
                ),
                f"vision-person-left-{result['event_sequence']}",
            )

        return result

    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to handle person_left event: {str(e)}")


@app.get("/api/conversation/transcript")
async def get_transcript():
    """
    Get current conversation transcript.

    Returns:
        Transcript state with entries
    """
    if not transcript_manager:
        raise HTTPException(
            status_code=503,
            detail="Transcript manager not initialized (missing LiveKit credentials)"
        )

    return transcript_manager.get_state()


@app.post("/api/conversation/command")
async def publish_agent_command(command: AgentCommandRequest):
    """
    Send a direct text/gesture command or a scripted multi-step command to the active agent.

    Mirrors the payload format used by testing_scripts/send_agent_command.py.
    """
    text = command.text.strip()
    gesture = command.gesture.strip() if command.gesture else None
    steps = [
        step
        for step in command.steps
        if step.text.strip() or step.gesture or step.pause_after_ms
    ]
    if not text and not gesture and not steps:
        raise HTTPException(status_code=400, detail="Provide text, gesture, steps, or a combination")
    if command.plain_text and not text:
        raise HTTPException(status_code=400, detail="Plain-text commands require text")
    if command.plain_text and steps:
        raise HTTPException(status_code=400, detail="Plain-text commands do not support scripted steps")

    room = command.room.strip() if command.room else None
    if not room and manual_controller:
        status = manual_controller.get_status()
        room = status.get("room")
    if not room:
        room = os.getenv("LIVEKIT_ROOM", "g1-lab")

    rag_toggle = _parse_rag_runtime_toggle(text, gesture, steps)

    try:
        response = await send_agent_command(
            AgentCommandRequest(
                text=text,
                gesture=gesture,
                force_gesture=command.force_gesture,
                steps=steps,
                room=room,
                topic=command.topic,
                plain_text=command.plain_text,
                identity=command.identity,
                name=command.name,
            ),
            room=room,
        )
        if rag_toggle is not None:
            if manual_controller:
                _sync_rag_runtime_state(manual_controller.get_status())
            _record_rag_runtime_toggle(rag_toggle)
        return response
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to publish agent command: {str(e)}")


@app.get("/api/gestures")
async def list_gestures() -> GestureCatalogResponse:
    """Return the gesture catalog for direct-command UI controls."""
    active_pool = _get_gesture_bridge_pool()
    catalog_id = _active_gesture_catalog_id()
    service_state = "unregistered"
    service_running = False

    if service_manager:
        gesture_service = service_manager.get("gesture-bridge")
        if gesture_service:
            status = gesture_service.get_status()
            service_state = status.state.value
            service_running = status.state == ServiceState.RUNNING

    return GestureCatalogResponse(
        gestures=list(get_allowed_gestures(active_pool, catalog_id)),
        all_gestures=list(get_catalog(catalog_id).names),
        active_pool=active_pool,
        available_pools=list(GESTURE_SAFETY_POOLS),
        service_state=service_state,
        service_running=service_running,
    )


@app.get("/api/gesture-bridge/config")
async def get_gesture_bridge_config():
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    gesture_bridge = service_manager.get("gesture-bridge")
    if not gesture_bridge:
        raise HTTPException(status_code=404, detail="Gesture bridge service not found")

    status = gesture_bridge.get_status()
    return {
        "safety_pool": _get_gesture_bridge_pool(),
        "available_pools": list(GESTURE_SAFETY_POOLS),
        "service_state": status.state.value,
        "service_running": status.state == ServiceState.RUNNING,
    }


@app.post("/api/gesture-bridge/config")
async def update_gesture_bridge_config(safety_pool: str):
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")
    if not manual_controller:
        raise HTTPException(status_code=500, detail="Manual controller not initialized")

    if not is_gesture_safety_pool(safety_pool):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid safety pool '{safety_pool}'. Use one of: {', '.join(GESTURE_SAFETY_POOLS)}",
        )

    conversation_status = manual_controller.get_status()
    if conversation_status.get("state") != "idle":
        raise HTTPException(
            status_code=409,
            detail="Cannot change gesture pool during an active or transitioning conversation.",
        )

    gesture_bridge = service_manager.get("gesture-bridge")
    if not gesture_bridge:
        raise HTTPException(status_code=404, detail="Gesture bridge service not found")

    voice_agent = service_manager.get("voice-agent")
    normalized_pool = normalize_gesture_safety_pool(safety_pool)

    try:
        await gesture_bridge.update_config({"safety_pool": normalized_pool})
        if voice_agent:
            await voice_agent.update_config({"gesture_safety_pool": normalized_pool})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update gesture bridge config: {str(e)}")

    gesture_status = gesture_bridge.get_status()
    return {
        "safety_pool": normalized_pool,
        "available_pools": list(GESTURE_SAFETY_POOLS),
        "service_state": gesture_status.state.value,
        "service_running": gesture_status.state == ServiceState.RUNNING,
        "voice_agent_pool": _get_voice_agent_pool(),
    }


# ============================================================================
# Agent Management Endpoints
# ============================================================================

@app.get("/api/agents")
async def list_agents():
    """
    List all available agents.

    Returns:
        List of agent metadata (name, module, path)
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    voice_agent = service_manager.get("voice-agent")
    if not voice_agent:
        raise HTTPException(status_code=404, detail="Voice agent service not found")

    # Discover agents from directory
    await voice_agent.discover_agents()

    return {
        "agents": voice_agent.get_available_agents()
    }


@app.get("/api/agents/current")
async def get_current_agent():
    """
    Get currently selected agent.

    Returns:
        Current agent metadata
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    voice_agent = service_manager.get("voice-agent")
    if not voice_agent:
        raise HTTPException(status_code=404, detail="Voice agent service not found")

    # Discover agents if not already done
    await voice_agent.discover_agents()

    # Check if an agent is selected
    current = voice_agent.get_current_agent()

    # If no agent selected, try to select the default from config
    if not current:
        selected_agent_implementation = voice_agent._config.get("selected_agent")
        if selected_agent_implementation:
            try:
                await voice_agent.set_agent(selected_agent_implementation)
                current = voice_agent.get_current_agent()
            except ValueError:
                # Default agent not found, just return None
                pass

    # If still no agent, return the first available agent as default
    if not current:
        agents = voice_agent.get_available_agents()
        if agents:
            # Use the first agent as default
            await voice_agent.set_agent(agents[0]['name'])
            current = voice_agent.get_current_agent()

    if not current:
        raise HTTPException(status_code=404, detail="No agents available")

    return current


@app.post("/api/agents/select/{agent_implementation}")
async def select_agent(agent_implementation: str):
    """
    Select an agent for the next conversation.
    Cannot change during active conversation.

    Args:
        agent_implementation: Local agent implementation to select

    Returns:
        Selected agent metadata
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    if not manual_controller:
        raise HTTPException(status_code=500, detail="Manual controller not initialized")

    # Check if conversation is active
    status = manual_controller.get_status()
    if status["state"] == "engaged":
        raise HTTPException(
            status_code=409,
            detail="Cannot change agent during active conversation. Wrap conversation first."
        )

    # Select the agent
    voice_agent = service_manager.get("voice-agent")
    if not voice_agent:
        raise HTTPException(status_code=404, detail="Voice agent service not found")

    try:
        await voice_agent.set_agent(agent_implementation)
        return voice_agent.get_current_agent()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to select agent: {str(e)}")


# ============================================================================
# Device Enumeration Endpoints
# ============================================================================

@app.get("/api/devices/audio")
async def list_audio_devices():
    """
    List available audio devices (input and output).

    Returns:
        Dict with input_devices and output_devices lists
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    audio_bridge = service_manager.get("audio-bridge")
    if not audio_bridge:
        raise HTTPException(status_code=404, detail="Audio bridge service not found")

    return audio_bridge.get_audio_devices()


@app.get("/api/devices/video")
async def list_video_devices():
    """
    List available video/camera devices.

    Returns:
        List of video device paths
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    camera_bridge = service_manager.get("camera-bridge")
    if not camera_bridge:
        return _lightweight_video_devices_payload()

    if not isinstance(camera_bridge, CameraBridgeService):
        raise HTTPException(status_code=404, detail="Camera bridge service not found")

    allow_probe = not _camera_device_owner_active()
    devices = await asyncio.to_thread(camera_bridge.get_video_devices, allow_probe=allow_probe)

    return {
        "devices": devices
    }


def _camera_bridge_config_payload(camera_bridge: CameraBridgeService) -> dict[str, Any]:
    config = camera_bridge.get_config()
    device = config.get("device", "/dev/video0")
    source = CameraBridgeService.infer_source_for_device(device, config.get("source", "opencv"))
    return {
        "device": device,
        "source": source,
        "resolution": config.get("resolution", "960x540"),
        "framerate": config.get("framerate", 15),
        "publish_fps": config.get("publish_fps"),
        "video_max_bitrate": config.get("video_max_bitrate", 1_500_000),
        "video_max_framerate": config.get("video_max_framerate"),
        "interval": config.get("interval", 3.0),
        "send_to_agent": config.get("send_to_agent", False),
        "react_to_visuals": config.get("react_to_visuals", False),
    }


def _get_vision_controller_service() -> VisionControllerService:
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    service = service_manager.get("vision-controller")
    if not isinstance(service, VisionControllerService):
        raise HTTPException(status_code=404, detail="Vision controller service not found")
    return service


def _get_video_recording_service() -> VideoRecordingService:
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    service = service_manager.get("video-recording-service")
    if not isinstance(service, VideoRecordingService):
        raise HTTPException(status_code=404, detail="Video Recording Service not found")
    return service


def _vision_controller_config_payload(
    vision_service: VisionControllerService,
) -> dict[str, Any]:
    config = vision_service.get_config()
    return {
        "camera_id": config.get("camera_id", 6),
        "camera_resolution": config.get("camera_resolution"),
        "camera_fps": config.get("camera_fps", DEFAULT_CAMERA_FPS),
        "camera_fourcc": config.get("camera_fourcc", DEFAULT_CAMERA_FOURCC),
        "camera_buffer_size": config.get("camera_buffer_size"),
        "enable_id_scanning": config.get("enable_id_scanning", False),
        "enable_face_recognition": config.get("enable_face_recognition", False),
    }


def _video_recording_config_payload(
    recording_service: VideoRecordingService,
) -> dict[str, Any]:
    config = recording_service.get_config()
    return {
        "device": config.get("device", DEFAULT_RECORDING_DEVICE),
        "resolution": config.get("resolution", DEFAULT_RECORDING_RESOLUTION),
        "framerate": config.get("framerate", DEFAULT_RECORDING_FRAMERATE),
        "output_dir": config.get("output_dir", DEFAULT_RECORDING_OUTPUT_DIR),
        "record_overlay_enabled": bool(config.get("record_overlay_enabled", False)),
        "cv_debug_enabled": bool(config.get("cv_debug_enabled", config.get("id_debug_enabled", False))),
        "cv_debug_mode": config.get("cv_debug_mode", "id_capture"),
        "id_debug_enabled": bool(config.get("id_debug_enabled", False)),
        "id_debug_overlay_enabled": bool(config.get("id_debug_overlay_enabled", False)),
        "id_debug_record_captures_enabled": bool(config.get("id_debug_record_captures_enabled", False)),
        "id_debug_mode": config.get("id_debug_mode", "capture"),
        "id_debug_capture_image_mode": config.get("id_debug_capture_image_mode", "crop"),
    }


def _sync_video_recording_debug_from_production_config(
    recording_service: VideoRecordingService,
) -> None:
    """Mirror production vision timing for local debug processing."""
    card_capture_fps = DEFAULT_CARD_CAPTURE_FPS
    detection_fps = DEFAULT_DETECTION_FPS
    if service_manager:
        vision_service = service_manager.get("vision-controller")
        if isinstance(vision_service, VisionControllerService):
            vision_config = vision_service.get_config()
            card_capture_fps = vision_config.get("card_capture_fps", DEFAULT_CARD_CAPTURE_FPS)
            detection_fps = vision_config.get("detection_fps", DEFAULT_DETECTION_FPS)
    recording_service.set_id_debug_card_capture_fps(card_capture_fps)
    recording_service.set_vision_dispatch_detection_fps(detection_fps)


async def _stop_runtime_for_video_recording_debug() -> list[str]:
    stopped: list[str] = []
    if manual_controller:
        status = manual_controller.get_status()
        if status.get("state") != "idle":
            try:
                await manual_controller.wrap_conversation(graceful=True)
                stopped.append("conversation")
            except Exception as exc:
                logger.warning("Failed to wrap conversation before CV debug mode: %s", exc)

    if not service_manager:
        return stopped

    try:
        ordered_service_names = list(reversed(service_manager._compute_dependency_order()))
    except Exception:
        ordered_service_names = [service.name for service in service_manager.list_all()]

    for service_name in ordered_service_names:
        if service_name == "video-recording-service":
            continue
        service = service_manager.get(service_name)
        if not service:
            continue
        if service.get_status().state not in {ServiceState.STARTING, ServiceState.RUNNING, ServiceState.STOPPING}:
            continue
        try:
            await service_manager.stop_service(service_name)
            stopped.append(service_name)
        except Exception as exc:
            logger.warning("Failed to stop %s before CV debug mode: %s", service_name, exc)
    return stopped


@app.get("/api/vision-controller/config")
async def get_vision_controller_config():
    """Get current Vision Controller camera configuration."""
    return _vision_controller_config_payload(_get_vision_controller_service())


@app.post("/api/vision-controller/config")
async def update_vision_controller_config(
    camera_id: Optional[str] = None,
    camera_resolution: Optional[str] = None,
    camera_fps: Optional[float] = Query(None, gt=0),
    camera_fourcc: Optional[str] = None,
    camera_buffer_size: Optional[int] = Query(None, ge=0),
    enable_id_scanning: Optional[bool] = None,
    enable_face_recognition: Optional[bool] = None,
):
    """Update Vision Controller camera configuration and restart it if running.

    Note: enable_face_recognition is applied live - the detector polls it from
    /api/vision, so toggling it does not restart the service (it is absent from
    DETECTOR_RESTART_CONFIG_KEYS).
    """
    vision_service = _get_vision_controller_service()
    updates: dict[str, Any] = {}
    if camera_id is not None:
        updates["camera_id"] = camera_id
    if camera_resolution is not None:
        updates["camera_resolution"] = camera_resolution
    if camera_fps is not None:
        updates["camera_fps"] = camera_fps
    if camera_fourcc is not None:
        updates["camera_fourcc"] = camera_fourcc
    if camera_buffer_size is not None:
        updates["camera_buffer_size"] = camera_buffer_size
    if enable_id_scanning is not None:
        updates["enable_id_scanning"] = enable_id_scanning
    if enable_face_recognition is not None:
        updates["enable_face_recognition"] = enable_face_recognition

    try:
        await service_manager.update_service_config("vision-controller", updates)
        return vision_service.get_status()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update vision controller config: {str(e)}",
        )


@app.get("/api/camera-bridge/{service_name}/config")
async def get_named_camera_bridge_config(service_name: str):
    """
    Get current camera bridge configuration.

    Returns:
        Configuration dict with device, resolution, framerate, publish_fps, and encoding settings
    """
    camera_bridge = _get_camera_bridge_service(service_name)
    return _camera_bridge_config_payload(camera_bridge)


@app.get("/api/camera-bridge/config")
async def get_camera_bridge_config():
    return await get_named_camera_bridge_config("camera-bridge")


async def _update_camera_bridge_config(
    service_name: str,
    device: Optional[str] = None,
    source: Optional[str] = None,
    resolution: Optional[str] = None,
    framerate: Optional[float] = None,
    publish_fps: Optional[float] = None,
    video_max_bitrate: Optional[int] = None,
    video_max_framerate: Optional[float] = None,
    interval: Optional[float] = None,
    send_to_agent: Optional[bool] = None,
    react_to_visuals: Optional[bool] = None,
):
    """
    Update camera bridge configuration.
    Service will automatically restart if running.

    Args:
        device: Video device path (e.g., /dev/video0)
        resolution: Resolution string (e.g., 1280x720)
        framerate: Requested camera capture frames per second
        publish_fps: LiveKit video publish frames per second
        video_max_bitrate: LiveKit video encoding max bitrate in bits per second
        video_max_framerate: LiveKit video encoding max framerate
        interval: Seconds between snapshots sent to the agent
        send_to_agent: Whether to send periodic JPEG snapshots to the agent
    Returns:
        Updated service status
    """
    camera_bridge = _get_camera_bridge_service(service_name)

    updates = {}
    if device is not None:
        updates["device"] = device
        updates["source"] = CameraBridgeService.infer_source_for_device(
            device,
            source or camera_bridge.get_config().get("source", "opencv"),
        )
    elif source is not None:
        updates["source"] = source
    if resolution is not None:
        updates["resolution"] = resolution
    if framerate is not None:
        updates["framerate"] = framerate
    if publish_fps is not None:
        updates["publish_fps"] = publish_fps
    if video_max_bitrate is not None:
        updates["video_max_bitrate"] = video_max_bitrate
    if video_max_framerate is not None:
        updates["video_max_framerate"] = video_max_framerate
    if interval is not None:
        updates["interval"] = interval
    if send_to_agent is not None:
        updates["send_to_agent"] = send_to_agent
    if react_to_visuals is not None:
        updates["react_to_visuals"] = react_to_visuals

    try:
        await service_manager.update_service_config(service_name, updates)
        return camera_bridge.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {str(e)}")


@app.post("/api/camera-bridge/{service_name}/config")
async def update_named_camera_bridge_config(
    service_name: str,
    device: Optional[str] = None,
    source: Optional[str] = None,
    resolution: Optional[str] = None,
    framerate: Optional[float] = None,
    publish_fps: Optional[float] = Query(None, gt=0),
    video_max_bitrate: Optional[int] = Query(None, gt=0),
    video_max_framerate: Optional[float] = Query(None, gt=0),
    interval: Optional[float] = None,
    send_to_agent: Optional[bool] = None,
    react_to_visuals: Optional[bool] = None,
):
    return await _update_camera_bridge_config(
        service_name=service_name,
        device=device,
        source=source,
        resolution=resolution,
        framerate=framerate,
        publish_fps=publish_fps,
        video_max_bitrate=video_max_bitrate,
        video_max_framerate=video_max_framerate,
        interval=interval,
        send_to_agent=send_to_agent,
        react_to_visuals=react_to_visuals,
    )


@app.post("/api/camera-bridge/config")
async def update_camera_bridge_config(
    device: Optional[str] = None,
    source: Optional[str] = None,
    resolution: Optional[str] = None,
    framerate: Optional[float] = None,
    publish_fps: Optional[float] = Query(None, gt=0),
    video_max_bitrate: Optional[int] = Query(None, gt=0),
    video_max_framerate: Optional[float] = Query(None, gt=0),
    interval: Optional[float] = None,
    send_to_agent: Optional[bool] = None,
    react_to_visuals: Optional[bool] = None,
):
    return await _update_camera_bridge_config(
        service_name="camera-bridge",
        device=device,
        source=source,
        resolution=resolution,
        framerate=framerate,
        publish_fps=publish_fps,
        video_max_bitrate=video_max_bitrate,
        video_max_framerate=video_max_framerate,
        interval=interval,
        send_to_agent=send_to_agent,
        react_to_visuals=react_to_visuals,
    )


@app.post("/api/camera-bridge/{service_name}/monitor-session")
async def create_camera_bridge_monitor_session(service_name: str):
    """Create a short-lived browser monitor lease for camera bridge demand."""
    camera_bridge = _get_camera_bridge_service(service_name)
    return camera_bridge.create_monitor_session()


@app.post("/api/camera-bridge/{service_name}/monitor-session/{session_id}/heartbeat")
async def heartbeat_camera_bridge_monitor_session(service_name: str, session_id: str):
    """Refresh a browser monitor lease for camera bridge demand."""
    camera_bridge = _get_camera_bridge_service(service_name)
    return camera_bridge.heartbeat_monitor_session(session_id)


@app.delete("/api/camera-bridge/{service_name}/monitor-session/{session_id}")
async def delete_camera_bridge_monitor_session(service_name: str, session_id: str):
    """Release a browser monitor lease for camera bridge demand."""
    camera_bridge = _get_camera_bridge_service(service_name)
    return camera_bridge.delete_monitor_session(session_id)


@app.get("/api/camera-bridge/{service_name}/demand")
async def get_camera_bridge_demand(service_name: str):
    """Return whether a camera bridge has active browser monitor demand."""
    camera_bridge = _get_camera_bridge_service(service_name)
    return camera_bridge.get_monitor_demand()


@app.get("/api/conversation-camera-stream/config")
async def get_conversation_camera_stream_config():
    """Legacy fallback config endpoint for the non-LiveKit MJPEG camera stream."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    conversation_stream = service_manager.get("conversation-camera-stream")
    if not conversation_stream:
        raise HTTPException(status_code=404, detail="Conversation camera stream service not found")

    config = conversation_stream.get_config()
    return {
        "device": config.get("device", "/dev/video0"),
        "resolution": config.get("resolution"),
        "framerate": config.get("framerate"),
        "jpeg_quality": config.get("jpeg_quality"),
    }


@app.post("/api/conversation-camera-stream/config")
async def update_conversation_camera_stream_config(
    device: Optional[str] = None,
    resolution: Optional[str] = None,
    framerate: Optional[float] = None,
):
    """Legacy fallback config update for the non-LiveKit MJPEG camera stream."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    conversation_stream = service_manager.get("conversation-camera-stream")
    if not conversation_stream:
        raise HTTPException(status_code=404, detail="Conversation camera stream service not found")

    updates = {}
    if device is not None:
        updates["device"] = device
    if resolution is not None:
        updates["resolution"] = resolution
    if framerate is not None:
        updates["framerate"] = framerate

    try:
        await service_manager.update_service_config("conversation-camera-stream", updates)
        return conversation_stream.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {str(e)}")


@app.get("/api/video/stream")
async def stream_video(
    device: Optional[str] = Query(None, description="Video device path (e.g., /dev/video0)"),
    width: Optional[int] = Query(None, ge=1),
    height: Optional[int] = Query(None, ge=1),
    framerate: Optional[float] = Query(None, gt=0),
    quality: Optional[int] = Query(None, ge=10, le=95),
):
    """
    Legacy fallback MJPEG stream endpoint.

    Frontend camera monitoring should use the LiveKit camera publication from
    Camera Bridge. This endpoint remains for backward compatibility and manual
    fallback debugging.

    Returns:
        multipart/x-mixed-replace MJPEG stream
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    conversation_stream = service_manager.get("conversation-camera-stream")
    if not conversation_stream:
        raise HTTPException(status_code=404, detail="Conversation camera stream service not found")
    if conversation_stream.get_status().state != ServiceState.RUNNING:
        raise HTTPException(status_code=409, detail="Start Conversation Camera Stream service first")

    running_camera_bridges = [
        service.display_name
        for service in _list_camera_bridge_services()
        if service.get_status().state == ServiceState.RUNNING
    ]
    if running_camera_bridges:
        raise HTTPException(
            status_code=409,
            detail=(
                "Conversation Camera Stream is exclusive with camera bridge services. "
                f"Stop {', '.join(running_camera_bridges)} first."
            ),
        )

    stream_service_config = conversation_stream.get_config()
    default_device = stream_service_config.get("device", "/dev/video0")
    default_resolution = stream_service_config.get("resolution")
    default_framerate = stream_service_config.get("framerate")
    default_quality = int(stream_service_config.get("jpeg_quality", 85))

    selected_device = device or default_device

    if width is None or height is None:
        try:
            cfg_width, cfg_height = parse_resolution(default_resolution)
        except ValueError:
            cfg_width, cfg_height = None, None
    else:
        cfg_width, cfg_height = width, height

    stream_cfg = StreamConfig(
        device=selected_device,
        width=cfg_width,
        height=cfg_height,
        framerate=framerate or default_framerate,
        jpeg_quality=quality if quality is not None else default_quality,
    )

    try:
        lease_id, generation = await conversation_stream.begin_stream()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    async def guarded_stream():
        try:
            async for chunk in mjpeg_stream(stream_cfg):
                if not conversation_stream.is_stream_active(lease_id, generation):
                    break
                conversation_stream.note_stream_frame(lease_id, generation)
                yield chunk
                if not conversation_stream.is_stream_active(lease_id, generation):
                    break
        except Exception as exc:
            conversation_stream.register_stream_error(str(exc))
            raise
        finally:
            await conversation_stream.end_stream(lease_id)

    return StreamingResponse(
        guarded_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/video-recording-service/config")
async def get_video_recording_config():
    """Get Video Recording Service camera and output configuration."""
    return _video_recording_config_payload(_get_video_recording_service())


@app.post("/api/video-recording-service/config")
async def update_video_recording_config(
    device: Optional[str] = None,
    resolution: Optional[str] = None,
    framerate: Optional[float] = Query(None, gt=0),
    output_dir: Optional[str] = None,
    record_overlay_enabled: Optional[bool] = None,
    cv_debug_enabled: Optional[bool] = None,
    cv_debug_mode: Optional[str] = None,
    id_debug_enabled: Optional[bool] = None,
    id_debug_overlay_enabled: Optional[bool] = None,
    id_debug_record_captures_enabled: Optional[bool] = None,
    id_debug_mode: Optional[str] = None,
    id_debug_capture_image_mode: Optional[str] = None,
):
    """Update Video Recording Service config and restart it if running."""
    recording_service = _get_video_recording_service()
    updates: dict[str, Any] = {}
    if device is not None:
        updates["device"] = device
    if resolution is not None:
        updates["resolution"] = resolution
    if framerate is not None:
        updates["framerate"] = framerate
    if output_dir is not None:
        updates["output_dir"] = output_dir
    if record_overlay_enabled is not None:
        updates["record_overlay_enabled"] = record_overlay_enabled
    if cv_debug_enabled is not None:
        updates["cv_debug_enabled"] = cv_debug_enabled
    if cv_debug_mode is not None:
        if cv_debug_mode not in {"id_capture", "portrait_edges", "vision_dispatch"}:
            raise HTTPException(status_code=400, detail="cv_debug_mode must be id_capture, portrait_edges, or vision_dispatch")
        updates["cv_debug_mode"] = cv_debug_mode
    if id_debug_enabled is not None:
        updates["id_debug_enabled"] = id_debug_enabled
        updates["cv_debug_enabled"] = id_debug_enabled
        if id_debug_enabled is False:
            updates["id_debug_overlay_enabled"] = False
            updates["id_debug_record_captures_enabled"] = False
    if id_debug_overlay_enabled is not None:
        updates["id_debug_overlay_enabled"] = id_debug_overlay_enabled
    if id_debug_record_captures_enabled is not None:
        updates["id_debug_record_captures_enabled"] = id_debug_record_captures_enabled
    if id_debug_mode is not None:
        if id_debug_mode not in {"capture", "portrait"}:
            raise HTTPException(status_code=400, detail="id_debug_mode must be capture or portrait")
        updates["id_debug_mode"] = id_debug_mode
        updates["cv_debug_mode"] = "portrait_edges" if id_debug_mode == "portrait" else "id_capture"
    if id_debug_capture_image_mode is not None:
        if id_debug_capture_image_mode not in {"crop", "whole"}:
            raise HTTPException(status_code=400, detail="id_debug_capture_image_mode must be crop or whole")
        updates["id_debug_capture_image_mode"] = id_debug_capture_image_mode

    try:
        config = recording_service.get_config()
        next_record_overlay_enabled = bool(updates.get("record_overlay_enabled", config.get("record_overlay_enabled", False)))
        if next_record_overlay_enabled:
            updates["id_debug_capture_image_mode"] = "whole"
        current_cv_debug_enabled = bool(config.get("cv_debug_enabled", config.get("id_debug_enabled", False)))
        next_cv_debug_enabled = bool(updates.get("cv_debug_enabled", current_cv_debug_enabled))
        next_cv_debug_mode = str(updates.get("cv_debug_mode", config.get("cv_debug_mode", "id_capture")))
        next_id_debug_enabled = next_cv_debug_enabled and next_cv_debug_mode in {"id_capture", "portrait_edges"}
        updates["id_debug_enabled"] = next_id_debug_enabled
        updates["id_debug_mode"] = "portrait" if next_cv_debug_mode == "portrait_edges" else "capture"
        if not next_cv_debug_enabled:
            updates["id_debug_overlay_enabled"] = False
            updates["id_debug_record_captures_enabled"] = False
        else:
            updates["id_debug_overlay_enabled"] = True
        enabling_cv_debug = next_cv_debug_enabled and not current_cv_debug_enabled
        stopped_runtime = await _stop_runtime_for_video_recording_debug() if enabling_cv_debug else []
        if next_cv_debug_enabled:
            _sync_video_recording_debug_from_production_config(recording_service)
        await service_manager.update_service_config("video-recording-service", updates)
        if next_cv_debug_enabled and recording_service.get_status().state in {ServiceState.STOPPED, ServiceState.FAILED}:
            await service_manager.start_service("video-recording-service", start_dependencies=False)
        status = recording_service.runtime_status()
        status["stopped_runtime"] = stopped_runtime
        return status
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update video recording config: {exc}")


@app.get("/api/video-recording-service/status")
async def get_video_recording_status():
    """Get Video Recording Service runtime status."""
    return _get_video_recording_service().runtime_status()


@app.post("/api/video-recording-service/id-debug/reset")
async def reset_video_recording_id_debug():
    """Reset the local-only ID debug scanner state."""
    recording_service = _get_video_recording_service()
    recording_service.reset_id_debug()
    return {
        "id_debug": recording_service.id_debug_status(),
        "cv_debug": recording_service.cv_debug_status(),
    }


@app.post("/api/video-recording-service/cv-debug/reset")
async def reset_video_recording_cv_debug():
    """Reset the local-only CV debug state."""
    recording_service = _get_video_recording_service()
    recording_service.reset_cv_debug()
    return {
        "id_debug": recording_service.id_debug_status(),
        "cv_debug": recording_service.cv_debug_status(),
    }


@app.get("/api/video-recording-service/stream")
async def stream_video_recording_service():
    """Stream the Video Recording Service preview as MJPEG."""
    recording_service = _get_video_recording_service()
    if recording_service.get_status().state != ServiceState.RUNNING:
        raise HTTPException(status_code=409, detail="Start Video Recording Service first")
    return StreamingResponse(
        recording_service.mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.post("/api/video-recording-service/capture")
async def capture_video_recording_image():
    """Save the latest Video Recording Service frame as a JPEG."""
    recording_service = _get_video_recording_service()
    try:
        return {"file": recording_service.capture_image()}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to capture image: {exc}")


@app.post("/api/video-recording-service/record/start")
async def start_video_recording():
    """Start server-side MP4 recording."""
    recording_service = _get_video_recording_service()
    try:
        return {"recording": recording_service.start_recording()}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start recording: {exc}")


@app.post("/api/video-recording-service/record/stop")
async def stop_video_recording():
    """Stop server-side MP4 recording and finalize the file."""
    recording_service = _get_video_recording_service()
    try:
        return {"file": recording_service.stop_recording()}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to stop recording: {exc}")


@app.get("/api/video-recording-service/files")
async def list_video_recording_files():
    """List saved image captures and recordings."""
    return {"files": _get_video_recording_service().list_files()}


@app.get("/api/video-recording-service/files/download-all")
async def download_all_video_recording_files():
    """Download all saved image captures and recordings as a zip archive."""
    recording_service = _get_video_recording_service()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in recording_service.list_files():
            try:
                path = recording_service.resolve_file(item["id"])
            except (FileNotFoundError, ValueError):
                continue
            archive.write(path, arcname=path.name)
            metadata_path = recording_service.sidecar_metadata_for_file(item["id"])
            if metadata_path:
                archive.write(metadata_path, arcname=metadata_path.name)

    buffer.seek(0)
    filename = f"video-recording-service-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/video-recording-service/files/{file_id}/download")
async def download_video_recording_file(file_id: str):
    """Download a saved image capture or recording."""
    recording_service = _get_video_recording_service()
    try:
        path = recording_service.resolve_file(file_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return FileResponse(path, filename=path.name)


@app.get("/api/video-recording-service/files/{file_id}/preview")
async def preview_video_recording_file(file_id: str):
    """Preview a saved image capture or recording inline in the browser."""
    recording_service = _get_video_recording_service()
    try:
        path = recording_service.resolve_file(file_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@app.patch("/api/video-recording-service/files/{file_id}")
async def rename_video_recording_file(file_id: str, payload: VideoRecordingRenameRequest):
    """Rename a saved file while preserving its extension."""
    recording_service = _get_video_recording_service()
    try:
        return {"file": recording_service.rename_file(file_id, payload.filename)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"File already exists: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/video-recording-service/files/{file_id}")
async def delete_video_recording_file(file_id: str):
    """Delete a saved image capture or recording."""
    recording_service = _get_video_recording_service()
    try:
        return recording_service.delete_file(file_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/video-recording-service/files")
async def delete_all_video_recording_files():
    """Delete all saved image captures and recordings."""
    recording_service = _get_video_recording_service()
    try:
        return recording_service.delete_all_files()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/audio-bridge/config")
async def get_audio_bridge_config():
    """
    Get current audio bridge configuration.

    Returns:
        Configuration dict with default_microphone and default_speakers
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    audio_bridge = service_manager.get("audio-bridge")
    if not audio_bridge:
        raise HTTPException(status_code=404, detail="Audio bridge service not found")

    config = audio_bridge.get_config()
    return {
        "default_microphone": config.get("default_microphone"),
        "default_speakers": config.get("default_speakers"),
        "enable_rnnoise": bool(config.get("enable_rnnoise", False)),
        "suppress_input_during_playback": bool(
            config.get("suppress_input_during_playback", False)
        ),
        "playback_echo_tail_ms": int(config.get("playback_echo_tail_ms", 500)),
    }


@app.post("/api/audio-bridge/config")
async def update_audio_bridge_config(
    default_microphone: Optional[str] = None,
    default_speakers: Optional[str] = None,
    enable_rnnoise: Optional[bool] = None,
    suppress_input_during_playback: Optional[bool] = None,
    playback_echo_tail_ms: Optional[int] = None,
):
    """
    Update audio bridge device configuration.
    Service will automatically restart if running.

    Args:
        default_microphone: Audio input PortAudio index/name or Linux ALSA id, if updating
        default_speakers: Audio output PortAudio index/name or Linux ALSA id, if updating

    Returns:
        Updated service status
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    audio_bridge = service_manager.get("audio-bridge")
    if not audio_bridge:
        raise HTTPException(status_code=404, detail="Audio bridge service not found")

    # Build config updates
    updates = {}
    if default_microphone is not None:
        updates["default_microphone"] = default_microphone
    if default_speakers is not None:
        updates["default_speakers"] = default_speakers
    if enable_rnnoise is not None:
        updates["enable_rnnoise"] = enable_rnnoise
    if suppress_input_during_playback is not None:
        updates["suppress_input_during_playback"] = suppress_input_during_playback
    if playback_echo_tail_ms is not None:
        if playback_echo_tail_ms < 0 or playback_echo_tail_ms > 5000:
            raise HTTPException(
                status_code=422,
                detail="playback_echo_tail_ms must be between 0 and 5000",
            )
        updates["playback_echo_tail_ms"] = playback_echo_tail_ms

    try:
        await service_manager.update_service_config("audio-bridge", updates)
        return audio_bridge.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {str(e)}")


@app.get("/api/audio-bridge/status")
async def get_audio_bridge_status():
    """Get live audio bridge mute status."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    audio_bridge = service_manager.get("audio-bridge")
    if not audio_bridge:
        raise HTTPException(status_code=404, detail="Audio bridge service not found")

    try:
        return await audio_bridge.get_bridge_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get audio bridge status: {str(e)}")


@app.post("/api/audio-bridge/mute")
async def set_audio_bridge_mute(muted: bool):
    """Mute or unmute the running audio bridge microphone."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    audio_bridge = service_manager.get("audio-bridge")
    if not audio_bridge:
        raise HTTPException(status_code=404, detail="Audio bridge service not found")

    try:
        return await audio_bridge.set_muted(muted)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update mute state: {str(e)}")


@app.post("/api/audio-bridge/input-gain")
async def set_audio_bridge_input_gain(input_mic_gain_db: float):
    """Update the running audio bridge microphone input gain in dB."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    audio_bridge = service_manager.get("audio-bridge")
    if not audio_bridge:
        raise HTTPException(status_code=404, detail="Audio bridge service not found")

    try:
        return await audio_bridge.set_input_mic_gain_db(input_mic_gain_db)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update input mic gain: {str(e)}")


@app.post("/api/audio-bridge/output-gain")
async def set_audio_bridge_output_gain(output_speaker_gain_db: float):
    """Update running robot speaker playback gain in dB."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")
    audio_bridge = service_manager.get("audio-bridge")
    if not audio_bridge:
        raise HTTPException(status_code=404, detail="Audio bridge service not found")
    method = getattr(audio_bridge, "set_output_speaker_gain_db", None)
    if not callable(method):
        raise HTTPException(status_code=409, detail="Speaker gain is not supported by this audio bridge")
    try:
        return await method(output_speaker_gain_db)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update speaker gain: {str(e)}")


@app.post("/api/audio-bridge/remote-playback/release")
async def release_audio_bridge_remote_playback():
    """Immediately clear agent audio queued for the robot speaker."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")
    audio_bridge = service_manager.get("audio-bridge")
    if not audio_bridge:
        raise HTTPException(status_code=404, detail="Audio bridge service not found")
    try:
        return await audio_bridge.release_remote_playback()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear speaker playback: {str(e)}")


def _get_audio_bridge_aima_method(method_name: str):
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    audio_bridge = service_manager.get("audio-bridge")
    if not audio_bridge:
        raise HTTPException(status_code=404, detail="Audio bridge service not found")

    method = getattr(audio_bridge, method_name, None)
    if not callable(method):
        raise HTTPException(
            status_code=404,
            detail="AIMA management is not available for this audio bridge",
        )
    return method


@app.get("/api/audio-bridge/aima/status")
async def get_audio_bridge_aima_status():
    """Get AIMA EM audio-resource mode/status for the active audio bridge."""
    method = _get_audio_bridge_aima_method("get_aima_status")
    try:
        return await method()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get AIMA status: {str(e)}")


@app.post("/api/audio-bridge/aima/mode/audio-bridge")
async def set_audio_bridge_aima_audio_bridge_mode():
    """Switch AIMA EM to low-latency audio-bridge-ready mode."""
    method = _get_audio_bridge_aima_method("set_aima_audio_bridge_mode")
    try:
        return await method()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to switch AIMA mode: {str(e)}")


@app.post("/api/audio-bridge/aima/mode/agibot")
async def set_audio_bridge_aima_agibot_mode():
    """Restore AIMA EM apps for native Agibot behavior."""
    method = _get_audio_bridge_aima_method("set_aima_agibot_mode")
    try:
        return await method()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to switch AIMA mode: {str(e)}")


@app.post("/api/audio-bridge/aima/doctor")
async def run_audio_bridge_aima_doctor():
    """Run PC3 `aima em doctor` through the remote audio bridge manager."""
    method = _get_audio_bridge_aima_method("run_aima_doctor")
    try:
        return await method()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to run AIMA doctor: {str(e)}")


@app.get("/api/livekit/monitor-token")
async def get_livekit_monitor_token(request: Request, room: Optional[str] = None):
    """Mint a browser token for monitoring room media from the supervisor UI."""
    livekit_url = os.getenv("LIVEKIT_URL")
    livekit_api_key = os.getenv("LIVEKIT_API_KEY")
    livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")
    bridge_identity = os.getenv("AUDIO_BRIDGE_IDENTITY", "audio-streamer")

    if not livekit_url or not livekit_api_key or not livekit_api_secret:
        raise HTTPException(status_code=500, detail="LiveKit credentials are not configured")

    selected_room = room
    if not selected_room and manual_controller:
        status = manual_controller.get_status()
        selected_room = status.get("room")
    if not selected_room:
        selected_room = os.getenv("LIVEKIT_ROOM", "g1-lab")

    public_livekit_url = os.getenv("LIVEKIT_PUBLIC_URL") or livekit_url
    identity = f"supervisor-browser-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    token = (
        AccessToken(livekit_api_key, livekit_api_secret)
        .with_identity(identity)
        .with_name("Supervisor Browser Monitor")
        .with_grants(
            VideoGrants(
                room_join=True,
                room=selected_room,
                can_subscribe=True,
                can_publish=False,
            )
        )
        .to_jwt()
    )

    return {
        "token": token,
        "url": _resolve_browser_livekit_url(request, public_livekit_url),
        "room": selected_room,
        "identity": identity,
        "bridge_identity": bridge_identity,
    }


def _resolve_browser_livekit_url(request: Request, livekit_url: str) -> str:
    """Rewrite loopback LiveKit URLs so remote browsers connect back to the robot."""
    parsed = urlsplit(livekit_url)
    hostname = parsed.hostname

    if not hostname or not _is_loopback_host(hostname):
        return _normalize_livekit_ws_scheme(livekit_url)

    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto")

    request_host = forwarded_host or request.headers.get("host") or request.url.netloc
    request_scheme = forwarded_proto or request.url.scheme

    # Development/reverse proxies may replace Host with their loopback target.
    # In that case the browser Origin is the only address the browser itself can
    # use. Never replace a real API host with Origin: a localhost Vite UI may be
    # calling a supervisor on a different robot host directly.
    origin = request.headers.get("origin")
    origin_parts = urlsplit(origin) if origin else None
    request_hostname = urlsplit(f"//{request_host}").hostname if request_host else None
    if (
        origin_parts
        and origin_parts.hostname
        and request_hostname
        and _is_loopback_host(request_hostname)
        and not _is_loopback_host(origin_parts.hostname)
    ):
        request_host = origin_parts.hostname
        request_scheme = origin_parts.scheme or request_scheme

    if not request_host:
        return _normalize_livekit_ws_scheme(livekit_url)

    browser_host = urlsplit(f"//{request_host}").hostname or request_host
    if ":" in browser_host and not browser_host.startswith("["):
        browser_host = f"[{browser_host}]"

    netloc = browser_host
    if parsed.port:
        netloc = f"{browser_host}:{parsed.port}"

    scheme = "wss" if request_scheme == "https" else "ws"
    rewritten = urlunsplit((scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return _normalize_livekit_ws_scheme(rewritten)


def _is_loopback_host(hostname: str) -> bool:
    if hostname in {"localhost", "::1"}:
        return True

    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _normalize_livekit_ws_scheme(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme == "http":
        return urlunsplit(("ws", parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    if parsed.scheme == "https":
        return urlunsplit(("wss", parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    return url


@app.get("/api/camera-bridges/stream-info")
async def list_camera_bridge_stream_info():
    """Return room/identity information for all camera bridge services."""
    return {
        "streams": [
            _camera_bridge_stream_info_payload(service)
            for service in _list_camera_bridge_services()
        ]
    }


@app.get("/api/camera-bridge/{service_name}/stream-info")
async def get_named_camera_bridge_stream_info(service_name: str):
    """Return room/identity information for a specific camera bridge service."""
    camera_bridge = _get_camera_bridge_service(service_name)
    return _camera_bridge_stream_info_payload(camera_bridge)


@app.get("/api/camera-bridge/stream-info")
async def get_camera_bridge_stream_info():
    return await get_named_camera_bridge_stream_info("camera-bridge")


# ============================================================================
# Service Log Endpoints
# ============================================================================

@app.get("/api/services/{service_name}/logs")
async def get_service_logs(
    service_name: str,
    lines: int = 100
):
    """
    Get recent logs for a specific service.

    Args:
        service_name: Name of the service
        lines: Number of lines to retrieve from end of log (default 100)

    Returns:
        Log content as plain text
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    service = service_manager.get(service_name)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not found")

    log_path = service.get_log_path()
    custom_tail_logs = service.__class__.tail_logs is not BaseService.tail_logs
    if not log_path and not custom_tail_logs:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' does not have logs")

    try:
        logs = await service.tail_logs(lines)
        return {
            "service": service_name,
            "log_path": log_path or "remote",
            "lines": lines,
            "content": logs
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {str(e)}")


@app.get("/api/services/{service_name}/logs/stream")
async def stream_service_logs(service_name: str):
    """
    Stream logs in real-time using Server-Sent Events.

    Args:
        service_name: Name of the service

    Returns:
        SSE stream of new log lines
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    service = service_manager.get(service_name)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not found")

    log_path = service.get_log_path()
    custom_tail_logs = service.__class__.tail_logs is not BaseService.tail_logs
    if not log_path and custom_tail_logs:
        async def generate_remote_log_stream():
            """Poll service-managed remote logs and yield changes as SSE events."""
            previous_logs = ""
            try:
                initial_logs = await service.tail_logs(50)
                previous_logs = initial_logs
                if initial_logs:
                    yield f"data: {json.dumps({'type': 'initial', 'content': initial_logs})}\n\n"

                while True:
                    await asyncio.sleep(1.0)

                    if service.get_status().state == ServiceState.STOPPED:
                        yield f"data: {json.dumps({'type': 'stopped'})}\n\n"
                        break

                    current_logs = await service.tail_logs(200)
                    if current_logs == previous_logs:
                        continue

                    if current_logs.startswith(previous_logs):
                        new_content = current_logs[len(previous_logs):]
                        if new_content:
                            yield f"data: {json.dumps({'type': 'line', 'content': new_content})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'initial', 'content': current_logs})}\n\n"

                    previous_logs = current_logs

            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            generate_remote_log_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    if not log_path or not Path(log_path).exists():
        raise HTTPException(status_code=404, detail=f"Log file not found for '{service_name}'")

    async def generate_log_stream():
        """Tail log file and yield new lines as SSE events."""
        try:
            # Send initial batch of recent logs
            initial_logs = await service.tail_logs(50)
            if initial_logs:
                yield f"data: {json.dumps({'type': 'initial', 'content': initial_logs})}\n\n"

            # Follow log file for new lines
            with open(log_path, 'r') as f:
                # Seek to end of file
                f.seek(0, 2)

                while True:
                    line = f.readline()
                    if line:
                        # New line available
                        yield f"data: {json.dumps({'type': 'line', 'content': line})}\n\n"
                    else:
                        # No new line, wait briefly
                        await asyncio.sleep(0.5)

                        # Check if service stopped (log file closed)
                        if service.get_status().state == ServiceState.STOPPED:
                            yield f"data: {json.dumps({'type': 'stopped'})}\n\n"
                            break

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate_log_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# ============================================================================
# Knowledge Management Endpoints
# ============================================================================

def _rag_base_url() -> str:
    return (
        os.getenv("RAG_SERVICE_BASE_URL")
        or os.getenv("RAG_EXTERNAL_API_URL")
        or "http://127.0.0.1:8098"
    ).rstrip("/")

def _qdrant_base_url() -> str:
    return os.getenv("RAG_QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")

def _rag_timeout() -> httpx.Timeout:
    return httpx.Timeout(60.0, connect=5.0)

def _knowledge_slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or "index"

def _normalize_knowledge_index(item: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(item)
    normalized.setdefault("description", "")
    normalized.setdefault("kind", "managed")
    normalized.setdefault("storage_path", None)
    slug = str(normalized.get("slug", "")).strip()
    collection_name = str(normalized.get("collection_name") or slug).strip()
    normalized["slug"] = slug
    normalized["collection_name"] = collection_name
    normalized["immutable"] = slug == MAIN_KNOWLEDGE_INDEX_SLUG or bool(normalized.get("immutable"))
    return normalized

async def _qdrant_collections() -> list[str]:
    url = f"{_qdrant_base_url()}/collections"
    try:
        async with httpx.AsyncClient(timeout=_rag_timeout()) as client:
            response = await client.get(url)
    except httpx.RequestError as exc:
        logger.warning("Qdrant collection discovery failed at %s: %s", url, exc)
        return []

    if response.status_code >= 400:
        logger.warning("Qdrant collection discovery failed: HTTP %s", response.status_code)
        return []

    try:
        payload = response.json()
    except Exception as exc:
        logger.warning("Qdrant collection discovery returned invalid JSON: %s", exc)
        return []

    collections = payload.get("result", {}).get("collections", [])
    names = [str(item.get("name")) for item in collections if item.get("name")]
    return sorted(names)

async def _knowledge_indexes_state() -> Dict[str, Any]:
    raw_state = await _rag_request("GET", "/indexes")
    indexes = [
        _normalize_knowledge_index(item)
        for item in raw_state.get("indexes", [])
        if isinstance(item, dict)
    ]

    collection_names = {item["collection_name"] for item in indexes}
    slugs = {item["slug"] for item in indexes}
    for collection_name in await _qdrant_collections():
        if collection_name in collection_names:
            continue

        slug = _knowledge_slugify(collection_name)
        base_slug = slug
        suffix = 2
        while slug in slugs:
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        indexes.append(
            {
                "slug": slug,
                "title": collection_name.replace("_", " ").replace("-", " ").title(),
                "description": "Discovered from available Qdrant collections",
                "kind": "external",
                "collection_name": collection_name,
                "storage_path": None,
                "immutable": slug == MAIN_KNOWLEDGE_INDEX_SLUG,
            }
        )
        slugs.add(slug)
        collection_names.add(collection_name)

    fallback_active_slug = indexes[0]["slug"] if indexes else ""
    active_slug = str(raw_state.get("active_slug") or fallback_active_slug)
    query_slugs = raw_state.get("query_slugs")
    if not isinstance(query_slugs, list):
        query_slugs = _knowledge_query_slugs or ([active_slug] if active_slug else [])

    available_slugs = {item["slug"] for item in indexes}
    normalized_query_slugs: list[str] = []
    for slug in query_slugs:
        slug = str(slug).strip()
        if slug in available_slugs and slug not in normalized_query_slugs:
            normalized_query_slugs.append(slug)
    if not normalized_query_slugs and active_slug in available_slugs:
        normalized_query_slugs = [active_slug]

    return {
        "active_slug": active_slug,
        "query_slugs": normalized_query_slugs,
        "indexes": indexes,
    }

def _upstream_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict) and payload.get("detail"):
            return str(payload["detail"])
    except Exception:
        pass

    text = response.text.strip()
    if text:
        return text[:500]
    return f"HTTP {response.status_code}"

async def _rag_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    files: Optional[Any] = None,
    json_body: Optional[Any] = None,
) -> Any:
    url = f"{_rag_base_url()}{path}"

    try:
        async with httpx.AsyncClient(timeout=_rag_timeout()) as client:
            response = await client.request(
                method=method,
                url=url,
                params=params,
                files=files,
                json=json_body,
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"RAG service unreachable at {url}: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=_upstream_error_detail(response),
        )

    if not response.content:
        return {}

    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


@app.get("/api/knowledge")
async def get_knowledge():
    return await _rag_request("GET", "/knowledge")

@app.post("/api/knowledge/upload")
async def upload_knowledge(file: UploadFile = File(...)):
    content = await file.read()
    files = {
        "file": (
            file.filename or "upload.pdf",
            content,
            file.content_type or "application/pdf",
        )
    }
    return await _rag_request("POST", "/knowledge/upload", files=files)


@app.get("/api/knowledge/{doc_id}/file")
async def get_knowledge_file(doc_id: str):
    """Proxy a managed PDF inline without exposing RAG storage paths."""
    url = f"{_rag_base_url()}/knowledge/{doc_id}/file"
    try:
        async with httpx.AsyncClient(timeout=_rag_timeout()) as client:
            response = await client.get(url)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"RAG service unreachable at {url}: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_upstream_error_detail(response))
    headers = {}
    disposition = response.headers.get("content-disposition")
    if disposition:
        headers["Content-Disposition"] = disposition
    return StreamingResponse(
        iter([response.content]),
        media_type=response.headers.get("content-type", "application/pdf"),
        headers=headers,
    )


@app.delete("/api/knowledge/{doc_id}")
async def delete_knowledge(doc_id: str):
    return await _rag_request("DELETE", f"/knowledge/{doc_id}")

@app.post("/api/knowledge/index")
async def reindex_knowledge():
    return await _rag_request("POST", "/knowledge/index")

@app.get("/api/knowledge/search")
async def search_knowledge(
    query: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=50),
):
    return await _rag_request(
        "GET",
        "/knowledge/search",
        params={"query": query, "limit": limit},
    )

@app.get("/api/knowledge/health")
async def knowledge_health():
    return await _rag_request("GET", "/health")

@app.get("/api/knowledge/indexes")
async def get_knowledge_indexes():
    return await _knowledge_indexes_state()


@app.get("/api/knowledge/options")
async def get_knowledge_options():
    indexes = await _knowledge_indexes_state()
    active = await _rag_request("GET", "/indexes/active")
    if isinstance(active, dict):
        active = _normalize_knowledge_index(active)
    return {
        "options": indexes,
        "active": active,
    }


@app.get("/api/knowledge/active")
async def get_active_knowledge_index():
    active = await _rag_request("GET", "/indexes/active")
    if isinstance(active, dict):
        return _normalize_knowledge_index(active)
    return active


@app.post("/api/knowledge/indexes/create")
async def create_knowledge_index(payload: Dict[str, Any]):
    slug = str(payload.get("slug", "")).strip()
    if slug == MAIN_KNOWLEDGE_INDEX_SLUG:
        raise HTTPException(status_code=409, detail="The main knowledge index is protected and cannot be created or changed")

    created = await _rag_request(
        "POST",
        "/indexes/create",
        json_body=payload,
    )
    if isinstance(created, dict):
        return _normalize_knowledge_index(created)
    return created


@app.delete("/api/knowledge/indexes/{index_slug}")
async def delete_knowledge_index(index_slug: str):
    if index_slug == MAIN_KNOWLEDGE_INDEX_SLUG:
        raise HTTPException(status_code=409, detail="The main knowledge index cannot be deleted")

    state = await _knowledge_indexes_state()
    profile = next((item for item in state["indexes"] if item["slug"] == index_slug), None)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown knowledge index: {index_slug}")
    if profile.get("immutable"):
        raise HTTPException(status_code=409, detail=f"Knowledge index '{index_slug}' is protected and cannot be deleted")

    try:
        return await _rag_request("DELETE", f"/indexes/{index_slug}")
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

    collection_name = profile["collection_name"]
    delete_url = f"{_qdrant_base_url()}/collections/{collection_name}"
    deleted_collection = False
    try:
        async with httpx.AsyncClient(timeout=_rag_timeout()) as client:
            response = await client.delete(delete_url)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant unreachable at {delete_url}: {exc}") from exc

    if response.status_code not in {200, 202, 404}:
        raise HTTPException(status_code=response.status_code, detail=_upstream_error_detail(response))
    deleted_collection = response.status_code != 404

    global _knowledge_query_slugs
    if _knowledge_query_slugs:
        _knowledge_query_slugs = [slug for slug in _knowledge_query_slugs if slug != index_slug]

    next_state = await _knowledge_indexes_state()
    active = next((item for item in next_state["indexes"] if item["slug"] == next_state["active_slug"]), None)
    return {
        "success": True,
        "deleted": profile,
        "active": active,
        "deleted_collection": deleted_collection,
        "deleted_storage": False,
    }


@app.post("/api/knowledge/query-indexes")
async def update_knowledge_query_indexes(payload: Dict[str, Any]):
    state = await _knowledge_indexes_state()
    available_slugs = {item["slug"] for item in state["indexes"]}
    slugs: list[str] = []
    for raw_slug in payload.get("slugs", []):
        slug = str(raw_slug).strip()
        if slug and slug not in slugs:
            slugs.append(slug)

    if not slugs:
        raise HTTPException(status_code=400, detail="At least one knowledge index must be selected for RAG queries")

    unknown_slugs = [slug for slug in slugs if slug not in available_slugs]
    if unknown_slugs:
        raise HTTPException(status_code=404, detail=f"Unknown knowledge indexes: {', '.join(unknown_slugs)}")

    try:
        return await _rag_request(
            "POST",
            "/indexes/query",
            json_body={"slugs": slugs},
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

    global _knowledge_query_slugs
    _knowledge_query_slugs = slugs
    next_state = await _knowledge_indexes_state()
    next_state["query_slugs"] = slugs
    return next_state


@app.post("/api/knowledge/update")
async def update_knowledge_index(index_slug: str):
    active = await _rag_request(
        "POST",
        "/indexes/activate",
        json_body={"slug": index_slug},
    )
    if isinstance(active, dict):
        active = _normalize_knowledge_index(active)
    return {
        "success": True,
        "active": active,
    }

# ============================================================================
# System Status Endpoints
# ============================================================================

@app.get("/api/health")
async def health_check():
    """
    Basic health check endpoint.

    Returns:
        Status: ok
    """
    return {"status": "ok"}


@app.get("/api/status")
async def get_system_status():
    """
    Get comprehensive system status.

    Returns:
        Services status, conversation status, system info
    """
    if not service_manager or not manual_controller:
        raise HTTPException(status_code=500, detail="System not initialized")

    conversation_status = manual_controller.get_status()
    return {
        "services": service_manager.get_all_status(),
        "conversation": conversation_status,
        "vision": _get_vision_state(),
        "robot_temperature": _get_robot_temperature_state(),
        "network": _get_network_state(),
        "runtime": _get_runtime_state(conversation_status),
        "robot": _robot_context_payload(),
        "system": {
            "version": "2.0.0",
            "total_services": len(service_manager.list_all())
        }
    }


# ============================================================================
# Server-Sent Events (SSE) for Real-time Updates
# ============================================================================

@app.get("/api/events")
async def event_stream():
    """
    Server-Sent Events stream for real-time status updates.

    Streams:
    - Service state changes
    - Conversation state changes
    - System events

    Returns:
        SSE stream
    """
    async def generate():
        """Generate SSE events."""
        try:
            while True:
                # Poll status every second
                # TODO: Make this event-driven instead of polling
                if service_manager and manual_controller:
                    try:
                        # Convert Pydantic models to dictionaries for JSON serialization
                        services_status = service_manager.get_all_status()

                        # Add transcript to event data
                        transcript_state = transcript_manager.get_state() if transcript_manager else {
                            "connected": False,
                            "enabled": False,
                            "entries": []
                        }

                        conversation_status = manual_controller.get_status()
                        event_data = {
                            "services": [s.model_dump() if hasattr(s, 'model_dump') else s.dict() for s in services_status],
                            "conversation": conversation_status,
                            "vision": _get_vision_state(),
                            "transcript": transcript_state,
                            "robot_temperature": _get_robot_temperature_state(),
                            "network": _get_network_state(),
                            "runtime": _get_runtime_state(conversation_status),
                            "robot": _robot_context_payload(),
                            "system": {
                                "version": "2.0.0",
                                "total_services": len(service_manager.list_all()),
                            },
                        }

                        yield f"data: {json.dumps(event_data)}\n\n"
                    except Exception as e:
                        # Log error but continue streaming
                        logger.warning("SSE event generation error: %s", e)
                        await asyncio.sleep(1)
                        continue

                await asyncio.sleep(1)
        except asyncio.CancelledError:
            # Handle graceful shutdown
            logger.info("SSE client disconnected")
            raise

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# ================================================================
# Conference Script Endpoints
# ================================================================
@app.get("/api/conference/program")
async def get_conference_program() -> ConferenceProgram:
    if not conference_script_service:
        raise HTTPException(status_code=500, detail="Conference script service not initialized")
    try:
        return conference_script_service.load_program()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load conference scripts: {e}")


@app.get("/api/conference/events")
async def list_conference_events() -> ConferenceEventListResponse:
    if not conference_script_service:
        raise HTTPException(status_code=500, detail="Conference script service not initialized")
    try:
        return conference_script_service.list_events()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list conference events: {e}")


@app.get("/api/conference/events/{event_key}/editor")
async def get_conference_event_editor(event_key: str) -> ConferenceEventDocument:
    if not conference_script_service:
        raise HTTPException(status_code=500, detail="Conference script service not initialized")
    try:
        return conference_script_service.get_event_document(event_key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load conference event: {e}")


@app.post("/api/conference/events")
async def create_conference_event(payload: ConferenceEventDocument) -> ConferenceEventDocument:
    if not conference_script_service:
        raise HTTPException(status_code=500, detail="Conference script service not initialized")
    try:
        return conference_script_service.create_event(
            payload,
            allowed_gestures=set(
                get_allowed_gestures(_get_gesture_bridge_pool(), _active_gesture_catalog_id())
            ),
        )
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create conference event: {e}")


@app.put("/api/conference/events/{event_key}")
async def update_conference_event(
    event_key: str,
    payload: ConferenceEventDocument,
) -> ConferenceEventDocument:
    if not conference_script_service:
        raise HTTPException(status_code=500, detail="Conference script service not initialized")
    try:
        return conference_script_service.update_event(
            event_key,
            payload,
            allowed_gestures=set(
                get_allowed_gestures(_get_gesture_bridge_pool(), _active_gesture_catalog_id())
            ),
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update conference event: {e}")


@app.delete("/api/conference/events/{event_key}")
async def delete_conference_event(event_key: str) -> ConferenceEventDeleteResponse:
    if not conference_script_service:
        raise HTTPException(status_code=500, detail="Conference script service not initialized")
    try:
        return conference_script_service.delete_event(event_key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete conference event: {e}")


@app.get("/api/conference/active-event")
async def get_active_conference_event() -> ConferenceActiveEventResponse:
    if not conference_script_service:
        raise HTTPException(status_code=500, detail="Conference script service not initialized")
    try:
        return conference_script_service.get_active_event()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load active conference event: {e}")


@app.post("/api/conference/active-event")
async def set_active_conference_event(
    event_key: str = Query(..., description="Conference event key"),
) -> ConferenceActiveEventResponse:
    if not conference_script_service:
        raise HTTPException(status_code=500, detail="Conference script service not initialized")
    try:
        return conference_script_service.set_active_event(event_key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update active conference event: {e}")


# ============================================================================
# Static File Serving (Frontend)
# ============================================================================

# Serve static files from dist/ (production build)
dist_dir = Path(__file__).parent.parent.parent / "dist"
if dist_dir.exists():
    # Mount static assets
    app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")

    # SPA fallback: serve index.html for all non-API routes
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """
        Serve React SPA for all routes except /api/*.
        Implements client-side routing fallback.
        """
        # API typos or routes missing from an old process must stay JSON 404s;
        # returning index.html makes the frontend fail with a misleading JSON
        # parser error ("Unexpected token '<'").
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail=f"API endpoint /{full_path} not found")

        # Check if requesting a file with extension (e.g., favicon.ico, robots.txt)
        if "." in full_path.split("/")[-1]:
            file_path = (dist_dir / full_path).resolve()
            resolved_dist = dist_dir.resolve()
            if resolved_dist in file_path.parents and file_path.is_file():
                return FileResponse(file_path)

        # Otherwise, serve index.html for SPA routing
        return FileResponse(
            dist_dir / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
else:
    logger.warning("Frontend build not found at dist/")
    logger.warning("Run 'cd frontend && npm run build' to create production build")
