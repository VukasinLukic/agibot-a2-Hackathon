"""RefereeService: the ONLY entry point that changes a match.

Order of checks for every command (02_BACKEND.md, phase 2):
  1. actor permission (server-determined actor)
  2. command_id idempotency (same payload -> stored answer, other payload -> 409)
  3. expected_revision
  4. domain guards (rally / proposal / calibration / assignment / status)
  5. one SQLite transaction: command + events + snapshot + outbox
  6. publish to SSE subscribers (still under the match lock), then kick outputs
Side effects never run inside the transaction and never block it.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Optional

from table_tennis.contracts import CommandResult, CreateMatchRequest, MatchSnapshot
from table_tennis.contracts.api import StreamEventMessage, StreamSnapshotMessage
from table_tennis.storage.sqlite_store import SqliteEventStore, canonical_hash

from . import engine
from .errors import ConflictError, ForbiddenError, NotFoundError
from .ports import Clock, IdGenerator, SystemClock, UuidGenerator
from .stream import Broker, StreamMessage, Subscriber

log = logging.getLogger("table_tennis.service")

# Which events produce which side-effect jobs.
SPEECH_EVENT_TYPES = frozenset(
    {"match.started", "point.proposed", "point.unclear", "point.confirmed", "rally.let", "score.corrected", "match.finished", "persona.changed",
     # robot arrival / failed call; the commentator decides what (if anything) to say
     "readiness.changed"}
)
GESTURE_EVENT_TYPES = frozenset({"point.confirmed", "match.finished"})

REPLAY_LIMIT = 2000  # max events replayed on reconnect before forcing a resync


class RefereeService:
    def __init__(
        self,
        store: SqliteEventStore,
        clock: Optional[Clock] = None,
        ids: Optional[IdGenerator] = None,
        *,
        automatic_scoring_enabled: bool = False,
    ):
        if automatic_scoring_enabled:
            raise ValueError("automatic scoring is not supported by the bootstrap engine")
        self.store = store
        self.clock = clock or SystemClock()
        self.ids = ids or UuidGenerator()
        self.broker = Broker()
        self._states: dict[str, engine.MatchState] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._create_lock = threading.RLock()
        self._commit_listeners: list[Callable[[str], None]] = []
        # test hook: called while the match lock is held during subscribe()
        self._subscribe_hook: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------ infra

    def add_commit_listener(self, fn: Callable[[str], None]) -> None:
        self._commit_listeners.append(fn)

    def _lock_for(self, match_id: str) -> threading.RLock:
        with self._locks_guard:
            lock = self._locks.get(match_id)
            if lock is None:
                lock = self._locks[match_id] = threading.RLock()
            return lock

    def _ctx(self) -> engine.EngineContext:
        return engine.EngineContext(now=self.clock.now(), new_id=self.ids.new_id)

    def _load_state(self, match_id: str) -> engine.MatchState:
        state = self._states.get(match_id)
        if state is not None:
            return state
        loaded = self.store.load_match(match_id)
        if loaded is None:
            raise NotFoundError("match_not_found", f"match {match_id} does not exist")
        _, state_json, _ = loaded
        state = engine.MatchState.model_validate_json(state_json)
        self._states[match_id] = state
        return state

    def rebuild_from_events(self, match_id: str) -> engine.MatchState:
        """Replay the event log (recovery / audit). Must equal the stored state."""
        events = self.store.events_for_match(match_id)
        if not events:
            raise NotFoundError("match_not_found", f"match {match_id} does not exist")
        return engine.fold(None, events)

    def _notify(self, match_id: str) -> None:
        for fn in self._commit_listeners:
            try:
                fn(match_id)
            except Exception:  # listeners never break scoring
                log.exception("commit listener failed")

    # ------------------------------------------------------------------ reads

    def get_snapshot(self, match_id: str) -> MatchSnapshot:
        with self._lock_for(match_id):
            return engine.snapshot(self._load_state(match_id))

    def get_state(self, match_id: str) -> engine.MatchState:
        with self._lock_for(match_id):
            return self._load_state(match_id).model_copy(deep=True)

    def is_point_active(self, match_id: str, event_id: str) -> bool:
        state = self.get_state(match_id)
        return any(p.event_id == event_id and p.active for p in state.points)

    def latest_match_id(self) -> Optional[str]:
        ids = self.store.list_matches()
        return ids[-1] if ids else None

    # ------------------------------------------------------------------ create

    def create_match(self, request: CreateMatchRequest, actor: str = "operator") -> tuple[MatchSnapshot, bool]:
        if actor != "operator":
            raise ForbiddenError("forbidden_actor", f"actor {actor!r} may not create matches")
        payload_hash = canonical_hash({"scope": "create", "actor": actor, "request": request.model_dump(mode="json")})
        with self._create_lock:
            existing = self.store.get_command(request.command_id)
            if existing is not None:
                if existing["scope"] == "create" and existing["payload_hash"] == payload_hash:
                    stored = CommandResult.model_validate_json(existing["response_json"])
                    return stored.snapshot, True
                raise ConflictError("command_id_conflict", "command_id was already used with different content")
            engine.validate_create(request)
            match_id = self.ids.new_id()
            ctx = self._ctx()
            events = engine.decide_create(request, match_id, ctx)
            state, events = engine.finalize(None, events)
            snap = engine.snapshot(state)
            result = CommandResult(command_id=request.command_id, duplicate=False, event_ids=[e.event_id for e in events], snapshot=snap)
            with self._lock_for(match_id):
                cursors = self.store.append_transaction(
                    scope="create",
                    command_id=request.command_id,
                    payload_hash=payload_hash,
                    match_id=match_id,
                    expected_previous_revision=0,
                    events=events,
                    snapshot=snap,
                    state_json=state.model_dump_json(),
                    response_json=result.model_dump_json(),
                    outbox_kinds=[(events[-1].event_id, "display")],
                )
                self._states[match_id] = state
                self._publish(match_id, events, cursors, snap)
        self._notify(match_id)
        return snap, False

    # ------------------------------------------------------------------ commands

    def handle(self, match_id: str, command: Any, actor: str) -> CommandResult:
        engine.check_permission(command, actor)
        scope = f"match:{match_id}"
        payload_hash = canonical_hash({"scope": scope, "actor": actor, "command": command.model_dump(mode="json")})
        with self._lock_for(match_id):
            existing = self.store.get_command(command.command_id)
            if existing is not None:
                if existing["scope"] == scope and existing["payload_hash"] == payload_hash:
                    stored = CommandResult.model_validate_json(existing["response_json"])
                    return stored.model_copy(update={"duplicate": True})
                raise ConflictError(
                    "command_id_conflict",
                    "command_id was already used with different content",
                    current_revision=self._states[match_id].revision if match_id in self._states else None,
                )
            state = self._load_state(match_id)
            engine.check_revision(state, command)
            events = engine.decide(state, command, actor, self._ctx())
            if events:
                new_state, events = engine.finalize(state, events)
            else:
                new_state = state
            snap = engine.snapshot(new_state)
            result = CommandResult(
                command_id=command.command_id, duplicate=False, event_ids=[e.event_id for e in events], snapshot=snap
            )
            outbox: list[tuple[str, str]] = []
            rally_decision = None
            if events:
                outbox.append((events[-1].event_id, "display"))
            for e in events:
                if e.type in SPEECH_EVENT_TYPES:
                    outbox.append((e.event_id, "speech"))
                if e.type in GESTURE_EVENT_TYPES:
                    outbox.append((e.event_id, "gesture"))
                if e.type in ("point.confirmed", "rally.let"):
                    rally_decision = (e.payload.rally_id, e.event_id)
            cursors = self.store.append_transaction(
                scope=scope,
                command_id=command.command_id,
                payload_hash=payload_hash,
                match_id=match_id,
                expected_previous_revision=state.revision,
                events=events,
                snapshot=snap,
                state_json=new_state.model_dump_json(),
                response_json=result.model_dump_json(),
                outbox_kinds=outbox,
                rally_decision=rally_decision,
            )
            self._states[match_id] = new_state
            if events:
                self._publish(match_id, events, cursors, snap)
        if events:
            self._notify(match_id)
        return result

    # ------------------------------------------------------------------ streaming

    def _publish(self, match_id: str, events: list[Any], cursors: list[int], snap: MatchSnapshot) -> None:
        msgs = [
            StreamMessage("match_event", StreamEventMessage(cursor=c, event=e).model_dump_json(), id=e.event_id)
            for e, c in zip(events, cursors)
        ]
        msgs.append(
            StreamMessage("snapshot", StreamSnapshotMessage(cursor=cursors[-1], snapshot=snap).model_dump_json())
        )
        self.broker.publish(match_id, msgs)

    def subscribe(
        self,
        match_id: str,
        subscriber: Subscriber,
        last_event_id: Optional[str] = None,
        force_resync: bool = False,
    ) -> list[StreamMessage]:
        """Register a subscriber atomically with its initial messages.

        Returns the messages to send first: either replayed events after
        ``last_event_id`` followed by a snapshot, or a (resync) snapshot.
        """
        with self._lock_for(match_id):
            state = self._load_state(match_id)
            snap = engine.snapshot(state)
            cursor = self.store.latest_cursor(match_id)
            initial: list[StreamMessage] = []
            resync = force_resync
            if last_event_id and not force_resync:
                found = self.store.cursor_of(last_event_id)
                if found is None or found[0] != match_id:
                    resync = True
                else:
                    missed = self.store.read_after(match_id, found[1], limit=REPLAY_LIMIT + 1)
                    if len(missed) > REPLAY_LIMIT:
                        resync = True
                    else:
                        for c, e in missed:
                            initial.append(
                                StreamMessage("match_event", StreamEventMessage(cursor=c, event=e).model_dump_json(), id=e.event_id)
                            )
            initial.append(
                StreamMessage(
                    "snapshot", StreamSnapshotMessage(cursor=cursor, resync=resync, snapshot=snap).model_dump_json()
                )
            )
            if self._subscribe_hook is not None:
                self._subscribe_hook()
            self.broker.add(match_id, subscriber)
            return initial

    def unsubscribe(self, match_id: str, subscriber: Subscriber) -> None:
        self.broker.remove(match_id, subscriber)


def result_json(result: CommandResult) -> dict:
    return json.loads(result.model_dump_json())
