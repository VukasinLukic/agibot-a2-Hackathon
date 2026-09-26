"""A2 adapter scaffold (owner: person 3, branch ``navigation``).

These classes implement the ports with a *dry-run* transport so the wiring can
be exercised today. The marked ``REAL:`` spots are where person 3 plugs in the
existing A2 code (robot_services.screen_manip, robot_services.gestures
motion_player, nav_missions / a2_nav). Nothing here imports those modules at
import time, and ``dry_run=False`` refuses to run until the real transport is
implemented and passed in explicitly.

Safety notes carried from 06_REUSE_AUDIT.md:
  * gesture "accepted" is not "completed"; never expose force_gesture;
  * navigation acceptance is not arrival; cancel needs the real task_id;
  * MissionRunner.start engages walking before its own checks - wrap it;
  * a programmatic cancel is not an E-stop.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from table_tennis.contracts import MatchSnapshot, RobotCall, RobotCallRequest, RobotStatus
from table_tennis.contracts.primitives import ROBOT_CALL_TERMINAL_STATES, NavigationState, RobotCallState
from table_tennis.core.ports import Clock, SystemClock

from .gesture_output import GESTURE_HINTS, UNPLAYED, GestureJob, GestureNotPlayed, GestureSession, MotionCoordinator
from .mission import MissionObservation, MissionSession
from .arrival import ArrivalFacts, assess_arrival
from .readiness import NavFacts, assess, facts_from_reply
from .fake import FakeRobotNavigator
from .score_display import ScoreboardSession

log = logging.getLogger("table_tennis.robot.a2")

Transport = Callable[[str, dict], Any]


class RealTransportMissing(RuntimeError):
    pass


def _native_task_id(reply: Any) -> Optional[str]:
    if not isinstance(reply, dict):
        return None
    raw = reply.get("task_id")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == "0":
        return None
    return text


# Real mode must not inherit the walk-ready defaults used by the mock.
_CLOSED_FACTS = NavFacts(
    work_enabled=False,
    mc_action="",
    localization_running=False,
    map_id=None,
    pose_age_ms=None,
)

_STOPPED_TASK_STATES = frozenset({"CANCELED", "CANCELLED", "SUCCESS", "FAILED", "FAILURE", "IDLE", "TIMEOUT"})
_STATE_PREFIXES = ("PncServiceState_", "CommonState_")


def _task_state(raw: Any) -> Optional[str]:
    """``PncServiceState_SUCCESS`` from the robot and ``SUCCESS`` mean the same state."""
    if raw is None:
        return None
    text = str(raw).strip()
    for prefix in _STATE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.upper() or None


def _target_id(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == "0":
        return None
    return text


def _match_target(points: list, name: str) -> Optional[str]:
    for point in points:
        if not isinstance(point, dict):
            continue
        if str(point.get("name") or "") != name:
            continue
        return _target_id(point.get("point_id"))
    return None


def _wire_target(text: str) -> str | int:
    return int(text) if text.isdigit() else text


def _dry_run_transport(action: str, args: dict) -> dict:
    log.info("[DRY-RUN A2] %s %s", action, args)
    return {"dry_run": True, "action": action}


class _A2Base:
    def __init__(self, *, dry_run: bool = True, transport: Optional[Transport] = None):
        # Dry-run never keeps a caller transport. A failing or real RPC passed by
        # mistake must not run while mode is still mock.
        if dry_run:
            chosen: Transport = _dry_run_transport
        elif transport is None:
            raise RealTransportMissing(
                f"{type(self).__name__}: real A2 transport is not implemented yet (person 3). "
                "Keep dry_run=True or pass an explicit transport."
            )
        else:
            chosen = transport
        self.dry_run = dry_run
        self.transport: Transport = chosen
        self.sent: list[tuple[str, dict]] = []

    def _send(self, action: str, args: dict) -> Any:
        self.sent.append((action, args))
        return self.transport(action, args)


class A2ScoreDisplay(_A2Base):
    """Persistent head-screen scoreboard. Latest revision wins per match."""

    def __init__(self, **kw: Any):
        super().__init__(**kw)
        self.session = ScoreboardSession(self._playback)

    def _playback(self, frame) -> None:
        # REAL: persistent scoreboard in robot_services.screen_manip.emoticon_screen,
        # slot emoticon_ct_message. Not the flash API, which restores the default face.
        self._send(
            "screen.show",
            {
                "slot": frame.slot_id,
                "primary": frame.primary,
                "secondary": frame.secondary,
                "revision": frame.revision,
            },
        )

    def render(self, snapshot: MatchSnapshot) -> None:
        self.session.render(snapshot)

    def close(self) -> None:
        # REAL: restore the default face only on explicit release.
        if self.session.release():
            self._send("screen.release", {"slot": self.session.slot_id})


class A2GestureOutput(_A2Base):
    def __init__(self, coordinator: Optional[MotionCoordinator] = None, **kw: Any):
        super().__init__(**kw)
        self.session = GestureSession(self._playback, self._neutral)
        if coordinator is not None:
            coordinator.bind(self.session)

    def _playback(self, job: GestureJob) -> None:
        # REAL: robot_services.gestures motion_player, resolved by display_name_en.
        # Catalog ids are not hardcoded; handshake is not a grasp.
        self._send(
            "gesture.play",
            {
                "name": job.name,
                "hint": GESTURE_HINTS.get(job.name, ""),
                "event_id": job.event_id,
                "revision": job.revision,
                "accepted_is_not_completed": True,
            },
        )

    def _neutral(self) -> None:
        # REAL: return to neutral. Do not hold a raised arm, and do not invert a started gesture.
        self._send("gesture.neutral", {})

    def present_point(self, event: Any, snapshot: MatchSnapshot) -> None:
        ack = self.session.present(event, snapshot)
        if ack.reason in UNPLAYED:
            raise GestureNotPlayed(ack.reason or "not_played")

    def cancel_pending(self, match_id: str) -> None:
        dropped = self.session.cancel_pending(match_id)
        if dropped:
            self._send("gesture.cancel_pending", {"match_id": match_id, "dropped": dropped})


class A2RobotNavigator(_A2Base):
    """Named-waypoint call to one known table. Real lifecycle is person 3's work."""

    def __init__(
        self,
        clock: Optional[Clock] = None,
        facts: Optional[NavFacts] = None,
        points: Optional[list[dict]] = None,
        coordinator: Optional[MotionCoordinator] = None,
        mission: Optional[MissionSession] = None,
        **kw: Any,
    ):
        super().__init__(**kw)
        self.clock = clock or SystemClock()
        self.coordinator = coordinator
        # Real mode waits for the operator and reads facts from the transport.
        # Dry-run keeps the simulated lifecycle and the walk-ready defaults.
        self._facts_supplied = facts is not None or self.dry_run
        self.facts = facts if facts is not None else (NavFacts() if self.dry_run else _CLOSED_FACTS)
        self._points_supplied = points is not None
        self._known_points: Optional[list] = list(points) if points is not None else None
        self.mission = mission if mission is not None or self.dry_run else MissionSession()
        self.calls: dict[str, RobotCall] = {}
        self._requests: dict[str, RobotCallRequest] = {}
        # Calls whose goal reached the robot. Only these can still be walking.
        self._sent: set[str] = set()
        self._simulator = (
            FakeRobotNavigator(clock=self.clock, facts=self.facts) if self.dry_run else None
        )
        if self._simulator is not None:
            self.calls = self._simulator.calls

    def _read_facts(self) -> NavFacts:
        if self._facts_supplied:
            return self.facts
        reply = self._send("nav.facts", {})
        data = reply if isinstance(reply, dict) else {}
        self.facts = facts_from_reply(data)
        if "points" in data and not self._points_supplied:
            raw = data.get("points")
            self._known_points = list(raw) if isinstance(raw, list) else []
        return self.facts

    def _points_for(self, name: str) -> Optional[str]:
        if self._known_points is None:
            reply = self._send("nav.points", {"map_id": self.facts.map_id})
            raw = reply.get("points") if isinstance(reply, dict) else None
            self._known_points = list(raw) if isinstance(raw, list) else []
        return _match_target(self._known_points, name)

    def _store(self, call: RobotCall) -> RobotCall:
        self.calls[call.call_id] = call
        if self.coordinator is not None:
            self.coordinator.note_call_state(call.state, call.match_id)
        return call

    def _refused(
        self, request: RobotCallRequest, call_id: str, state: RobotCallState, reason: Optional[str]
    ) -> RobotCall:
        return self._store(
            RobotCall(
                call_id=call_id,
                table_id=request.table_id,
                named_waypoint_id=request.named_waypoint_id,
                state=state,
                updated_at=self.clock.now(),
                reason=reason,
                match_id=request.match_id,
                simulated=self.dry_run,
            )
        )

    def request_call(self, request: RobotCallRequest, call_id: str) -> RobotCall:
        if self._simulator is not None:
            # Dry-run is a real lifecycle simulation: preflight can still
            # reject a call, while a ready call advances to arrival on ticks.
            call = self._simulator.request_call(request, call_id)
            if call.state == "requested":
                self._send("nav.request", {"table_id": request.table_id, "waypoint": request.named_waypoint_id})
            return call
        verdict = assess(self._read_facts())
        if verdict.state != "ready":
            return self._refused(
                request, call_id, "failed" if verdict.state == "failed" else "busy", verdict.reason
            )
        if self.mission is not None and not self.mission.route_clear:
            self._requests[call_id] = request
            return self._refused(request, call_id, "requested", "route_not_confirmed")
        return self._start(request, call_id)

    def confirm_route(self, call_id: str, actor: str) -> RobotCall:
        """Operator says the path is free. Until then nothing is sent."""
        call = self.calls[call_id]
        if self.mission is None or call.reason != "route_not_confirmed":
            return call
        if not self.mission.confirm_route(actor):
            return call
        request = self._requests.pop(call_id)
        return self._start(request, call_id)

    def _start(self, request: RobotCallRequest, call_id: str) -> RobotCall:
        facts = self._read_facts()
        if self.mission is not None:
            started = self.mission.begin(facts)
            if started != "started":
                state: RobotCallState = "busy" if started == "robot_busy" else "failed"
                return self._refused(request, call_id, state, started)
        target = self._points_for(request.named_waypoint_id)
        if target is None:
            if self.mission is not None and self.mission.active:
                self.mission.finish(cancel_task=False, estop=False)
            return self._refused(request, call_id, "failed", "waypoint_not_on_map")
        # One existing mission, after the route is confirmed. Acceptance is not arrival.
        reply = self._send(
            "nav.request",
            {
                "table_id": request.table_id,
                "waypoint": request.named_waypoint_id,
                "map_id": facts.map_id,
                "target_id": _wire_target(target),
            },
        )
        self._sent.add(call_id)
        # An accepted RPC is not arrival. task_id 0 does not identify a mission.
        task_id = _native_task_id(reply)
        if self.mission is not None:
            self.mission.note_task(task_id)
        return self._store(
            RobotCall(
                call_id=call_id,
                table_id=request.table_id,
                named_waypoint_id=request.named_waypoint_id,
                state="requested",
                updated_at=self.clock.now(),
                reason="goal accepted; arrival not confirmed",
                match_id=request.match_id,
                native_task_id=task_id,
                simulated=self.dry_run,
            )
        )

    def get_call(self, call_id: str) -> Optional[RobotCall]:
        if self._simulator is not None:
            return self._simulator.get_call(call_id)
        return self.calls.get(call_id)

    def get_status(self) -> RobotStatus:
        if self._simulator is not None:
            return self._simulator.get_status()
        facts = self._read_facts()
        verdict = assess(facts)
        active = next((c for c in self.calls.values() if c.state not in ROBOT_CALL_TERMINAL_STATES), None)
        if active is not None:
            nav: NavigationState = {
                "requested": "validating",
                "validating": "validating",
                "moving": "moving",
                "arrived": "arrived",
                "cancel_requested": "cancelling",
            }.get(active.state, "idle")
            return RobotStatus(
                call_id=active.call_id,
                availability="busy",
                navigation_state=nav,
                pose_age_ms=facts.pose_age_ms,
                ready=False,
                reason=active.reason,
                simulated=False,
            )
        ready = verdict.state == "ready"
        return RobotStatus(
            availability="available" if ready else "offline",
            navigation_state="idle" if ready else "failed",
            pose_age_ms=facts.pose_age_ms,
            ready=ready,
            reason=None if ready else verdict.reason,
            simulated=False,
        )

    def cancel(self, call_id: str) -> RobotCall:
        if self._simulator is not None:
            call = self._simulator.cancel(call_id)
            if call.state == "cancel_requested":
                self._send("nav.cancel", {"call_id": call_id})
            return call
        # REAL: Cancel is a request. ``cancelled`` waits until the robot confirms.
        # task_id 0 is not a mission id and is not sent.
        call = self.calls[call_id]
        if call.state in ROBOT_CALL_TERMINAL_STATES:
            return call
        self._requests.pop(call_id, None)
        self._cancel_native(call)
        return self._store(self._copy(call, "cancel_requested", "cancel requested"))

    def confirm_arrival(self, call_id: str, actor: str) -> RobotCall:
        """Operator saw the robot stop at the spot. Only an ``arrived`` call can end this way."""
        call = self.calls[call_id]
        if self._simulator is not None:
            return self._simulator.confirm_arrival(call_id, actor)
        if actor != "operator" or call.state != "arrived":
            return call
        return self._store(self._copy(call, "ready", "operator_confirmed_arrival"))

    def abandon(self, call: RobotCall) -> None:
        """A previous process left this call open. Stop its task; do not resume it."""
        if self._simulator is not None:
            return
        self._cancel_native(call)

    def tick(self) -> list[RobotCall]:
        if self._simulator is not None:
            return self._simulator.tick()
        changed: list[RobotCall] = []
        for call in list(self.calls.values()):
            if call.state in ROBOT_CALL_TERMINAL_STATES or call.reason == "route_not_confirmed":
                continue
            updated = self._advance(call)
            if (
                updated.state == call.state
                and updated.reason == call.reason
                and updated.native_task_id == call.native_task_id
            ):
                continue
            changed.append(self._store(updated))
        return changed

    def _advance(self, call: RobotCall) -> RobotCall:
        if call.state == "cancel_requested":
            return self._advance_cancel(call)
        reply = self._status(call)
        if bool(reply.get("emergency_stop")):
            # The planner can keep this task RUNNING through an E-stop. Cancel it so
            # the robot does not resume the old goal after recovery. Walk is not restored.
            if self.mission is not None and self.mission.active:
                self.mission.finish(cancel_task=True, estop=True)
            self._cancel_native(call)
            return self._copy(call, "failed", "emergency_stop")
        if call.state == "arrived" or (self.mission is not None and not self.mission.active and call.state != "requested"):
            return self._advance_arrival(call, reply)
        if self.mission is None:
            return call
        verdict = self.mission.poll(self._observation(reply))
        if verdict.state == "failed":
            self._cancel_native(call)
            return self._copy(call, "failed", verdict.reason)
        if verdict.state == "arrived":
            self.mission.finish(cancel_task=False, estop=False)
            return self._advance_arrival(call, reply)
        return self._copy(call, "moving", verdict.reason)

    def _advance_cancel(self, call: RobotCall) -> RobotCall:
        if call.call_id in self._sent:
            reply = self._status(call)
            if bool(reply.get("emergency_stop")):
                if self.mission is not None and self.mission.active:
                    self.mission.finish(cancel_task=True, estop=True)
                return self._copy(call, "failed", "emergency_stop")
            if not self._robot_stopped(call, reply):
                return call
        if self.mission is not None and self.mission.active:
            self.mission.finish(cancel_task=False, estop=False)
        return self._copy(call, "cancelled", "cancel confirmed")

    def _robot_stopped(self, call: RobotCall, reply: dict) -> bool:
        """With a task id, that task must be stopped. Without one, the planner must say it is not running."""
        if call.native_task_id:
            if _native_task_id(reply) not in (None, call.native_task_id):
                return False
            return _task_state(reply.get("task_state")) in _STOPPED_TASK_STATES
        # No id to follow: only an explicit "not running" confirms the stop.
        return "global_running" in reply and reply.get("global_running") is False

    def _advance_arrival(self, call: RobotCall, reply: dict) -> RobotCall:
        if not call.native_task_id:
            return self._copy(call, "arrived", "need_operator_confirmation")
        observed = _native_task_id(reply)
        verdict = assess_arrival(
            ArrivalFacts(
                expected_task_id=call.native_task_id,
                observed_task_id=observed,
                pose_age_ms=reply.get("pose_age_ms") if "pose_age_ms" in reply else None,
                within_tolerance=reply.get("within_tolerance") if "within_tolerance" in reply else None,
                settled=reply.get("settled") if "settled" in reply else None,
            )
        )
        return self._copy(call, verdict.state, verdict.reason)

    def _status(self, call: RobotCall) -> dict:
        reply = self._send("nav.status", {"call_id": call.call_id, "native_task_id": call.native_task_id})
        return reply if isinstance(reply, dict) else {}

    def _observation(self, reply: dict) -> MissionObservation:
        return MissionObservation(
            now_s=self.clock.monotonic(),
            pose_age_ms=reply.get("pose_age_ms") if "pose_age_ms" in reply else None,
            task_id=_native_task_id(reply),
            task_state=_task_state(reply.get("task_state")),
            global_running=bool(reply.get("global_running")),
            progress_mark=None if "progress_mark" not in reply else reply.get("progress_mark"),
            emergency_stop=bool(reply.get("emergency_stop")),
        )

    def _cancel_native(self, call: RobotCall) -> None:
        # task_id 0 is not a mission id and is never sent as a cancel target.
        task_id = _native_task_id({"task_id": call.native_task_id})
        if task_id:
            self._send("nav.cancel", {"call_id": call.call_id, "native_task_id": task_id})

    def _copy(self, call: RobotCall, state: RobotCallState, reason: str) -> RobotCall:
        return call.model_copy(update={"state": state, "updated_at": self.clock.now(), "reason": reason})
