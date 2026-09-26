"""One referee walk, in the order the existing MissionRunner does not guarantee.

``MissionRunner.start`` arms walking before localization is checked, and
``_wait_terminal`` follows global planner state with no deadline. This session
does not import that runner and does not start a second one. It records the
calls a later real transport may make, after the checks.

Cleanup cancels the stored task and closes the pose lease. It does not disable
motors or balance, and it does not put the robot back into a walk mode after
an E-stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .readiness import NavFacts, assess

MissionState = Literal["idle", "moving", "arrived", "failed", "cancelled"]

# Standing cleanup must never emit these. hold.release that drops balance is
# the same class of mistake as switching motors off.
FORBIDDEN_CLEANUP = frozenset({"motor.disable", "balance.off", "walk.restore"})


@dataclass(frozen=True)
class MissionObservation:
    now_s: float
    pose_age_ms: Optional[int] = 0
    task_id: Optional[str] = None
    task_state: Optional[str] = None
    global_running: bool = False
    progress_mark: Optional[str] = None
    emergency_stop: bool = False


@dataclass(frozen=True)
class MissionVerdict:
    state: MissionState
    reason: str


@dataclass
class MissionSession:
    mission_timeout_s: float = 180.0
    stale_pose_ms: int = 5000
    progress_timeout_s: float = 45.0
    steps: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    route_clear: bool = False
    active: bool = False
    pose_open: bool = False
    task_id: Optional[str] = None
    started_at: Optional[float] = None
    last_progress_at: Optional[float] = None
    last_progress_mark: Optional[str] = None

    def confirm_route(self, actor: str) -> bool:
        """The operator confirms the path is free. An HTTP call is not that confirmation."""
        if actor != "operator":
            self.steps.append("route_refused")
            return False
        self.route_clear = True
        self.steps.append("route_confirmed")
        return True

    def begin(self, facts: NavFacts) -> str:
        """Preflight first. Walk hold and the pose lease come only after that."""
        if self.active:
            self.steps.append("refused:robot_busy")
            return "robot_busy"
        if not self.route_clear:
            self.steps.append("refused_without_route_confirmation")
            return "route_not_confirmed"
        self.steps.append("preflight")
        verdict = assess(facts)
        if verdict.state != "ready":
            self.steps.append(f"refused:{verdict.reason}")
            return verdict.reason or "not_ready"
        self.steps.append("arm_walk_hold")
        self.pose_open = True
        self.steps.append("pose_lease_open")
        self.active = True
        self.steps.append("mission_start")
        return "started"

    def note_task(self, task_id: Optional[str]) -> Optional[str]:
        text = "" if task_id is None else str(task_id).strip()
        if not text or text == "0":
            self.steps.append("ignored_task_0")
            return None
        self.task_id = text
        self.steps.append(f"task:{text}")
        return text

    def poll(self, obs: MissionObservation) -> MissionVerdict:
        if obs.emergency_stop:
            self.finish(cancel_task=True, estop=True)
            return MissionVerdict("failed", "emergency_stop")
        if not self.pose_open:
            self.finish(cancel_task=True, estop=False)
            return MissionVerdict("failed", "pose_lease_lost")
        if self.started_at is None:
            self.started_at = obs.now_s
            self.last_progress_at = obs.now_s
        if obs.now_s - self.started_at > self.mission_timeout_s:
            self.finish(cancel_task=True, estop=False)
            return MissionVerdict("failed", "navigation_timeout")
        if self.task_id and obs.task_id and obs.task_id != self.task_id:
            return MissionVerdict("moving", "native_task_mismatch")
        # Planner SUCCESS for this task is arrival telemetry, not a cancelled walk.
        if obs.task_state == "SUCCESS" and self.task_id and obs.task_id == self.task_id:
            return MissionVerdict("arrived", "goal_reached")
        if obs.pose_age_ms is None or obs.pose_age_ms > self.stale_pose_ms:
            self.finish(cancel_task=True, estop=False)
            return MissionVerdict("failed", "stale_pose")
        # A planner that is running with no id, or with someone else's id, is not this walk.
        # None == None must not count as our task.
        if obs.global_running and (not self.task_id or obs.task_id != self.task_id):
            return MissionVerdict("moving", "global_running_ignored")
        # No progress mark means no progress telemetry; the mission timeout still applies.
        if obs.progress_mark is None:
            pass
        elif obs.progress_mark != self.last_progress_mark:
            self.last_progress_mark = obs.progress_mark
            self.last_progress_at = obs.now_s
        elif self.last_progress_at is not None and obs.now_s - self.last_progress_at > self.progress_timeout_s:
            self.finish(cancel_task=True, estop=False)
            return MissionVerdict("failed", "no_progress")
        return MissionVerdict("moving", "in_progress")

    def finish(self, *, cancel_task: bool, estop: bool) -> list[str]:
        """Close the task and the pose lease. Leave a standing robot's balance alone."""
        done: list[str] = []
        if cancel_task and self.task_id and self.task_id != "0":
            done.append(f"nav.cancel:{self.task_id}")
        if self.pose_open:
            self.pose_open = False
            done.append("pose_lease_close")
        self.active = False
        # The next walk needs a new operator confirmation.
        self.route_clear = False
        done.append("estop_hold" if estop else "walk_hold_kept")
        self.actions.extend(done)
        return done
