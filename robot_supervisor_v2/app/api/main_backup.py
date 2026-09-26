"""
FastAPI application for Robot Supervisor V2.

Provides REST API for:
1. Service management (start/stop/status)
2. Conversation control (dispatch/wrap)
3. Agent selection
4. System status
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
import asyncio
import ipaddress
import json
import sys
import uuid
import yaml
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from dotenv import load_dotenv
from livekit.api import AccessToken, VideoGrants

_repo_root = Path(__file__).resolve().parents[3]  # go up to Humanoid Livekit/
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from livekit_config import get_prompt_builder, update_and_rebuild
from robot_services.gestures import GESTURE_NAMES

# Deterministic env loading order:
# 1) repo .env (optional)
# 2) supervisor app/.env (preferred for local supervisor runs)
_repo_env = _repo_root / ".env"
_supervisor_env = _repo_root / "robot_supervisor_v2" / "app" / ".env"
if _repo_env.exists():
    load_dotenv(_repo_env, override=False)
if _supervisor_env.exists():
    load_dotenv(_supervisor_env, override=True)

from ..services.registry import ServiceRegistry, ServiceManager
from ..services.base import ServiceState

from ..controllers.manual import ManualController
from ..models.conversation import (
    AgentCommandRequest,
    GestureCatalogResponse,
)
from ..utils.logging import archive_logs
from ..utils.agent_commands import send_agent_command
from ..transcript_store import TranscriptStore
from ..transcript_manager import TranscriptManager
from ..video_stream import StreamConfig, mjpeg_stream, parse_resolution
import os

from ..models.conference import ConferenceProgram
from ..services.conference_scripts import ConferenceScriptService


# Global state
service_manager: Optional[ServiceManager] = None
manual_controller: Optional[ManualController] = None
transcript_store: Optional[TranscriptStore] = None
transcript_manager: Optional[TranscriptManager] = None
conference_script_service: Optional[ConferenceScriptService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Handles startup and shutdown of core components.
    """
    global service_manager, manual_controller

    # Startup: Load config and register services
    print("🚀 Starting Robot Supervisor V2...")

    # Load configuration
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    if not config_path.exists():
        config_path = Path(__file__).parent.parent.parent / "config.example.yaml"
        print(f"📋 Using example config (config.yaml not found)")
    else:
        print(f"📋 Using config.yaml")

    print(f"   Config path: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Create service manager
    service_manager = ServiceManager()

    # Register all services from config
    for service_def in config["services"]:
        service = ServiceRegistry.create_service(
            service_type=service_def["type"],
            name=service_def["name"],
            config=service_def["config"]
        )
        service_manager.register(service)

    print(f"✓ Registered {len(service_manager.list_all())} services")

    # Create manual controller
    manual_controller = ManualController(service_manager)
    print("✓ Manual controller initialized")

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
        print("✓ Transcript manager initialized")
    else:
        print("⚠️  Transcript manager disabled (missing LiveKit credentials)")

    print("✓ Robot Supervisor V2 ready")

    global conference_script_service
    conference_script_service = ConferenceScriptService(_repo_root / "conference_scripts")
    print("✓ Conference script service initialized")

    
    try:
        yield
    finally:
        # Shutdown: Stop all services (always runs, even on error)
        print("\n🛑 Shutting down Robot Supervisor V2...")

        # Disconnect transcript manager
        if transcript_manager:
            try:
                await transcript_manager.disconnect()
            except Exception as e:
                print(f"  ✗ Error disconnecting transcript manager: {e}")

        # Clean up knowledge service
        try:
            #knowledge = await get_knowledge_service()
            #await knowledge.close()
            print("  ✓ Knowledge service cleaned up")
        except Exception as e:
            print(f"  ✗ Error cleaning up knowledge service: {e}")

        # Wrap any active conversation
        if manual_controller:
            try:
                status = manual_controller.get_status()
                if status["state"] == "engaged":
                    print("  Wrapping active conversation...")
                    await manual_controller.wrap_conversation()
            except Exception as e:
                print(f"  ✗ Error wrapping conversation: {e}")

        # Stop all services
        if service_manager:
            try:
                print("  Stopping all services...")
                await service_manager.stop_all()
            except Exception as e:
                print(f"  ✗ Error stopping services: {e}")

        # Archive logs
        logs_dir = Path(__file__).parent.parent.parent / "logs"
        try:
            archive_dir = archive_logs(logs_dir)
            if archive_dir:
                print(f"  ✓ Archived logs to {archive_dir.relative_to(logs_dir.parent)}")
            else:
                print("  ℹ No logs to archive")
        except Exception as e:
            print(f"  ✗ Error archiving logs: {e}")

        print("✓ Shutdown complete")


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start services: {str(e)}")


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


# ============================================================================
# Conversation Control Endpoints
# ============================================================================

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

        # Handle different result statuses
        if result["status"] == "operation_in_progress":
            raise HTTPException(status_code=409, detail=result["message"])
        elif result["status"] == "already_engaged":
            return result  # Return current state (idempotent)
        elif result["status"] == "success":
            # Start transcript capture
            print(f"DEBUG: transcript_manager={transcript_manager}, room={result.get('room')}")
            if transcript_manager and result.get("room"):
                try:
                    print(f"DEBUG: Attempting to connect transcript manager to room {result['room']}")
                    await transcript_manager.connect(result["room"])
                    print(f"DEBUG: Transcript manager connected successfully")
                except Exception as e:
                    print(f"⚠️  Failed to start transcript capture: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"DEBUG: Skipping transcript manager connection")
            return result
        else:
            raise HTTPException(status_code=500, detail=f"Unknown status: {result['status']}")

    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
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

        # Handle different result statuses
        if result["status"] == "operation_in_progress":
            raise HTTPException(status_code=409, detail=result["message"])
        elif result["status"] == "already_idle":
            return result  # Return current state (idempotent)
        elif result["status"] == "success":
            # Stop transcript capture and clear
            if transcript_manager:
                await transcript_manager.disconnect()
                if transcript_store:
                    transcript_store.clear()
            return result
        else:
            raise HTTPException(status_code=500, detail=f"Unknown status: {result['status']}")

    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
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

    try:
        return await send_agent_command(
            AgentCommandRequest(
                text=text,
                gesture=gesture,
                steps=steps,
                room=room,
                topic=command.topic,
                plain_text=command.plain_text,
                identity=command.identity,
                name=command.name,
            ),
            room=room,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to publish agent command: {str(e)}")


@app.get("/api/gestures")
async def list_gestures() -> GestureCatalogResponse:
    """Return the gesture catalog for direct-command UI controls."""
    service_state = "unregistered"
    service_running = False

    if service_manager:
        gesture_service = service_manager.get("gesture-bridge")
        if gesture_service:
            status = gesture_service.get_status()
            service_state = status.state.value
            service_running = status.state == ServiceState.RUNNING

    return GestureCatalogResponse(
        gestures=list(GESTURE_NAMES),
        service_state=service_state,
        service_running=service_running,
    )


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

    return {
        "input_devices": audio_bridge.get_input_devices(),
        "output_devices": audio_bridge.get_output_devices()
    }


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
        # Fallback: list /dev/video* directly if camera bridge is missing
        devices = sorted(Path("/dev").glob("video*"))
        return {"devices": [str(dev) for dev in devices]}

    return {
        "devices": camera_bridge.get_video_devices()
    }


@app.get("/api/camera-bridge/config")
async def get_camera_bridge_config():
    """
    Get current camera bridge configuration.

    Returns:
        Configuration dict with device, resolution, framerate, and mode
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    camera_bridge = service_manager.get("camera-bridge")
    if not camera_bridge:
        raise HTTPException(status_code=404, detail="Camera bridge service not found")

    config = camera_bridge.get_config()
    return {
        "device": config.get("device", "/dev/video0"),
        "resolution": config.get("resolution"),
        "framerate": config.get("framerate"),
        "mode": config.get("mode", "livekit"),
    }


@app.post("/api/camera-bridge/config")
async def update_camera_bridge_config(
    device: Optional[str] = None,
    resolution: Optional[str] = None,
    framerate: Optional[float] = None,
    mode: Optional[str] = None,
):
    """
    Update camera bridge configuration.
    Service will automatically restart if running.

    Args:
        device: Video device path (e.g., /dev/video0)
        resolution: Resolution string (e.g., 1280x720)
        framerate: Frames per second
        mode: "livekit" or "frontend"

    Returns:
        Updated service status
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    camera_bridge = service_manager.get("camera-bridge")
    if not camera_bridge:
        raise HTTPException(status_code=404, detail="Camera bridge service not found")

    updates = {}
    if device is not None:
        updates["device"] = device
    if resolution is not None:
        updates["resolution"] = resolution
    if framerate is not None:
        updates["framerate"] = framerate
    if mode is not None:
        if mode not in ("livekit", "frontend"):
            raise HTTPException(status_code=400, detail="Invalid mode. Use livekit or frontend.")
        updates["mode"] = mode

    try:
        await service_manager.update_service_config("camera-bridge", updates)
        return camera_bridge.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {str(e)}")


@app.get("/api/video/stream")
async def stream_video(
    device: Optional[str] = Query(None, description="Video device path (e.g., /dev/video0)"),
    width: Optional[int] = Query(None, ge=1),
    height: Optional[int] = Query(None, ge=1),
    framerate: Optional[float] = Query(None, gt=0),
    quality: int = Query(85, ge=10, le=95),
):
    """
    Stream MJPEG video frames from the selected camera device.

    Returns:
        multipart/x-mixed-replace MJPEG stream
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    camera_bridge = service_manager.get("camera-bridge")
    default_device = "/dev/video0"
    default_resolution = None
    default_framerate = None

    if camera_bridge:
        config = camera_bridge.get_config()
        default_device = config.get("device", default_device)
        default_resolution = config.get("resolution")
        default_framerate = config.get("framerate")

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
        jpeg_quality=quality,
    )

    return StreamingResponse(
        mjpeg_stream(stream_cfg),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


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
        "default_speakers": config.get("default_speakers")
    }


@app.post("/api/audio-bridge/config")
async def update_audio_bridge_config(
    default_microphone: Optional[int] = None,
    default_speakers: Optional[int] = None
):
    """
    Update audio bridge device configuration.
    Service will automatically restart if running.

    Args:
        default_microphone: Audio input device index (or null to unset)
        default_speakers: Audio output device index (or null to unset)

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

    if not request_host:
        return _normalize_livekit_ws_scheme(livekit_url)

    if ":" in request_host and not request_host.startswith("["):
        browser_host = request_host.split(":", 1)[0]
    else:
        browser_host = request_host

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


@app.get("/api/camera-bridge/stream-info")
async def get_camera_bridge_stream_info():
    """Return camera bridge room/identity information for frontend monitoring."""
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    camera_bridge = service_manager.get("camera-bridge")
    if not camera_bridge:
        raise HTTPException(status_code=404, detail="Camera bridge service not found")

    config = camera_bridge.get_config()
    return {
        "running": camera_bridge.get_status().state == ServiceState.RUNNING,
        "room": config.get("room") or os.getenv("LIVEKIT_ROOM", "g1-lab"),
        "identity": os.getenv("LIVEKIT_CAMERA_IDENTITY", "camera-bridge"),
        "track_name": os.getenv("LIVEKIT_CAMERA_TRACK_NAME", "camera"),
        "topic": os.getenv("LIVEKIT_CAMERA_TOPIC", "images"),
    }


@app.get("/api/camera-bridge/config")
async def get_camera_bridge_config():
    """
    Get current camera bridge configuration.

    Returns:
        Configuration dict with selected camera device path
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    camera_bridge = service_manager.get("camera-bridge")
    if not camera_bridge:
        raise HTTPException(status_code=404, detail="Camera bridge service not found")

    config = camera_bridge.get_config()
    return {
        "device": config.get("device"),
        "interval": config.get("interval", 3.0)
    }


@app.post("/api/camera-bridge/config")
async def update_camera_bridge_config(
    device: Optional[str] = None,
    interval: Optional[float] = None
):
    """
    Update camera bridge device configuration.
    Service will automatically restart if running.

    Args:
        device: Video device path (e.g., /dev/video0)
        interval: Seconds between frames (e.g., 3.0)

    Returns:
        Updated service status
    """
    if not service_manager:
        raise HTTPException(status_code=500, detail="Service manager not initialized")

    camera_bridge = service_manager.get("camera-bridge")
    if not camera_bridge:
        raise HTTPException(status_code=404, detail="Camera bridge service not found")

    updates = {}
    if device is not None:
        updates["device"] = device
    if interval is not None:
        updates["interval"] = interval

    print(f"[DEBUG] Camera bridge config updates: {updates}")

    try:
        await service_manager.update_service_config("camera-bridge", updates)
        return camera_bridge.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {str(e)}")


# ============================================================================
# Prompt Configuration Endpoints
# ============================================================================

@app.get("/api/prompts/options")
async def get_prompt_options():
    """
    Get available prompt configuration options.
    
    Returns:
        Available languages, personas, contexts and current active config
    """
    try:
        builder = get_prompt_builder()
        return {
            "options": builder.get_available_options(),
            "active": builder.get_active()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get prompt options: {str(e)}")

@app.get("/api/prompts/active")
async def get_active_prompt():
    """
    Get currently active prompt configuration.
    
    Returns:
        Current language, persona, context selections
    """
    try:
        builder = get_prompt_builder()
        return builder.get_active()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get active prompt: {str(e)}")
    
@app.post("/api/prompts/update")
async def update_prompt_config(
    language: Optional[str] = None,
    persona: Optional[str] = None,
    context: Optional[str] = None
):
    """
    Update prompt configuration and get the new system prompt.
    
    Args:
        language: Language code (sr, sl)
        persona: Persona type (friendly_assistant, professional_guide)
        context: Context/scenario (default, petrol_event)
    
    Returns:
        Updated active config and prompt preview
    """
    try:
        new_prompt = update_and_rebuild(
            language=language,
            persona=persona,
            context=context
        )
        
        builder = get_prompt_builder()
        return {
            "success": True,
            "active": builder.get_active(),
            "prompt_preview": new_prompt[:500] + "..." if len(new_prompt) > 500 else new_prompt
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update prompt: {str(e)}")

@app.post("/api/prompts/preview")
async def preview_prompt(
    language: Optional[str] = None,
    persona: Optional[str] = None,
    context: Optional[str] = None
):
    """
    Preview a prompt configuration without applying it.
    Useful for UI to show what will happen.
    
    Args:
        language: Language code (sr, sl)
        persona: Persona type
        context: Context/scenario
    
    Returns:
        Full prompt content
    """
    try:
        builder = get_prompt_builder()
        prompt = builder.build(
            language=language,
            persona=persona,
            context=context
        )
        return {
            "prompt": prompt
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to preview prompt: {str(e)}")

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
    if not log_path:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' does not have logs")

    try:
        logs = await service.tail_logs(lines)
        return {
            "service": service_name,
            "log_path": log_path,
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

    return {
        "services": service_manager.get_all_status(),
        "conversation": manual_controller.get_status(),
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

                        event_data = {
                            "services": [s.model_dump() if hasattr(s, 'model_dump') else s.dict() for s in services_status],
                            "conversation": manual_controller.get_status(),
                            "transcript": transcript_state
                        }

                        yield f"data: {json.dumps(event_data)}\n\n"
                    except Exception as e:
                        # Log error but continue streaming
                        print(f"[SSE] Error generating event: {e}")
                        await asyncio.sleep(1)
                        continue

                await asyncio.sleep(1)
        except asyncio.CancelledError:
            # Handle graceful shutdown
            print("[SSE] Client disconnected")
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
        # Check if requesting a file with extension (e.g., favicon.ico, robots.txt)
        if "." in full_path.split("/")[-1]:
            file_path = dist_dir / full_path
            if file_path.exists():
                return FileResponse(file_path)

        # Otherwise, serve index.html for SPA routing
        return FileResponse(dist_dir / "index.html")
else:
    print("⚠️  Warning: Frontend build not found at dist/")
    print("   Run 'cd frontend && npm run build' to create production build")
