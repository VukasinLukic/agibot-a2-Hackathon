"""Adapter ports (contract v1, section 9) plus Clock / IdGenerator implementations.

Every port has a working fake in ``table_tennis.robot.fake``,
``table_tennis.persona.speech`` or ``table_tennis.vision.stub``. Real
implementations are owned by the branch owners and are only constructed when
``mode: real`` is explicitly configured.
"""

from __future__ import annotations

import itertools
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional, Protocol, runtime_checkable

from table_tennis.contracts import MatchSnapshot, RobotCall, RobotCallRequest, RobotStatus

# --------------------------------------------------------------------------- time / ids


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        # millisecond precision = what the wire format carries, so replayed
        # state compares equal to live state
        now = datetime.now(timezone.utc)
        return now.replace(microsecond=(now.microsecond // 1000) * 1000)

    def monotonic(self) -> float:
        import time

        return time.monotonic()


class FixedClock:
    """Deterministic clock for tests and fixture generation."""

    def __init__(self, start: Optional[datetime] = None, step_ms: int = 1000):
        self._now = start or datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
        self._step = timedelta(milliseconds=step_ms)
        self._mono = 0.0
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            value = self._now
            self._now = self._now + self._step
            return value

    def monotonic(self) -> float:
        with self._lock:
            self._mono += self._step.total_seconds()
            return self._mono

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += timedelta(seconds=seconds)
            self._mono += seconds


@runtime_checkable
class IdGenerator(Protocol):
    def new_id(self) -> str: ...


class UuidGenerator:
    def new_id(self) -> str:
        return str(uuid.uuid4())


class SequentialIdGenerator:
    """Deterministic UUID-shaped ids: 00000000-0000-4000-8000-000000000001, ..."""

    def __init__(self, namespace: int = 0):
        self._counter = itertools.count(1)
        self._ns = namespace
        self._lock = threading.Lock()

    def new_id(self) -> str:
        with self._lock:
            n = next(self._counter)
        return f"{self._ns:08x}-0000-4000-8000-{n:012x}"


# --------------------------------------------------------------------------- outputs


@runtime_checkable
class ScoreDisplay(Protocol):
    """Scoreboard. Idempotent: latest snapshot wins; stale revisions are ignored."""

    def render(self, snapshot: MatchSnapshot) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class GestureOutput(Protocol):
    """Short gesture after a *confirmed* point. Never replayed after restart."""

    def present_point(self, event: Any, snapshot: MatchSnapshot) -> None: ...

    def cancel_pending(self, match_id: str) -> None: ...


@runtime_checkable
class SpeechOutput(Protocol):
    """Announcement of a confirmed fact; commentary comes from the persona module."""

    def announce(self, event: Any, snapshot: MatchSnapshot) -> None: ...

    def cancel_pending(self, match_id: str) -> None: ...


# --------------------------------------------------------------------------- robot


@runtime_checkable
class RobotNavigator(Protocol):
    """Single-flight robot call lifecycle. Acceptance is not arrival."""

    def request_call(self, request: RobotCallRequest, call_id: str) -> RobotCall: ...

    def get_call(self, call_id: str) -> Optional[RobotCall]: ...

    def get_status(self) -> RobotStatus: ...

    def cancel(self, call_id: str) -> RobotCall: ...

    def tick(self) -> list[RobotCall]:
        """Advance internal state; returns calls whose state changed."""
        ...


# --------------------------------------------------------------------------- vision


CommandSink = Callable[[dict], Any]
ContextProvider = Callable[[], MatchSnapshot]


@runtime_checkable
class VisionProducer(Protocol):
    """Produces observations and point.propose commands; never mutates score."""

    def run(self, sink: CommandSink, context_provider: ContextProvider) -> None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- storage


@runtime_checkable
class EventStore(Protocol):
    def append_transaction(
        self,
        *,
        scope: str,
        command_id: str,
        payload_hash: str,
        match_id: str,
        expected_previous_revision: int,
        events: Iterable[Any],
        snapshot: MatchSnapshot,
        state_json: str,
        response_json: str,
        outbox_kinds: Iterable[tuple[str, str]],
        rally_decision: Optional[tuple[str, str]] = None,
    ) -> list[int]: ...

    def load_match(self, match_id: str) -> Optional[tuple[MatchSnapshot, str]]: ...

    def read_after(self, match_id: str, cursor: int, limit: int = 1000) -> list[tuple[int, Any]]: ...
