"""Working fake robot adapters (dry-run). No SSH, HTTP-RPC, AIMA or ROS calls.

Every action is recorded in ``FakeOutputLog`` (and optionally a log file) with
match_id, event_id, revision, winner and robot side, so the mock demo shows
exactly what the real robot *would* do.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from table_tennis.contracts import MatchSnapshot, RobotCall, RobotCallRequest, RobotStatus
from table_tennis.contracts.primitives import ROBOT_CALL_TERMINAL_STATES
from table_tennis.core.fake_log import FakeOutputLog
from table_tennis.core.ports import Clock, SystemClock

from .gesture_output import GestureJob, GestureSession, MotionCoordinator
from .readiness import NavFacts, assess
from .score_display import ScoreboardSession

# --------------------------------------------------------------------------- display


class FakeScoreDisplay:
    """Dry-run scoreboard. One slot, latest revision, default face only on close."""

    def __init__(self, log: FakeOutputLog):
        self.log = log
        self.session = ScoreboardSession(self._play)
        self.renders = 0
        self.closed = False

    @property
    def latest(self) -> Optional[MatchSnapshot]:
        return self.session.held

    def _play(self, frame) -> None:
        self.renders += 1
        self.log.record(
            "display",
            f"{frame.primary} | {frame.secondary}",
            match_id=frame.match_id,
            revision=frame.revision,
        )

    def render(self, snapshot: MatchSnapshot) -> None:
        self.session.render(snapshot)

    def close(self) -> None:
        if self.session.release():
            self.log.record("display", "DEFAULT FACE")
        self.closed = True


# --------------------------------------------------------------------------- gestures


class FakeGestureOutput:
    """Maps winner_id -> robot_side_by_player -> gesture. Accepted is not completed."""

    def __init__(self, log: FakeOutputLog, coordinator: Optional[MotionCoordinator] = None):
        self.log = log
        self.session = GestureSession(self._play, self._neutral)
        self.performed: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        if coordinator is not None:
            coordinator.bind(self.session)

    def _play(self, job: GestureJob) -> None:
        if job.event_id == "greeting":
            text = "wave (greeting)"
            winner, side = None, None
        else:
            text = (
                f"{job.name} (winner={job.winner_id}, robot_side={job.robot_side}, "
                f"score={job.score[0]}:{job.score[1]})"
            )
            winner, side = job.winner_id, job.robot_side
        self.performed.append(
            {
                "match_id": job.match_id,
                "event_id": job.event_id,
                "revision": job.revision,
                "winner_id": winner,
                "robot_side": side,
                "gesture": job.name,
                "completed": True,
            }
        )
        self.log.record(
            "gesture",
            text,
            match_id=job.match_id,
            event_id=None if job.event_id == "greeting" else job.event_id,
            revision=job.revision or None,
        )

    def _neutral(self) -> None:
        return None

    def present_point(self, event: Any, snapshot: MatchSnapshot) -> None:
        self.session.present(event, snapshot)

    def cancel_pending(self, match_id: str) -> None:
        self.cancelled.append(match_id)
        self.session.cancel_pending(match_id)


# --------------------------------------------------------------------------- navigation


class FakeRobotNavigator:
    """Simulated call lifecycle: requested -> validating -> moving -> arrived -> ready.

    ``tick()`` advances every active call by one state (the mock app calls it on a
    timer). Waypoints listed in ``fail_waypoints`` end in ``failed``. Only one
    active call at a time (single flight).
    """

    FLOW = ["requested", "validating", "moving", "arrived", "ready"]

    def __init__(
        self,
        clock: Optional[Clock] = None,
        fail_waypoints: Optional[set[str]] = None,
        facts: Optional[NavFacts] = None,
        coordinator: Optional[MotionCoordinator] = None,
    ):
        self.clock = clock or SystemClock()
        self.fail_waypoints = set(fail_waypoints or ())
        self.facts = facts or NavFacts()
        self.coordinator = coordinator
        self.calls: dict[str, RobotCall] = {}
        self._lock = threading.Lock()
        self.native_calls: list[str] = []  # would-be native actions (for assertions)

    def _active(self) -> Optional[RobotCall]:
        for c in self.calls.values():
            if c.state not in ROBOT_CALL_TERMINAL_STATES:
                return c
        return None

    def active_call(self) -> Optional[RobotCall]:
        with self._lock:
            return self._active()

    def restore(self, call: RobotCall) -> None:
        with self._lock:
            self.calls[call.call_id] = call

    def request_call(self, request: RobotCallRequest, call_id: str) -> RobotCall:
        with self._lock:
            blocked = self._blocked_call(request, call_id)
            if blocked is not None:
                self.calls[call_id] = blocked
                return blocked
            call = RobotCall(
                call_id=call_id,
                table_id=request.table_id,
                named_waypoint_id=request.named_waypoint_id,
                state="requested",
                updated_at=self.clock.now(),
                reason="simulated call (mock mode, no robot command sent)",
                match_id=request.match_id,
                native_task_id=None,
                simulated=True,
            )
            self.calls[call_id] = call
            self.native_calls.append(f"WOULD navigate to {request.table_id}/{request.named_waypoint_id}")
            self._note_motion(call)
            return call

    def _blocked_call(self, request: RobotCallRequest, call_id: str) -> Optional[RobotCall]:
        """Preflight before any would-be motion. A failed or busy verdict sends nothing."""
        verdict = assess(self.facts)
        if verdict.state == "ready":
            return None
        return RobotCall(
            call_id=call_id,
            table_id=request.table_id,
            named_waypoint_id=request.named_waypoint_id,
            state="failed" if verdict.state == "failed" else "busy",
            updated_at=self.clock.now(),
            reason=verdict.reason,
            match_id=request.match_id,
            native_task_id=None,
            simulated=True,
        )

    def get_call(self, call_id: str) -> Optional[RobotCall]:
        with self._lock:
            return self.calls.get(call_id)

    def get_status(self) -> RobotStatus:
        with self._lock:
            active = self._active()
            last = list(self.calls.values())[-1] if self.calls else None
        if active is not None:
            nav = {
                "requested": "validating",
                "validating": "validating",
                "moving": "moving",
                "arrived": "arrived",
                "cancel_requested": "cancelling",
            }.get(active.state, "idle")
            return RobotStatus(
                call_id=active.call_id, availability="busy", navigation_state=nav, pose_age_ms=0, ready=False,
                reason="simulated", simulated=True,
            )
        ready = bool(last and last.state == "ready")
        return RobotStatus(
            call_id=last.call_id if last else None,
            availability="simulated",
            navigation_state="failed" if last and last.state == "failed" else ("arrived" if ready else "idle"),
            pose_age_ms=0 if ready else None,
            ready=ready,
            reason="simulated robot (mock mode)",
            simulated=True,
        )

    def cancel(self, call_id: str) -> RobotCall:
        with self._lock:
            call = self.calls[call_id]
            if call.state in ROBOT_CALL_TERMINAL_STATES:
                return call
            call = call.model_copy(
                update={"state": "cancel_requested", "updated_at": self.clock.now(), "reason": "cancel requested"}
            )
            self.calls[call_id] = call
            self.native_calls.append(f"WOULD cancel task for call {call_id}")
            return call

    def tick(self) -> list[RobotCall]:
        changed = []
        with self._lock:
            for cid, call in list(self.calls.items()):
                if call.state in ROBOT_CALL_TERMINAL_STATES:
                    continue
                if call.state == "cancel_requested":
                    new_state, reason = "cancelled", "cancel confirmed (simulated)"
                elif call.state == "validating" and call.named_waypoint_id in self.fail_waypoints:
                    new_state, reason = "failed", "simulated navigation failure"
                else:
                    new_state = self.FLOW[self.FLOW.index(call.state) + 1]
                    reason = "simulated" if new_state != "ready" else "simulated arrival; robot ready"
                call = call.model_copy(update={"state": new_state, "updated_at": self.clock.now(), "reason": reason})
                self.calls[cid] = call
                changed.append(call)
                self._note_motion(call)
        return changed

    def _note_motion(self, call: RobotCall) -> None:
        if self.coordinator is not None:
            self.coordinator.note_call_state(call.state, call.match_id)
