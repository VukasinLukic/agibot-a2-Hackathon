"""
Service management package for robot supervisor.
"""

from .base import (
    BaseService,
    ServiceState,
    ServiceStatus,
    Device,
    DeviceType,
    ConfigParameter,
    HealthCheckConfig,
    HealthStatus,
)
from .livekit_server import LiveKitServerService
from .voice_agent import VoiceAgentService
from .camera_bridge import CameraBridgeService
from .audio_bridge import AudioBridgeService
from .audio_bridge_remote import AudioBridgeRemoteService
from .gesture_bridge import GestureBridgeService
from .inspire_hands import InspireHandsService
from .rag_service import RAGService
from .teleimager_server import TeleimagerServerService
from .xr_teleop import XRTeleopService
from .conversation_camera_stream import ConversationCameraStreamService
from .vision_controller import VisionControllerService
from .video_recording import VideoRecordingService
from .registry import ServiceRegistry, ServiceManager


__all__ = [
    # Base classes
    "BaseService",
    "ServiceState",
    "ServiceStatus",
    "Device",
    "DeviceType",
    "ConfigParameter",
    "HealthCheckConfig",
    "HealthStatus",
    # Service implementations
    "LiveKitServerService",
    "VoiceAgentService",
    "CameraBridgeService",
    "AudioBridgeService",
    "AudioBridgeRemoteService",
    "GestureBridgeService",
    "InspireHandsService",
    "RAGService",
    "TeleimagerServerService",
    "XRTeleopService",
    "ConversationCameraStreamService",
    "VisionControllerService",
    "VideoRecordingService",
    # Registry
    "ServiceRegistry",
    "ServiceManager",
]
