"""Phase 4: an accepted goal is not arrival."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.contracts import RobotCallRequest
from table_tennis.robot.a2_adapters import A2RobotNavigator
from table_tennis.robot.arrival import ArrivalFacts, assess_arrival
from table_tennis.robot.fake import FakeRobotNavigator
from table_tennis.robot.readiness import NavFacts


def _request() -> RobotCallRequest:
    return RobotCallRequest(
        command_id="11111111-1111-4111-8111-111111111111",
        table_id="table-1",
        named_waypoint_id="referee-spot",
    )


def _walk(nav: FakeRobotNavigator, call_id: str) -> list[str]:
    states = [nav.get_call(call_id).state]
    for _ in range(6):
        nav.tick()
        state = nav.get_call(call_id).state
        if state == states[-1] and state in {"arrived", "ready", "failed", "cancelled"}:
            break
        states.append(state)
    return states


def test_confirmed_telemetry_reaches_ready_with_a_task_id() -> None:
    nav = FakeRobotNavigator()
    call_id = "22222222-2222-4222-8222-222222222222"
    nav.request_call(_request(), call_id)
    assert _walk(nav, call_id) == ["requested", "validating", "moving", "arrived", "ready"]
    done = nav.get_call(call_id)
    assert done.reason == "arrival_confirmed"
    assert done.native_task_id == f"sim-{call_id}"
    assert done.native_task_id != "0"


def test_missing_telemetry_stays_arrived_for_the_operator() -> None:
    nav = FakeRobotNavigator(arrival=ArrivalFacts())
    call_id = "33333333-3333-4333-8333-333333333333"
    nav.request_call(_request(), call_id)
    assert _walk(nav, call_id) == ["requested", "validating", "moving", "arrived"]
    parked = nav.get_call(call_id)
    assert parked.state == "arrived"
    assert parked.reason == "need_operator_confirmation"
    assert nav.tick() == []


def test_task_mismatch_fails_and_does_not_become_ready() -> None:
    nav = FakeRobotNavigator(
        arrival=ArrivalFacts(
            expected_task_id="9",
            observed_task_id="8",
            pose_age_ms=0,
            within_tolerance=True,
            settled=True,
        )
    )
    call_id = "44444444-4444-4444-8444-444444444444"
    nav.request_call(_request(), call_id)
    assert _walk(nav, call_id)[-1] == "failed"
    assert nav.get_call(call_id).reason == "native_task_mismatch"


def test_stale_pose_outside_tolerance_and_motion_do_not_count_as_ready() -> None:
    stale = assess_arrival(
        ArrivalFacts(expected_task_id="1", observed_task_id="1", pose_age_ms=5001, within_tolerance=True, settled=True)
    )
    off = assess_arrival(
        ArrivalFacts(expected_task_id="1", observed_task_id="1", pose_age_ms=0, within_tolerance=False, settled=True)
    )
    moving = assess_arrival(
        ArrivalFacts(expected_task_id="1", observed_task_id="1", pose_age_ms=0, within_tolerance=True, settled=False)
    )
    assert (stale.state, stale.reason) == ("arrived", "stale_pose")
    assert (off.state, off.reason) == ("arrived", "outside_tolerance")
    assert (moving.state, moving.reason) == ("arrived", "not_settled")


def test_task_id_zero_is_not_a_mission() -> None:
    verdict = assess_arrival(
        ArrivalFacts(expected_task_id="0", observed_task_id="0", pose_age_ms=0, within_tolerance=True, settled=True)
    )
    assert verdict.reason == "need_operator_confirmation"


def test_a2_cancel_is_a_request_and_ignores_task_zero() -> None:
    seen: list[tuple[str, dict]] = []

    def transport(action: str, args: dict) -> dict:
        seen.append((action, args))
        return {"ok": True, "task_id": 0}

    nav = A2RobotNavigator(
        dry_run=False,
        transport=transport,
        facts=NavFacts(),
        points=[{"point_id": 9, "name": "referee-spot"}],
    )
    call_id = "55555555-5555-4555-8555-555555555555"
    held = nav.request_call(_request(), call_id)
    assert held.reason == "route_not_confirmed"
    assert seen == []
    call = nav.confirm_route(call_id, "operator")
    assert call.state == "requested"
    assert call.native_task_id is None
    assert call.reason == "goal accepted; arrival not confirmed"
    cancelled = nav.cancel(call_id)
    assert cancelled.state == "cancel_requested"
    assert [action for action, _ in seen] == ["nav.request"]
    # No id to cancel and no "not running" from the planner: the stop is not confirmed.
    assert nav.tick() == []
    assert nav.get_call(call_id).state == "cancel_requested"
    assert "nav.cancel" not in [action for action, _ in seen]


def test_real_status_reaches_ready_only_with_matching_telemetry() -> None:
    replies: dict[str, dict] = {
        "nav.request": {"task_id": 5},
        "nav.status": {
            "task_id": "5",
            "task_state": "RUNNING",
            "pose_age_ms": 20,
            "progress_mark": "a",
        },
    }

    def transport(action: str, args: dict) -> dict:
        if action == "nav.request":
            assert args["target_id"] == 42
            assert args["map_id"] == "1"
            assert args["waypoint"] == "referee-spot"
        return replies.get(action, {})

    nav = A2RobotNavigator(
        dry_run=False,
        transport=transport,
        facts=NavFacts(map_id="1"),
        points=[{"point_id": 42, "name": "referee-spot"}],
    )
    call_id = "66666666-6666-4666-8666-666666666666"
    held = nav.request_call(_request(), call_id)
    assert held.reason == "route_not_confirmed"
    assert nav.tick() == []
    started = nav.confirm_route(call_id, "operator")
    assert started.native_task_id == "5"
    assert started.reason == "goal accepted; arrival not confirmed"
    moving = nav.tick()
    assert moving[-1].state == "moving"
    replies["nav.status"] = {
        "task_id": "5",
        "task_state": "SUCCESS",
        "pose_age_ms": 20,
        "within_tolerance": True,
        "settled": True,
    }
    arrived = nav.tick()
    assert arrived[-1].state == "ready"
    assert arrived[-1].reason == "arrival_confirmed"
    status = nav.get_status()
    assert status.availability == "available"
    assert status.simulated is False


def test_cancel_of_a_running_task_waits_until_the_robot_stops() -> None:
    running = {"task_state": "RUNNING", "task_id": "5", "pose_age_ms": 10, "progress_mark": "a"}

    def transport(action: str, args: dict) -> dict:
        if action == "nav.request":
            return {"task_id": 5}
        if action == "nav.status":
            return dict(running)
        return {}

    nav = A2RobotNavigator(
        dry_run=False,
        transport=transport,
        facts=NavFacts(),
        points=[{"point_id": 42, "name": "referee-spot"}],
    )
    call_id = "77777777-7777-4777-8777-777777777777"
    nav.request_call(_request(), call_id)
    nav.confirm_route(call_id, "operator")
    nav.tick()
    assert nav.cancel(call_id).state == "cancel_requested"
    assert nav.tick() == []
    running["task_state"] = "CANCELED"
    done = nav.tick()
    assert done[-1].state == "cancelled"
    assert nav.get_status().availability == "available"
