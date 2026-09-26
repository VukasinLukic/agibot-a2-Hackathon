"""Contract v1: the single source of truth for all four branches.

Pydantic models here generate JSON Schema / OpenAPI and the frontend
TypeScript types (``python -m table_tennis.contracts.generate``). Do not keep a
hand-written second copy of these types anywhere.
"""

from .api import CommandResult, StreamEventMessage, StreamSnapshotMessage
from .commands import (
    COMMAND_ADAPTER,
    COMMAND_PERMISSIONS,
    REVISIONLESS_COMMANDS,
    CommandEnvelope,
    parse_command,
)
from .events import EVENT_ADAPTER, SCORE_EVENT_TYPES, EventEnvelope, parse_event
from .models import (
    CapabilityStatus,
    CourtEndByPlayer,
    CreateMatchRequest,
    DebugOutputs,
    ErrorResponse,
    FakeOutputRecord,
    GameRules,
    HealthResponse,
    MatchConfig,
    MatchSnapshot,
    Player,
    PointProposal,
    Readiness,
    RobotCall,
    RobotCallRequest,
    RobotCancelRequest,
    RobotSideByPlayer,
    RobotStatus,
    ScoreByPlayer,
    VisionObservation,
)
from .primitives import PLAYER_IDS, ROBOT_CALL_TERMINAL_STATES, SCHEMA_VERSION

__all__ = [
    "CapabilityStatus",
    "COMMAND_ADAPTER",
    "COMMAND_PERMISSIONS",
    "CommandEnvelope",
    "CommandResult",
    "CourtEndByPlayer",
    "CreateMatchRequest",
    "DebugOutputs",
    "ErrorResponse",
    "EVENT_ADAPTER",
    "EventEnvelope",
    "FakeOutputRecord",
    "GameRules",
    "HealthResponse",
    "MatchConfig",
    "MatchSnapshot",
    "PLAYER_IDS",
    "Player",
    "PointProposal",
    "Readiness",
    "REVISIONLESS_COMMANDS",
    "ROBOT_CALL_TERMINAL_STATES",
    "RobotCall",
    "RobotCallRequest",
    "RobotCancelRequest",
    "RobotSideByPlayer",
    "RobotStatus",
    "SCHEMA_VERSION",
    "SCORE_EVENT_TYPES",
    "ScoreByPlayer",
    "StreamEventMessage",
    "StreamSnapshotMessage",
    "VisionObservation",
    "parse_command",
    "parse_event",
]
