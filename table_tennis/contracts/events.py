"""Events emitted by the backend only (contract v1, section 6).

Events are facts after commit. Several events can share one revision (e.g.
point.confirmed + match.finished); consumers dedupe by ``event_id`` and judge
staleness by ``revision``.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import Field, TypeAdapter

from .models import (
    ContractModel,
    CourtEndByPlayer,
    GameRules,
    MatchSnapshot,
    Player,
    PointProposal,
    Readiness,
    RobotCall,
    RobotSideByPlayer,
    ScoreByPlayer,
)
from .primitives import (
    Identifier,
    MatchStatus,
    Persona,
    PlayerId,
    PointReason,
    SchemaVersion,
    ScoringMode,
    ShortText,
    UTCDateTime,
    UUIDStr,
)

# --------------------------------------------------------------------------- payloads


class MatchCreatedPayload(ContractModel):
    players: list[Player]
    config: GameRules
    first_server_id: PlayerId
    scoring_mode: ScoringMode
    persona: Persona
    court_end_by_player: CourtEndByPlayer
    robot_side_by_player: RobotSideByPlayer
    calibration_id: Optional[Identifier] = None
    table_id: Optional[Identifier] = None


class MatchStartedPayload(ContractModel):
    server_id: PlayerId


class RallyArmedPayload(ContractModel):
    rally_id: UUIDStr
    server_id: PlayerId


class PointProposedPayload(ContractModel):
    proposal: PointProposal


class PointUnclearEventPayload(ContractModel):
    rally_id: UUIDStr
    reason: Optional[ShortText] = None


class PointConfirmedPayload(ContractModel):
    rally_id: UUIDStr
    winner_id: PlayerId
    reason: PointReason
    source: Literal["operator_award", "confirmed_proposal"]
    proposal_id: Optional[UUIDStr] = None
    previous_score: ScoreByPlayer
    new_score: ScoreByPlayer
    # Post-commit snapshot (filled by the engine after folding all events).
    snapshot: Optional[MatchSnapshot] = None


class RallyLetEventPayload(ContractModel):
    rally_id: UUIDStr
    reason: Optional[ShortText] = None


class ScoreCorrectedPayload(ContractModel):
    target_event_id: UUIDStr
    rally_id: UUIDStr
    # winner of the point that was undone
    winner_id: PlayerId
    reason: Optional[ShortText] = None
    previous_score: ScoreByPlayer
    new_score: ScoreByPlayer
    # An armed / pending rally that the undo invalidated, if any.
    invalidated_rally_id: Optional[UUIDStr] = None
    snapshot: Optional[MatchSnapshot] = None


class MatchPausedPayload(ContractModel):
    reason: Optional[ShortText] = None
    previous_status: MatchStatus
    resume_to: MatchStatus
    cancelled_rally_id: Optional[UUIDStr] = None


class MatchResumedPayload(ContractModel):
    reason: Optional[ShortText] = None
    status: MatchStatus


class MatchFinishedPayload(ContractModel):
    winner_id: Optional[PlayerId]
    final_score: ScoreByPlayer
    ended_by_operator: bool = False
    reason: Optional[ShortText] = None


class PersonaChangedPayload(ContractModel):
    previous: Persona
    persona: Persona


class SidesChangedPayload(ContractModel):
    assignment_version: int
    court_end_by_player: CourtEndByPlayer
    robot_side_by_player: RobotSideByPlayer
    invalidated_proposal_id: Optional[UUIDStr] = None
    invalidated_rally_id: Optional[UUIDStr] = None


class CalibrationChangedPayload(ContractModel):
    previous: Optional[Identifier]
    calibration_id: Identifier
    invalidated_proposal_id: Optional[UUIDStr] = None
    invalidated_rally_id: Optional[UUIDStr] = None


class ReadinessChangedPayload(ContractModel):
    component: Literal["robot_ready", "camera_ready", "calibration_ready", "operator_ready"]
    value: bool
    reason: Optional[ShortText] = None
    ready: Readiness


# --------------------------------------------------------------------------- envelopes


class _EventBase(ContractModel):
    schema_version: SchemaVersion = "1.0"
    event_id: UUIDStr
    # Null only for robot call events, which have no score revision.
    match_id: Optional[UUIDStr]
    revision: Optional[int]
    occurred_at: UTCDateTime
    received_at: UTCDateTime
    # command_id of the command that caused the event
    causation_id: Optional[UUIDStr] = None


class MatchCreatedEvent(_EventBase):
    type: Literal["match.created"]
    payload: MatchCreatedPayload


class MatchStartedEvent(_EventBase):
    type: Literal["match.started"]
    payload: MatchStartedPayload


class RallyArmedEvent(_EventBase):
    type: Literal["rally.armed"]
    payload: RallyArmedPayload


class PointProposedEvent(_EventBase):
    type: Literal["point.proposed"]
    payload: PointProposedPayload


class PointUnclearEvent(_EventBase):
    type: Literal["point.unclear"]
    payload: PointUnclearEventPayload


class PointConfirmedEvent(_EventBase):
    type: Literal["point.confirmed"]
    payload: PointConfirmedPayload


class RallyLetEvent(_EventBase):
    type: Literal["rally.let"]
    payload: RallyLetEventPayload


class ScoreCorrectedEvent(_EventBase):
    type: Literal["score.corrected"]
    payload: ScoreCorrectedPayload


class MatchPausedEvent(_EventBase):
    type: Literal["match.paused"]
    payload: MatchPausedPayload


class MatchResumedEvent(_EventBase):
    type: Literal["match.resumed"]
    payload: MatchResumedPayload


class MatchFinishedEvent(_EventBase):
    type: Literal["match.finished"]
    payload: MatchFinishedPayload


class PersonaChangedEvent(_EventBase):
    type: Literal["persona.changed"]
    payload: PersonaChangedPayload


class SidesChangedEvent(_EventBase):
    type: Literal["sides.changed"]
    payload: SidesChangedPayload


class CalibrationChangedEvent(_EventBase):
    type: Literal["calibration.changed"]
    payload: CalibrationChangedPayload


class ReadinessChangedEvent(_EventBase):
    type: Literal["readiness.changed"]
    payload: ReadinessChangedPayload


class RobotCallUpdatedEvent(_EventBase):
    type: Literal["robot.call.updated"]
    payload: RobotCall


EventEnvelope = Annotated[
    Union[
        MatchCreatedEvent,
        MatchStartedEvent,
        RallyArmedEvent,
        PointProposedEvent,
        PointUnclearEvent,
        PointConfirmedEvent,
        RallyLetEvent,
        ScoreCorrectedEvent,
        MatchPausedEvent,
        MatchResumedEvent,
        MatchFinishedEvent,
        PersonaChangedEvent,
        SidesChangedEvent,
        CalibrationChangedEvent,
        ReadinessChangedEvent,
        RobotCallUpdatedEvent,
    ],
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter = TypeAdapter(EventEnvelope)

SCORE_EVENT_TYPES = frozenset({"point.confirmed", "score.corrected"})


def parse_event(data: object):
    return EVENT_ADAPTER.validate_python(data)
