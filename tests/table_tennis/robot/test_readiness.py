"""Readiness verdict and the mock boundary for the navigation package."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.contracts import RobotCallRequest
from table_tennis.robot.a2_adapters import A2RobotNavigator, RealTransportMissing
from table_tennis.robot.fake import FakeRobotNavigator
from table_tennis.robot.readiness import NavFacts, assess, facts_from_reply


def test_free_walk_ready_robot_is_ready() -> None:
    verdict = assess(NavFacts())
    assert verdict.state == "ready"
    assert verdict.reason is None


def test_active_call_is_busy() -> None:
    verdict = assess(NavFacts(call_active=True))
    assert verdict.state == "busy"
    assert verdict.reason == "robot_busy"


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (NavFacts(emergency_stop=True), "emergency_stop"),
        (NavFacts(work_enabled=False), "not_enabled"),
        (NavFacts(collision=True), "collision"),
        (NavFacts(mc_action="McAction_DEFAULT"), "cannot_walk"),
        (NavFacts(localization_running=False), "localization_off"),
        (NavFacts(map_id=0), "no_map"),
        (NavFacts(map_id=None), "no_map"),
        (NavFacts(map_id=""), "no_map"),
        (NavFacts(pose_age_ms=None), "stale_pose"),
        (NavFacts(pose_age_ms=5001), "stale_pose"),
    ],
)
def test_preflight_failures_have_distinct_reasons(facts: NavFacts, reason: str) -> None:
    verdict = assess(facts)
    assert verdict.state == "failed"
    assert verdict.reason == reason


def test_emergency_stop_wins_over_a_busy_call() -> None:
    verdict = assess(NavFacts(emergency_stop=True, call_active=True))
    assert verdict.state == "failed"
    assert verdict.reason == "emergency_stop"


def test_pose_at_the_age_limit_is_still_fresh() -> None:
    assert assess(NavFacts(pose_age_ms=5000)).state == "ready"


def test_importing_robot_package_does_not_load_robot_services() -> None:
    before = set(sys.modules)
    import table_tennis.robot
    import table_tennis.robot.a2_adapters
    import table_tennis.robot.readiness

    loaded = set(sys.modules) - before
    assert not any(name == "robot_services" or name.startswith("robot_services.") for name in loaded)
    assert table_tennis.robot is not None


def test_dry_run_construction_and_call_ignore_a_failing_transport() -> None:
    def boom(action: str, args: dict) -> dict:
        raise AssertionError(f"transport called: {action} {args}")

    nav = A2RobotNavigator(dry_run=True, transport=boom)
    call = nav.request_call(
        RobotCallRequest(
            command_id="11111111-1111-4111-8111-111111111111",
            table_id="table-1",
            named_waypoint_id="referee",
        ),
        "22222222-2222-4222-8222-222222222222",
    )
    assert call.state == "requested"
    assert call.simulated is True
    assert nav.sent[0][0] == "nav.request"
    assert nav.tick()[0].state == "validating"
    assert nav.tick()[-1].state == "moving"


def test_dry_run_a2_call_reaches_ready_and_reports_simulated_status() -> None:
    nav = A2RobotNavigator(dry_run=True)
    call = nav.request_call(_request(), "88888888-8888-4888-8888-888888888888")
    for _ in range(4):
        changed = nav.tick()
    assert changed[-1].state == "ready"
    status = nav.get_status()
    assert status.ready is True
    assert status.availability == "simulated"
    assert status.simulated is True
    assert nav.sent == [("nav.request", {"table_id": "table-1", "waypoint": "referee-spot"})]


def test_real_mode_without_transport_fails_before_any_call() -> None:
    with pytest.raises(RealTransportMissing):
        A2RobotNavigator(dry_run=False)


def test_real_mode_calls_transport_only_when_a_method_runs() -> None:
    seen: list[str] = []

    def transport(action: str, args: dict) -> dict:
        seen.append(action)
        return {"ok": True}

    nav = A2RobotNavigator(
        dry_run=False,
        transport=transport,
        facts=NavFacts(),
        points=[{"point_id": 3, "name": "referee"}],
    )
    assert seen == []
    call_id = "33333333-3333-4333-8333-333333333333"
    held = nav.request_call(
        RobotCallRequest(
            command_id="11111111-1111-4111-8111-111111111111",
            table_id="table-1",
            named_waypoint_id="referee",
        ),
        call_id,
    )
    assert held.reason == "route_not_confirmed"
    assert seen == []
    nav.confirm_route(call_id, "operator")
    assert seen == ["nav.request"]


def _request() -> RobotCallRequest:
    return RobotCallRequest(
        command_id="11111111-1111-4111-8111-111111111111",
        table_id="table-1",
        named_waypoint_id="referee-spot",
    )


def test_fake_call_does_not_navigate_when_preflight_fails() -> None:
    nav = FakeRobotNavigator(facts=NavFacts(localization_running=False))
    call = nav.request_call(_request(), "44444444-4444-4444-8444-444444444444")
    assert call.state == "failed"
    assert call.reason == "localization_off"
    assert nav.native_calls == []
    assert nav.tick() == []


def test_fake_call_is_busy_without_navigation_when_robot_is_taken() -> None:
    nav = FakeRobotNavigator(facts=NavFacts(call_active=True))
    call = nav.request_call(_request(), "55555555-5555-4555-8555-555555555555")
    assert call.state == "busy"
    assert call.reason == "robot_busy"
    assert nav.native_calls == []


def test_fake_call_still_navigates_when_preflight_is_ready() -> None:
    nav = FakeRobotNavigator()
    call = nav.request_call(_request(), "66666666-6666-4666-8666-666666666666")
    assert call.state == "requested"
    assert nav.native_calls == ["WOULD navigate to table-1/referee-spot"]


def test_real_mode_does_not_treat_a_blank_snapshot_as_ready() -> None:
    seen: list[str] = []

    def transport(action: str, args: dict) -> dict:
        seen.append(action)
        return {"ok": True}

    nav = A2RobotNavigator(dry_run=False, transport=transport)
    call = nav.request_call(_request(), "99999999-9999-4999-8999-999999999999")
    assert call.state == "failed"
    assert call.reason == "not_enabled"
    assert seen == ["nav.facts"]
    status = nav.get_status()
    assert status.ready is False
    assert status.availability == "offline"
    assert status.reason != "A2 navigator scaffold is not connected"


def test_unknown_waypoint_name_is_not_sent() -> None:
    seen: list[str] = []

    def transport(action: str, args: dict) -> dict:
        seen.append(action)
        return {"points": [{"point_id": 4, "name": "other-spot"}]}

    nav = A2RobotNavigator(dry_run=False, transport=transport, facts=NavFacts(), mission=None)
    # Route is not held when mission is absent only if route_clear. Use a confirmed session.
    from table_tennis.robot.mission import MissionSession

    session = MissionSession()
    session.confirm_route("operator")
    nav.mission = session
    call = nav.request_call(_request(), "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert call.state == "failed"
    assert call.reason == "waypoint_not_on_map"
    assert seen == ["nav.points"]


def test_facts_from_reply_does_not_invent_a_ready_robot() -> None:
    verdict = assess(facts_from_reply({"ok": True}))
    assert verdict.state == "failed"
    ready = assess(
        facts_from_reply(
            {
                "work_enabled": True,
                "mc_action": "McAction_RL_LOCOMOTION_DEFAULT",
                "localization_running": True,
                "map_id": 7,
                "pose_age_ms": 12,
            }
        )
    )
    assert ready.state == "ready"


def test_a2_call_does_not_send_when_preflight_fails() -> None:
    nav = A2RobotNavigator(dry_run=False, transport=lambda action, args: {"ok": True}, facts=NavFacts(pose_age_ms=None))
    call = nav.request_call(_request(), "77777777-7777-4777-8777-777777777777")
    assert call.state == "failed"
    assert call.reason == "stale_pose"
    assert nav.sent == []
