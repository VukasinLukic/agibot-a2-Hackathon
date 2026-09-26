"""Conversation transcript and command data models."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ConversationRole(str, Enum):
    """Role of conversation participant."""
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class ConversationEntry(BaseModel):
    """Single conversation transcript entry."""
    id: str                              # Unique ID: segment_id:stream_id
    segment_id: str                      # Transcription segment ID
    participant_identity: Optional[str]  # LiveKit participant identity
    role: ConversationRole               # Speaker role
    text: str                            # Transcript text
    final: bool                          # Whether segment is finalized
    updated_at: float                    # Unix timestamp
    session_id: int                      # Session counter


class TranscriptState(BaseModel):
    """Current transcript state."""
    connected: bool                      # Is listener connected to LiveKit?
    enabled: bool                        # Is transcript tracking enabled?
    entries: list[ConversationEntry]     # Transcript entries (merged history + pending)


class AgentCommandStep(BaseModel):
    """Single direct-command step for scripted speech/gesture playback."""

    text: str = ""
    gesture: Optional[str] = None
    pause_after_ms: Optional[int] = None
    force_gesture: Optional[bool] = None


class AgentCommandRequest(BaseModel):
    """Direct command payload sent to the active agent over LiveKit."""

    text: str = ""
    gesture: Optional[str] = None
    force_gesture: bool = False
    steps: list[AgentCommandStep] = Field(default_factory=list)
    room: Optional[str] = None
    topic: Optional[str] = None
    plain_text: bool = False
    identity: Optional[str] = None
    name: Optional[str] = None


class AgentCommandResponse(BaseModel):
    """Result of publishing a direct agent command."""

    status: str
    room: str
    topic: str
    mime_type: str
    payload_size: int
    plain_text: bool
    text: str
    gesture: Optional[str] = None


class VisionPresenceEvent(BaseModel):
    """Presence event emitted by the vision detection service."""

    locked: bool
    track_id: Optional[int | str] = None
    timestamp: Optional[float] = None
    timings: dict = Field(default_factory=dict)
    reason: Optional[str] = None
    # {"status": "known"|"unknown", "name": ..., "face_id": ...}, resolved once
    # by the vision service at lock time. Never carries a raw embedding.
    identity: Optional[dict] = None


class VisionCardCaptureRequest(BaseModel):
    """Request to start a card capture job in the vision service."""

    timeout_s: Optional[float] = Field(default=None, gt=0)
    target_type: Optional[str] = None
    source: Optional[str] = None


class VisionCardCaptureResult(BaseModel):
    """Card capture result emitted by the vision detection service."""

    request_id: str
    status: str
    timestamp: Optional[float] = None
    metadata: dict = Field(default_factory=dict)


class VisionFaceCaptureRequest(BaseModel):
    """Request to start one distance-gated enrollment capture in the vision service."""

    name: str
    distance: str = Field(description="'close' or 'far'")
    timeout_s: Optional[float] = Field(default=None, gt=0)


class VisionFaceCaptureResult(BaseModel):
    """Face capture result emitted by the vision detection service.

    metadata never contains a raw embedding by the time it reaches here - the
    vision service pops it before posting, using it locally to match/enroll
    against its own face store.
    """

    request_id: str
    status: str
    timestamp: Optional[float] = None
    metadata: dict = Field(default_factory=dict)


class VisionFaceForgetRequest(BaseModel):
    """Request to delete a stored face by id or name."""

    face_id: Optional[str] = None
    name: Optional[str] = None


class VisionFaceForgetResult(BaseModel):
    """Face forget result emitted by the vision detection service."""

    request_id: str
    status: str
    timestamp: Optional[float] = None


class GestureCatalogResponse(BaseModel):
    """Available gestures exposed to the supervisor frontend."""

    gestures: list[str]
    all_gestures: list[str]
    active_pool: str
    available_pools: list[str]
    service_state: str
    service_running: bool
