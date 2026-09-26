"""Core data models of contract v1 (05_SHARED_CONTRACT.md, section 3)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .primitives import (
    CourtEnd,
    DisplayName,
    Identifier,
    MatchStatus,
    NavigationState,
    ObservationKind,
    Persona,
    PlayerId,
    PointReason,
    RobotAvailability,
    RobotCallState,
    RobotSide,
    SchemaVersion,
    ScoringMode,
    ServiceMode,
    ShortText,
    UTCDateTime,
    UUIDStr,
)


class ContractModel(BaseModel):
    """Base for every wire model: unknown fields are rejected, not silently dropped."""

    model_config = ConfigDict(extra="forbid", frozen=False)


# --------------------------------------------------------------------------- players / config


class Player(ContractModel):
    id: PlayerId
    display_name: DisplayName
    # Voluntary, manually entered, only for the humorous persona. Never inferred.
    role_label: Optional[DisplayName] = None
    role_rank: Optional[int] = Field(default=None, ge=0, le=100)


class GameRules(ContractModel):
    """Scoring rules. v1 supports exactly one game to 11, win by 2."""

    target_points: int = Field(default=11, ge=1, le=99)
    win_by: int = Field(default=2, ge=1, le=10)
    best_of: int = Field(default=1, ge=1, le=9)


class MatchConfig(GameRules):
    """Create-time configuration. scoring_mode is fixed for the whole match in v1."""

    first_server_id: PlayerId = "p1"
    scoring_mode: ScoringMode = "assisted"
    persona: Persona = "regular"


class ScoreByPlayer(ContractModel):
    p1: int = Field(ge=0)
    p2: int = Field(ge=0)

    def get(self, player: str) -> int:
        return int(getattr(self, player))


class CourtEndByPlayer(ContractModel):
    p1: CourtEnd
    p2: CourtEnd

    @model_validator(mode="after")
    def _distinct(self) -> "CourtEndByPlayer":
        if self.p1 == self.p2:
            raise ValueError("players must be on different table ends")
        return self


class RobotSideByPlayer(ContractModel):
    p1: RobotSide
    p2: RobotSide

    @model_validator(mode="after")
    def _distinct(self) -> "RobotSideByPlayer":
        if self.p1 == self.p2:
            raise ValueError("players must be on different robot sides")
        return self


class Readiness(ContractModel):
    robot_ready: bool = False
    camera_ready: bool = False
    calibration_ready: bool = False
    operator_ready: bool = False


# --------------------------------------------------------------------------- vision


class PointProposal(ContractModel):
    proposal_id: UUIDStr
    rally_id: UUIDStr
    winner_id: PlayerId
    # Raw model score in [0, 1]; NOT a calibrated probability.
    confidence: float = Field(ge=0.0, le=1.0)
    reason: PointReason
    calibration_id: Identifier
    assignment_version: int = Field(ge=1)
    capture_start_seq: int = Field(ge=0)
    capture_end_seq: int = Field(ge=0)
    # Local clip identifier, never a path or URL to be dereferenced.
    evidence_ref: Optional[Identifier] = None

    @model_validator(mode="after")
    def _window(self) -> "PointProposal":
        if self.capture_end_seq < self.capture_start_seq:
            raise ValueError("capture_end_seq must be >= capture_start_seq")
        return self


class VisionObservation(ContractModel):
    frame_seq: int = Field(ge=0)
    capture_monotonic_ns: int = Field(ge=0)
    detected: bool
    x_px: Optional[float] = None
    y_px: Optional[float] = None
    observation_kind: ObservationKind
    confidence: float = Field(ge=0.0, le=1.0)
    calibration_id: Optional[Identifier] = None

    @model_validator(mode="after")
    def _coords(self) -> "VisionObservation":
        if self.observation_kind == "missing" and (self.x_px is not None or self.y_px is not None):
            raise ValueError("missing observation cannot carry coordinates")
        if self.observation_kind != "missing" and (self.x_px is None or self.y_px is None):
            raise ValueError("observed/predicted observation needs x_px and y_px")
        return self


# --------------------------------------------------------------------------- robot


class RobotCall(ContractModel):
    call_id: UUIDStr
    table_id: Identifier
    named_waypoint_id: Identifier
    state: RobotCallState
    updated_at: UTCDateTime
    reason: Optional[ShortText] = None
    # Additive fields (not in the v1 table, optional for consumers).
    match_id: Optional[UUIDStr] = None
    native_task_id: Optional[str] = None
    simulated: bool = True


class RobotStatus(ContractModel):
    call_id: Optional[UUIDStr] = None
    availability: RobotAvailability
    navigation_state: NavigationState
    pose_age_ms: Optional[int] = Field(default=None, ge=0)
    ready: bool
    reason: Optional[ShortText] = None
    simulated: bool = True


# --------------------------------------------------------------------------- match snapshot


class MatchSnapshot(ContractModel):
    schema_version: SchemaVersion = "1.0"
    match_id: UUIDStr
    revision: int = Field(ge=0)
    status: MatchStatus
    players: list[Player] = Field(min_length=2, max_length=2)
    config: GameRules
    score_by_player: ScoreByPlayer
    first_server_id: PlayerId
    server_id: Optional[PlayerId]
    winner_id: Optional[PlayerId]
    assignment_version: int = Field(ge=1)
    court_end_by_player: CourtEndByPlayer
    robot_side_by_player: RobotSideByPlayer
    calibration_id: Optional[Identifier]
    active_rally_id: Optional[UUIDStr]
    active_proposal_id: Optional[UUIDStr]
    persona: Persona
    scoring_mode: ScoringMode
    ready: Readiness
    updated_at: UTCDateTime
    # Additive helpers for clients; all derived by the backend.
    active_proposal: Optional[PointProposal] = None
    paused_from: Optional[MatchStatus] = None
    last_point_event_id: Optional[UUIDStr] = None
    table_id: Optional[Identifier] = None

    def player(self, player_id: str) -> Player:
        for p in self.players:
            if p.id == player_id:
                return p
        raise KeyError(player_id)


# --------------------------------------------------------------------------- API helpers


class CreateMatchRequest(ContractModel):
    schema_version: SchemaVersion = "1.0"
    # Idempotency key for match creation (scope: create).
    command_id: UUIDStr
    players: list[Player] = Field(min_length=2, max_length=2)
    config: MatchConfig = Field(default_factory=MatchConfig)
    court_end_by_player: CourtEndByPlayer = Field(
        default_factory=lambda: CourtEndByPlayer(p1="end_a", p2="end_b")
    )
    robot_side_by_player: RobotSideByPlayer = Field(
        default_factory=lambda: RobotSideByPlayer(p1="left", p2="right")
    )
    calibration_id: Optional[Identifier] = None
    table_id: Identifier = "table-1"

    @model_validator(mode="after")
    def _players(self) -> "CreateMatchRequest":
        ids = sorted(p.id for p in self.players)
        if ids != ["p1", "p2"]:
            raise ValueError("players must be exactly p1 and p2")
        return self


class ErrorResponse(ContractModel):
    code: str
    message: str
    current_revision: Optional[int] = None
    details: Optional[dict[str, Any]] = None


class RobotCallRequest(ContractModel):
    command_id: UUIDStr
    table_id: Identifier
    named_waypoint_id: Identifier
    # Additive: when set, arrival marks robot_ready on this match.
    match_id: Optional[UUIDStr] = None


class RobotCancelRequest(ContractModel):
    command_id: UUIDStr


class CapabilityStatus(ContractModel):
    available: bool
    simulated: bool
    detail: Optional[str] = None


class HealthResponse(ContractModel):
    status: str = "ok"
    schema_version: SchemaVersion = "1.0"
    mode: ServiceMode
    simulated: bool
    automatic_scoring_enabled: bool
    auth_mode: str
    capabilities: dict[str, CapabilityStatus]


class FakeOutputRecord(ContractModel):
    kind: str
    match_id: Optional[UUIDStr] = None
    event_id: Optional[UUIDStr] = None
    revision: Optional[int] = None
    text: str
    at: UTCDateTime


class DebugOutputs(ContractModel):
    """Mock-only view of what fake screen/speech/gesture adapters would have done."""

    simulated: bool = True
    display: Optional[FakeOutputRecord] = None
    speech: list[FakeOutputRecord] = Field(default_factory=list)
    gestures: list[FakeOutputRecord] = Field(default_factory=list)
