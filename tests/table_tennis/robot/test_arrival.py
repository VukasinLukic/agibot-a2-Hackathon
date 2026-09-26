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

    nav = A2RobotNavigator(dry_run=False, transport=transport)
    call_id = "55555555-5555-4555-8555-555555555555"
    call = nav.request_call(_request(), call_id)
    assert call.state == "requested"
    assert call.native_task_id is None
    assert call.reason == "goal accepted; arrival not confirmed"
    cancelled = nav.cancel(call_id)
    assert cancelled.state == "cancel_requested"
    assert [action for action, _ in seen] == ["nav.request"]
