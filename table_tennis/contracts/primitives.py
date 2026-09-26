"""Primitive contract types shared by every model (contract v1, section 2)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, Field, PlainSerializer

SCHEMA_VERSION: Literal["1.0"] = "1.0"
SchemaVersion = Literal["1.0"]

# Stable player identity. Never "left"/"right".
PlayerId = Literal["p1", "p2"]
PLAYER_IDS: tuple[PlayerId, PlayerId] = ("p1", "p2")

# Calibrated table coordinates (camera/table geometry).
CourtEnd = Literal["end_a", "end_b"]
# Robot perspective (not necessarily the left side of the video frame).
RobotSide = Literal["left", "right"]

MatchStatus = Literal["setup", "between_rallies", "rally", "pending_decision", "paused", "finished"]
ScoringMode = Literal["manual", "assisted", "automatic"]
Persona = Literal["regular", "corporate"]
PointReason = Literal["missed_return", "double_bounce", "out_after_hit", "service_fault", "unknown"]
ObservationKind = Literal["observed", "predicted", "missing"]
Actor = Literal["operator", "vision", "sim", "robot", "persona"]
ServiceMode = Literal["mock", "real"]

RobotCallState = Literal[
    "requested",
    "validating",
    "moving",
    "arrived",
    "ready",
    "failed",
    "busy",
    "cancel_requested",
    "cancelled",
]
ROBOT_CALL_TERMINAL_STATES = frozenset({"ready", "failed", "busy", "cancelled"})
RobotAvailability = Literal["available", "busy", "offline", "simulated"]
NavigationState = Literal["idle", "validating", "moving", "arrived", "failed", "cancelling"]

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _check_uuid(value: str) -> str:
    if not _UUID_RE.match(value):
        raise ValueError("must be a UUID string")
    return value.lower()


# UUIDs travel as strings on the wire (contract: "UUID string").
UUIDStr = Annotated[
    str,
    AfterValidator(_check_uuid),
    Field(json_schema_extra={"format": "uuid"}),
]


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _fmt_utc(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    text = value.isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


# Timezone-aware UTC timestamp, serialized as ISO8601 with "Z".
UTCDateTime = Annotated[
    AwareDatetime,
    AfterValidator(_to_utc),
    PlainSerializer(_fmt_utc, return_type=str, when_used="json"),
]

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean_text(value: str) -> str:
    # Names and labels are untrusted user data: strip control chars, collapse spaces.
    return " ".join(_CONTROL_RE.sub("", value).split())


def _clean_name(value: str) -> str:
    value = _clean_text(value)
    if not value:
        raise ValueError("must not be blank")
    return value


DisplayName = Annotated[str, AfterValidator(_clean_name), Field(min_length=1, max_length=40)]
ShortText = Annotated[str, AfterValidator(_clean_text), Field(max_length=200)]
Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
