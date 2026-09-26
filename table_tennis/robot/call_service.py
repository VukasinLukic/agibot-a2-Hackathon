"""Robot call orchestration: idempotency, single flight, persistence, readiness.

The browser never runs the navigation loop: the server owns the lifecycle and
the UI only polls ``GET /robot/calls/{id}``. A second concurrent call gets
``robot_busy``. After a process restart an unfinished call is marked failed
("outcome unknown") instead of being resumed automatically.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional

from table_tennis.contracts import RobotCall, RobotCallRequest, RobotStatus
from table_tennis.contracts.commands import RobotReadySetCommand
from table_tennis.contracts.primitives import ROBOT_CALL_TERMINAL_STATES
from table_tennis.core.errors import ConflictError, ForbiddenError, NotFoundError, RefereeError
from table_tennis.core.ports import Clock, IdGenerator, RobotNavigator, SystemClock, UuidGenerator
from table_tennis.core.service import RefereeService
from table_tennis.storage.sqlite_store import SqliteEventStore, canonical_hash

log = logging.getLogger("table_tennis.robot")

_READY_NS = uuid.UUID("7b0f1d6e-2a55-4c1e-9b1a-5d1f3c0a9e01")


class RobotCallService:
    def __init__(
        self,
        navigator: RobotNavigator,
        store: SqliteEventStore,
        referee: RefereeService,
        *,
        waypoints: dict[str, list[str]],
        ids: Optional[IdGenerator] = None,
        clock: Optional[Clock] = None,
    ):
        self.navigator = navigator
        self.store = store
        self.referee = referee
        self.waypoints = waypoints
        self.ids = ids or UuidGenerator()
        self.clock = clock or SystemClock()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ lifecycle

    def startup(self) -> int:
        """Unfinished calls from a previous process: outcome unknown -> failed."""
        n = 0
        abandon = getattr(self.navigator, "abandon", None)
        for row in self.store.robot_calls_active():
            call = RobotCall.model_validate_json(row["json"])
            # The robot may still be walking this goal. Stop it; never resume it.
            if abandon is not None:
                try:
                    abandon(call)
                except Exception:
                    log.exception("could not cancel the task of unfinished call %s", call.call_id)
            call = call.model_copy(
                update={
                    "state": "failed",
                    "updated_at": self.clock.now(),
                    "reason": "process restarted during call; outcome unknown - check the robot",
                }
            )
            self.store.save_robot_call(call.call_id, row["command_id"], row["payload_hash"], call.model_dump_json(), True)
            n += 1
        return n

    def _persist(self, call: RobotCall, command_id: str, payload_hash: str) -> None:
        self.store.save_robot_call(
            call.call_id, command_id, payload_hash, call.model_dump_json(), call.state in ROBOT_CALL_TERMINAL_STATES
        )

    # ------------------------------------------------------------------ API

    def request(self, req: RobotCallRequest, actor: str) -> tuple[RobotCall, bool]:
        if actor != "operator":
            raise ForbiddenError("forbidden_actor", f"actor {actor!r} may not call the robot")
        payload_hash = canonical_hash({"scope": "robot_call", "request": req.model_dump(mode="json")})
        with self._lock:
            row = self.store.robot_call_by_command(req.command_id)
            if row is not None:
                if row["payload_hash"] != payload_hash:
                    raise ConflictError("command_id_conflict", "command_id was already used with different content")
                return self.get(row["call_id"]), True
            if self.store.get_command(req.command_id) is not None:
                raise ConflictError("command_id_conflict", "command_id was already used in another scope")
            allowed = self.waypoints.get(req.table_id)
            if allowed is None or req.named_waypoint_id not in allowed:
                raise RefereeError(
                    "unknown_waypoint",
                    "table_id/named_waypoint_id is not in the configured allowlist",
                    details={"allowed": self.waypoints},
                    http_status=422,
                )
            if req.match_id is not None:
                self.referee.get_snapshot(req.match_id)  # 404 if unknown
            active = self._active_call()
            if active is not None:
                raise ConflictError(
                    "robot_busy",
                    "robot already has an active call",
                    details={"call_id": active.call_id, "state": active.state},
                )
            call = self.navigator.request_call(req, self.ids.new_id())
            self._persist(call, req.command_id, payload_hash)
            return call, False

    def _active_call(self) -> Optional[RobotCall]:
        for row in self.store.robot_calls_active():
            return RobotCall.model_validate_json(row["json"])
        return None

    def get(self, call_id: str) -> RobotCall:
        live = self.navigator.get_call(call_id)
        if live is not None:
            return live
        row = self.store.robot_call(call_id)
        if row is None:
            raise NotFoundError("call_not_found", f"robot call {call_id} does not exist")
        return RobotCall.model_validate_json(row["json"])

    def status(self) -> RobotStatus:
        return self.navigator.get_status()

    def confirm_route(self, call_id: str, actor: str) -> RobotCall:
        """Operator confirms the path is free. The fake navigator has nothing to hold."""
        if actor != "operator":
            raise ForbiddenError("forbidden_actor", f"actor {actor!r} may not clear a robot route")
        with self._lock:
            row = self.store.robot_call(call_id)
            if row is None:
                raise NotFoundError("call_not_found", f"robot call {call_id} does not exist")
            confirm = getattr(self.navigator, "confirm_route", None)
            if confirm is None:
                return self.get(call_id)
            call = confirm(call_id, actor)
            self._persist(call, row["command_id"], row["payload_hash"])
            return call

    def confirm_arrival(self, call_id: str, actor: str) -> RobotCall:
        """Operator saw the robot stop at the spot, when the robot could not prove it."""
        if actor != "operator":
            raise ForbiddenError("forbidden_actor", f"actor {actor!r} may not confirm robot arrival")
        with self._lock:
            row = self.store.robot_call(call_id)
            if row is None:
                raise NotFoundError("call_not_found", f"robot call {call_id} does not exist")
            confirm = getattr(self.navigator, "confirm_arrival", None)
            if confirm is None or self.navigator.get_call(call_id) is None:
                return self.get(call_id)
            before = self.navigator.get_call(call_id)
            call = confirm(call_id, actor)
            self._persist(call, row["command_id"], row["payload_hash"])
        if call.match_id and before is not None and before.state != "ready" and call.state == "ready":
            self._report_to_match(call)
        return call

    def cancel(self, call_id: str, command_id: str, actor: str) -> RobotCall:
        if actor != "operator":
            raise ForbiddenError("forbidden_actor", f"actor {actor!r} may not cancel robot calls")
        scope = f"robot_cancel:{call_id}"
        payload_hash = canonical_hash({"scope": scope})
        with self._lock:
            row = self.store.robot_call(call_id)
            if row is None:
                raise NotFoundError("call_not_found", f"robot call {call_id} does not exist")
            existing = self.store.get_command(command_id)
            if existing is not None:
                if existing["scope"] == scope and existing["payload_hash"] == payload_hash:
                    return self.get(call_id)
                raise ConflictError("command_id_conflict", "command_id was already used with different content")
            call = self.navigator.cancel(call_id) if self.navigator.get_call(call_id) else self.get(call_id)
            self._persist(call, row["command_id"], row["payload_hash"])
            self.store.record_scoped_command(command_id, scope, payload_hash, call.model_dump_json())
            return call

    def tick(self) -> list[RobotCall]:
        with self._lock:
            changed = self.navigator.tick()
            for call in changed:
                row = self.store.robot_call(call.call_id)
                if row is not None:
                    self._persist(call, row["command_id"], row["payload_hash"])
        for call in changed:
            if call.match_id and call.state in ("ready", "failed", "cancelled"):
                self._report_to_match(call)
        return changed

    def _report_to_match(self, call: RobotCall) -> None:
        """Tell the match about the call outcome (backend emits readiness.changed).

        ready -> robot_ready=True, reason robot_arrived
        failed -> robot_ready=False, reason robot_call_failed
        cancelled -> robot_ready=False, reason robot_call_cancelled
        One deterministic command_id per (call, outcome), so repeats are idempotent.
        """
        ready = call.state == "ready"
        reason = {"ready": "robot_arrived", "failed": "robot_call_failed", "cancelled": "robot_call_cancelled"}[call.state]
        actor = "robot"
        if call.reason == "operator_confirmed_arrival":
            # The robot did not prove arrival; the operator did.
            reason, actor = "manual_arrival", "operator"
        cmd = RobotReadySetCommand(
            command_id=str(uuid.uuid5(_READY_NS, f"{call.call_id}:{call.state}")),
            expected_revision=None,
            type="robot.ready.set",
            payload={"ready": ready, "reason": reason},
        )
        try:
            self.referee.handle(call.match_id, cmd, actor=actor)
        except RefereeError as exc:
            log.warning("could not report robot call %s to match %s: %s", call.state, call.match_id, exc)


class TickerThread:
    """Advances the simulated navigator on a timer (mock mode only)."""

    def __init__(self, service: RobotCallService, interval_s: float):
        self.service = service
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tt-robot-sim", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.service.tick()
            except Exception:
                log.exception("robot sim tick failed")
