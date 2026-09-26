from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

ServiceState = Literal["inactive", "active", "failed", "unknown"]
EngagementState = Literal["idle", "dispatching", "engaged", "wrapping", "error"]


class DependencyStatus(BaseModel):
    name: str
    display_name: Optional[str] = None
    state: ServiceState
    auto_started_at: Optional[float] = None


class ServiceStatus(BaseModel):
    name: str
    display_name: Optional[str] = None
    backend: Literal["systemd", "process"]
    state: ServiceState
    pid: Optional[int] = None
    info: Optional[str] = None
    started_at: Optional[float] = None
    uptime_s: Optional[float] = None
    dependencies: List[DependencyStatus] = Field(default_factory=list)
    mode: Optional[str] = None
    mode_display_name: Optional[str] = None
    modes: List["ServiceModeInfo"] = Field(default_factory=list)


class SupervisorStatus(BaseModel):
    version: str
    now: float
    services: List[ServiceStatus]


class AuditEntry(BaseModel):
    timestamp: float
    actor: str
    action: str
    target: str
    ok: bool
    detail: Optional[str] = None


class ServiceModeInfo(BaseModel):
    name: str
    display_name: Optional[str] = None


class ServiceCommandParameter(BaseModel):
    id: str
    label: str
    value: Optional[str] = None
    flag: Optional[str] = None


class ServiceConfigResponse(BaseModel):
    name: str
    display_name: Optional[str] = None
    mode: Optional[str] = None
    command: List[str] = Field(default_factory=list)
    parameters: List[ServiceCommandParameter] = Field(default_factory=list)


class ServiceCommandParameterUpdate(BaseModel):
    id: str
    value: Optional[str] = None


class ServiceConfigUpdateRequest(BaseModel):
    parameters: List[ServiceCommandParameterUpdate] = Field(default_factory=list)


ConversationRole = Literal["user", "agent", "system", "unknown"]


class ConversationEntry(BaseModel):
    id: str
    segment_id: str
    participant_identity: Optional[str] = None
    role: ConversationRole = "unknown"
    text: str
    final: bool
    updated_at: float
    session_id: int = 0


class ConversationState(BaseModel):
    connected: bool
    enabled: bool
    entries: List[ConversationEntry] = Field(default_factory=list)


class EngagementStatus(BaseModel):
    state: EngagementState
    updated_at: float
    last_room: Optional[str] = None
    last_job_id: Optional[str] = None
    last_error: Optional[str] = None


__all__ = [
    "ServiceStatus",
    "SupervisorStatus",
    "ServiceState",
    "AuditEntry",
    "DependencyStatus",
    "ServiceModeInfo",
    "ServiceCommandParameter",
    "ServiceConfigResponse",
    "ServiceCommandParameterUpdate",
    "ServiceConfigUpdateRequest",
    "ConversationEntry",
    "ConversationState",
    "EngagementStatus",
]
