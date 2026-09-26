"""Request/response models specific to the HTTP + SSE transport (section 8)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .events import EventEnvelope
from .models import ContractModel, MatchSnapshot
from .primitives import UUIDStr


class CommandResult(ContractModel):
    command_id: UUIDStr
    # True when this is a stored answer for an already accepted command_id.
    duplicate: bool
    event_ids: list[UUIDStr] = Field(default_factory=list)
    snapshot: MatchSnapshot


class StreamSnapshotMessage(ContractModel):
    """SSE ``event: snapshot``. Sent first, after resync and after each commit."""

    kind: Literal["snapshot"] = "snapshot"
    # Server cursor of the last event already reflected in ``snapshot``.
    cursor: int = Field(ge=0)
    # True when the client's last_event_id was unknown / too old (full resync).
    resync: bool = False
    snapshot: MatchSnapshot


class StreamEventMessage(ContractModel):
    """SSE ``event: match_event`` (SSE id = event_id)."""

    kind: Literal["event"] = "event"
    cursor: int = Field(ge=1)
    event: EventEnvelope
