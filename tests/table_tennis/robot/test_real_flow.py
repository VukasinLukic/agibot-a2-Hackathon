"""Real-mode navigator against a transport that answers the way the A2 does.

The robot is not here, so these replies copy what a2_nav reports on the robot:
``PncServiceState_*`` names, task id 0 from some calls, a planner that keeps
RUNNING through an E-stop, and no progress field unless someone adds it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.contracts import RobotCall, RobotCallRequest
from table_tennis.core.ports import FixedClock
from table_tennis.robot.a2_adapters import A2RobotNavigator

POINTS = [{"point_id": 42, "name": "referee-spot"}, {"point_id": 7, "name": "door"}]
READY_FACTS = {
    "work_enabled": True,
    "mc_action": "McAction_RL_LOCOMOTION_DEFAULT",
    "localization_running": True,
    "map_id": 3,
    "pose_age_ms": 40,
    "points": POINTS,
}


class FakeA2:
    """Answers nav.* the way the robot would. Tests change ``status`` between ticks."""

    def __init__(self, task_id: object = 11):
        self.task_id = task_id
        self.facts = dict(READY_FACTS)
        self.status: dict = {}
        self.sent: list[tuple[str, dict]] = []

    def __call__(self, action: str, args: dict) -> dict:
        self.sent.append((action, args))
        if action == "nav.facts":
            return dict(self.facts)
        if action == "nav.points":
            return {"points": POINTS}
        if action == "nav.request":
            return {"code": 0, "task_id": self.task_id}
        if action == "nav.status":
            return dict(self.status)
        return {}

    def actions(self) -> list[str]:
        return [action for action, _ in self.sent]


def _request() -> RobotCallRequest:
    return RobotCallRequest(
        command_id="11111111-1111-4111-8111-111111111111",
        table_id="table-1",
        named_waypoint_id="referee-spot",
    )


def _nav(robot: FakeA2) -> A2RobotNavigator:
    return A2RobotNavigator(dry_run=False, transport=robot, clock=FixedClock())


CALL = "22222222-2222-4222-8222-222222222222"


def _walking(robot: FakeA2, nav: A2RobotNavigator) -> None:
    nav.request_call(_request(), CALL)
    nav.confirm_route(CALL, "operator")
    robot.status = {"task_id": 11, "task_state": "PncServiceState_RUNNING", "pose_age_ms": 30}
    assert nav.tick()[-1].state == "moving"


def test_robot_state_names_reach_ready() -> None:
    robot = FakeA2()
    nav = _nav(robot)
    _walking(robot, nav)
    sent = dict(robot.sent)["nav.request"]
    assert sent["target_id"] == 42 and sent["map_id"] == 3
    robot.status = {
        "task_id": 11,
        "task_state": "PncServiceState_SUCCESS",
        "pose_age_ms": 30,
        "within_tolerance": True,
        "settled": True,
    }
    done = nav.tick()[-1]
    assert (done.state, done.reason) == ("ready", "arrival_confirmed")


def test_success_without_pose_checks_waits_for_the_operator() -> None:
    robot = FakeA2()
    nav = _nav(robot)
    _walking(robot, nav)
    robot.status = {"task_id": 11, "task_state": "PncServiceState_SUCCESS", "pose_age_ms": 30}
    parked = nav.tick()[-1]
    assert (parked.state, parked.reason) == ("arrived", "need_operator_confirmation")
    assert nav.confirm_arrival(CALL, "vision").state == "arrived"
    done = nav.confirm_arrival(CALL, "operator")
    assert (done.state, done.reason) == ("ready", "operator_confirmed_arrival")


def test_cancel_waits_for_the_robot_state_name() -> None:
    robot = FakeA2()
    nav = _nav(robot)
    _walking(robot, nav)
    nav.cancel(CALL)
    assert ("nav.cancel", {"call_id": CALL, "native_task_id": "11"}) in robot.sent
    assert nav.tick() == []
    robot.status = {"task_id": 11, "task_state": "PncServiceState_CANCELED"}
    assert nav.tick()[-1].state == "cancelled"


def test_task_zero_cancel_is_not_confirmed_until_the_planner_stops() -> None:
    robot = FakeA2(task_id=0)
    nav = _nav(robot)
    nav.request_call(_request(), CALL)
    nav.confirm_route(CALL, "operator")
    nav.cancel(CALL)
    assert "nav.cancel" not in robot.actions()
    robot.status = {"global_running": True}
    assert nav.tick() == []
    robot.status = {}
    assert nav.tick() == []
    robot.status = {"global_running": False}
    assert nav.tick()[-1].state == "cancelled"


def test_cancel_before_the_route_is_confirmed_sends_nothing() -> None:
    robot = FakeA2()
    nav = _nav(robot)
    nav.request_call(_request(), CALL)
    nav.cancel(CALL)
    assert nav.tick()[-1].state == "cancelled"
    assert "nav.request" not in robot.actions()
    assert "nav.cancel" not in robot.actions()


def test_estop_cancels_the_task_and_does_not_restore_walking() -> None:
    robot = FakeA2()
    nav = _nav(robot)
    _walking(robot, nav)
    robot.status = {"task_id": 11, "task_state": "PncServiceState_RUNNING", "emergency_stop": True}
    failed = nav.tick()[-1]
    assert (failed.state, failed.reason) == ("failed", "emergency_stop")
    assert ("nav.cancel", {"call_id": CALL, "native_task_id": "11"}) in robot.sent
    assert not any("walk" in action or "arm" in action for action in robot.actions())
    assert "walk.restore" not in nav.mission.actions


def test_missing_progress_field_does_not_fail_a_normal_walk() -> None:
    robot = FakeA2()
    nav = _nav(robot)
    _walking(robot, nav)
    # 60 s without a progress field is past the 45 s no-progress limit, inside the 180 s timeout.
    for _ in range(60):
        nav.clock.monotonic()
    nav.tick()
    assert nav.get_call(CALL).state == "moving"


def test_blank_snapshot_and_unknown_name_never_send_a_goal() -> None:
    robot = FakeA2()
    robot.facts = {"ok": True}
    nav = _nav(robot)
    assert nav.request_call(_request(), CALL).reason == "not_enabled"
    robot.facts = dict(READY_FACTS, points=[{"point_id": 7, "name": "door"}])
    other = A2RobotNavigator(dry_run=False, transport=robot, clock=FixedClock())
    other.request_call(_request(), CALL)
    failed = other.confirm_route(CALL, "operator")
    assert failed.reason == "waypoint_not_on_map"
    assert "nav.request" not in robot.actions()
    assert other.mission.active is False


def test_restart_cancels_the_task_left_by_the_previous_process() -> None:
    robot = FakeA2()
    nav = _nav(robot)
    left = RobotCall(
        call_id=CALL,
        table_id="table-1",
        named_waypoint_id="referee-spot",
        state="moving",
        updated_at=FixedClock().now(),
        native_task_id="11",
        simulated=False,
    )
    nav.abandon(left)
    assert robot.sent == [("nav.cancel", {"call_id": CALL, "native_task_id": "11"})]
