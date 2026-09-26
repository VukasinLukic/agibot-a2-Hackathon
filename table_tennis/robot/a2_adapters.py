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
from table_tennis.contracts.primitives import ROBOT_CALL_TERMINAL_STATES
from table_tennis.core.ports import Clock, SystemClock

from .gesture_output import GESTURE_HINTS, GestureJob, GestureSession, MotionCoordinator
from .readiness import NavFacts, assess
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
        self.session.present(event, snapshot)

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
        coordinator: Optional[MotionCoordinator] = None,
        **kw: Any,
    ):
        super().__init__(**kw)
        self.clock = clock or SystemClock()
        self.facts = facts or NavFacts()
        self.coordinator = coordinator
        self.calls: dict[str, RobotCall] = {}
        self._simulator = (
            FakeRobotNavigator(clock=self.clock, facts=self.facts) if self.dry_run else None
        )
        if self._simulator is not None:
            self.calls = self._simulator.calls

    def request_call(self, request: RobotCallRequest, call_id: str) -> RobotCall:
        if self._simulator is not None:
            # Dry-run is a real lifecycle simulation: preflight can still
            # reject a call, while a ready call advances to arrival on ticks.
            call = self._simulator.request_call(request, call_id)
            if call.state == "requested":
                self._send("nav.request", {"table_id": request.table_id, "waypoint": request.named_waypoint_id})
            return call
        # Facts stand in for a2_nav.preflight. A later real-mode reader fills them
        # from that client. Nothing is sent until the verdict is ready.
        verdict = assess(self.facts)
        if verdict.state != "ready":
            call = RobotCall(
                call_id=call_id,
                table_id=request.table_id,
                named_waypoint_id=request.named_waypoint_id,
                state="failed" if verdict.state == "failed" else "busy",
                updated_at=self.clock.now(),
                reason=verdict.reason,
                match_id=request.match_id,
                simulated=self.dry_run,
            )
            self.calls[call_id] = call
            return call
        # REAL: operator route confirmation, then start ONE mission and remember
        # the native task_id. Dry-run still does not drive the robot.
        reply = self._send("nav.request", {"table_id": request.table_id, "waypoint": request.named_waypoint_id})
        # An accepted RPC is not arrival. task_id 0 does not identify a mission.
        task_id = _native_task_id(reply)
        call = RobotCall(
            call_id=call_id,
            table_id=request.table_id,
            named_waypoint_id=request.named_waypoint_id,
            state="failed" if self.dry_run else "requested",
            updated_at=self.clock.now(),
            reason="dry-run: A2 navigation not wired yet" if self.dry_run else "goal accepted; arrival not confirmed",
            match_id=request.match_id,
            native_task_id=task_id,
            simulated=self.dry_run,
        )
        self.calls[call_id] = call
        return call

    def get_call(self, call_id: str) -> Optional[RobotCall]:
        if self._simulator is not None:
            return self._simulator.get_call(call_id)
        return self.calls.get(call_id)

    def get_status(self) -> RobotStatus:
        if self._simulator is not None:
            return self._simulator.get_status()
        return RobotStatus(
            availability="offline",
            navigation_state="idle",
            ready=False,
            reason="A2 navigator scaffold is not connected",
            simulated=self.dry_run,
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
        task_id = call.native_task_id
        if task_id and task_id != "0":
            self._send("nav.cancel", {"call_id": call_id, "native_task_id": task_id})
        call = call.model_copy(
            update={
                "state": "cancel_requested",
                "updated_at": self.clock.now(),
                "reason": "cancel requested",
            }
        )
        self.calls[call_id] = call
        return call

    def tick(self) -> list[RobotCall]:
        if self._simulator is not None:
            return self._simulator.tick()
        # REAL: poll task status with deadline + stale-pose watchdog.
        return []
