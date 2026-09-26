"""Inbound commands (contract v1, section 5).

Commands are *requests*. Only the backend turns an accepted command into
events. ``point.propose`` never changes the score by itself.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import Field, TypeAdapter

from .models import ContractModel, CourtEndByPlayer, PointProposal, RobotSideByPlayer
from .primitives import Identifier, Persona, PlayerId, PointReason, SchemaVersion, ShortText, UUIDStr

# --------------------------------------------------------------------------- payloads


class EmptyPayload(ContractModel):
    pass


class ReasonPayload(ContractModel):
    reason: Optional[ShortText] = None


class PointConfirmPayload(ContractModel):
    proposal_id: UUIDStr


class PointAwardPayload(ContractModel):
    rally_id: UUIDStr
    winner_id: PlayerId
    reason: PointReason = "unknown"


class RallyLetPayload(ContractModel):
    rally_id: UUIDStr
    reason: Optional[ShortText] = None


class PointUndoPayload(ContractModel):
    target_event_id: UUIDStr
    reason: Optional[ShortText] = None


class PersonaSetPayload(ContractModel):
    persona: Persona


class SidesSetPayload(ContractModel):
    court_end_by_player: CourtEndByPlayer
    robot_side_by_player: RobotSideByPlayer


class CalibrationSetPayload(ContractModel):
    calibration_id: Identifier


class ReadySetPayload(ContractModel):
    ready: bool
    # For robot.ready.set sent by an operator this must be "manual_arrival".
    reason: Optional[ShortText] = None


class OperatorReadyPayload(ContractModel):
    ready: bool


# --------------------------------------------------------------------------- envelopes


class _CommandBase(ContractModel):
    schema_version: SchemaVersion = "1.0"
    command_id: UUIDStr
    # Required for every score-affecting command. May be null only for the
    # *.ready.set readiness commands sent by adapters (no score effect).
    expected_revision: Optional[int] = Field(default=None, ge=0)


class MatchStartCommand(_CommandBase):
    type: Literal["match.start"]
    payload: EmptyPayload = Field(default_factory=EmptyPayload)


class RallyArmCommand(_CommandBase):
    type: Literal["rally.arm"]
    payload: EmptyPayload = Field(default_factory=EmptyPayload)


class PointProposeCommand(_CommandBase):
    type: Literal["point.propose"]
    payload: PointProposal


class PointConfirmCommand(_CommandBase):
    type: Literal["point.confirm"]
    payload: PointConfirmPayload


class PointAwardCommand(_CommandBase):
    type: Literal["point.award"]
    payload: PointAwardPayload


class RallyLetCommand(_CommandBase):
    type: Literal["rally.let"]
    payload: RallyLetPayload


class PointUndoCommand(_CommandBase):
    type: Literal["point.undo"]
    payload: PointUndoPayload


class MatchPauseCommand(_CommandBase):
    type: Literal["match.pause"]
    payload: ReasonPayload = Field(default_factory=ReasonPayload)


class MatchResumeCommand(_CommandBase):
    type: Literal["match.resume"]
    payload: ReasonPayload = Field(default_factory=ReasonPayload)


class MatchEndCommand(_CommandBase):
    type: Literal["match.end"]
    payload: ReasonPayload = Field(default_factory=ReasonPayload)


class PersonaSetCommand(_CommandBase):
    type: Literal["persona.set"]
    payload: PersonaSetPayload


class SidesSetCommand(_CommandBase):
    type: Literal["sides.set"]
    payload: SidesSetPayload


class CalibrationSetCommand(_CommandBase):
    type: Literal["calibration.set"]
    payload: CalibrationSetPayload


class RobotReadySetCommand(_CommandBase):
    type: Literal["robot.ready.set"]
    payload: ReadySetPayload


class CameraReadySetCommand(_CommandBase):
    type: Literal["camera.ready.set"]
    payload: ReadySetPayload


class OperatorReadySetCommand(_CommandBase):
    type: Literal["operator.ready.set"]
    payload: OperatorReadyPayload


CommandEnvelope = Annotated[
    Union[
        MatchStartCommand,
        RallyArmCommand,
        PointProposeCommand,
        PointConfirmCommand,
        PointAwardCommand,
        RallyLetCommand,
        PointUndoCommand,
        MatchPauseCommand,
        MatchResumeCommand,
        MatchEndCommand,
        PersonaSetCommand,
        SidesSetCommand,
        CalibrationSetCommand,
        RobotReadySetCommand,
        CameraReadySetCommand,
        OperatorReadySetCommand,
    ],
    Field(discriminator="type"),
]

COMMAND_ADAPTER: TypeAdapter = TypeAdapter(CommandEnvelope)

CommandType = Literal[
    "match.start",
    "rally.arm",
    "point.propose",
    "point.confirm",
    "point.award",
    "rally.let",
    "point.undo",
    "match.pause",
    "match.resume",
    "match.end",
    "persona.set",
    "sides.set",
    "calibration.set",
    "robot.ready.set",
    "camera.ready.set",
    "operator.ready.set",
]

# Commands that may omit expected_revision (readiness only, never score).
REVISIONLESS_COMMANDS = frozenset({"robot.ready.set", "camera.ready.set", "operator.ready.set"})

# Who may send what (contract v1 section 5). The actor is determined by the
# server (auth / adapter wiring), never by a field inside the JSON.
COMMAND_PERMISSIONS: dict[str, frozenset[str]] = {
    "match.start": frozenset({"operator"}),
    "rally.arm": frozenset({"operator"}),
    "point.propose": frozenset({"vision", "sim"}),
    "point.confirm": frozenset({"operator"}),
    "point.award": frozenset({"operator"}),
    "rally.let": frozenset({"operator"}),
    "point.undo": frozenset({"operator"}),
    "match.pause": frozenset({"operator"}),
    "match.resume": frozenset({"operator"}),
    "match.end": frozenset({"operator"}),
    "persona.set": frozenset({"operator"}),
    "sides.set": frozenset({"operator"}),
    "calibration.set": frozenset({"operator"}),
    # operator only for an explicitly marked manual arrival (checked in engine)
    "robot.ready.set": frozenset({"robot", "operator"}),
    "camera.ready.set": frozenset({"vision", "sim"}),
    "operator.ready.set": frozenset({"operator"}),
}


def parse_command(data: object):
    """Validate raw JSON-like data into a concrete command model."""
    return COMMAND_ADAPTER.validate_python(data)
