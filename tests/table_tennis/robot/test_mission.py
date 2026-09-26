"""Phase 5 and 6: one existing mission, then the referee cycle."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.contracts import RobotCallRequest
from table_tennis.robot.a2_adapters import A2RobotNavigator
from table_tennis.robot.cycle import FULL_CYCLE, CycleInput, run_cycle
from table_tennis.robot.mission import FORBIDDEN_CLEANUP, MissionObservation, MissionSession
from table_tennis.robot.readiness import NavFacts


def _request() -> RobotCallRequest:
    return RobotCallRequest(
        command_id="11111111-1111-4111-8111-111111111111",
        table_id="table-1",
        named_waypoint_id="referee-spot",
    )


def test_walk_hold_comes_after_preflight_and_a_clear_route() -> None:
    session = MissionSession()
    assert session.begin(NavFacts()) == "route_not_confirmed"
    assert "arm_walk_hold" not in session.steps
    assert session.confirm_route("vision") is False
    assert session.confirm_route("operator") is True
    assert session.begin(NavFacts(emergency_stop=True)) == "emergency_stop"
    assert "arm_walk_hold" not in session.steps
    assert session.begin(NavFacts()) == "started"
    assert session.steps == [
        "refused_without_route_confirmation",
        "route_refused",
        "route_confirmed",
        "preflight",
        "refused:emergency_stop",
        "preflight",
        "arm_walk_hold",
        "pose_lease_open",
        "mission_start",
    ]
    assert session.begin(NavFacts()) == "robot_busy"


def test_global_running_is_not_progress_and_timeouts_close_without_motors() -> None:
    session = MissionSession(mission_timeout_s=10, progress_timeout_s=5)
    session.confirm_route("operator")
    assert session.begin(NavFacts()) == "started"
    assert session.note_task("0") is None
    assert session.note_task("44") == "44"
    ignored = session.poll(MissionObservation(now_s=1, global_running=True))
    assert ignored.reason == "global_running_ignored"
    assert ignored.state == "moving"
    stale = session.poll(MissionObservation(now_s=2, pose_age_ms=5001, task_id="44"))
    assert stale.reason == "stale_pose"
    assert session.pose_open is False
    assert set(session.actions).isdisjoint(FORBIDDEN_CLEANUP)
    assert "nav.cancel:44" in session.actions
    assert "pose_lease_close" in session.actions
    assert "walk.restore" not in session.actions


def test_navigation_timeout_and_estop_do_not_restore_walking() -> None:
    session = MissionSession(mission_timeout_s=10)
    session.confirm_route("operator")
    session.begin(NavFacts())
    session.note_task("7")
    session.poll(MissionObservation(now_s=0, task_id="7", progress_mark="a"))
    timed = session.poll(MissionObservation(now_s=11, task_id="7", progress_mark="b"))
    assert timed.reason == "navigation_timeout"
    assert "walk_hold_kept" in session.actions

    stopped = MissionSession()
    stopped.confirm_route("operator")
    stopped.begin(NavFacts())
    stopped.note_task("8")
    verdict = stopped.poll(MissionObservation(now_s=1, task_id="8", emergency_stop=True))
    assert verdict.reason == "emergency_stop"
    assert "estop_hold" in stopped.actions
    assert "walk.restore" not in stopped.actions
    assert set(stopped.actions).isdisjoint(FORBIDDEN_CLEANUP)


def test_no_progress_fails_the_mission() -> None:
    session = MissionSession(progress_timeout_s=5, mission_timeout_s=100)
    session.confirm_route("operator")
    session.begin(NavFacts())
    session.note_task("3")
    session.poll(MissionObservation(now_s=0, task_id="3", progress_mark="0"))
    stuck = session.poll(MissionObservation(now_s=6, task_id="3", progress_mark="0"))
    assert stuck.reason == "no_progress"


def test_unconfirmed_route_does_not_send_navigation() -> None:
    seen: list[str] = []
    nav = A2RobotNavigator(
        dry_run=False,
        transport=lambda action, args: seen.append(action) or {"ok": True, "task_id": 5},
        mission=MissionSession(),
    )
    call = nav.request_call(_request(), "22222222-2222-4222-8222-222222222222")
    assert call.state == "failed"
    assert call.reason == "route_not_confirmed"
    assert seen == []


def test_importing_the_mission_does_not_load_robot_services() -> None:
    before = set(sys.modules)
    import table_tennis.robot.cycle
    import table_tennis.robot.mission

    loaded = set(sys.modules) - before
    assert not any(name == "robot_services" or name.startswith("robot_services.") for name in loaded)
    assert table_tennis.robot.mission is not None


def test_full_cycle_keeps_the_score_when_an_output_is_missing() -> None:
    ready = run_cycle(CycleInput(score=(1, 0)))
    assert ready.steps == FULL_CYCLE
    assert ready.score == (1, 0) and ready.score_kept

    manual = run_cycle(CycleInput(nav_available=False, score=(1, 0)))
    assert manual.steps[0] == "manual_arrival"
    assert "call" not in manual.steps
    assert manual.score_kept

    quiet = run_cycle(CycleInput(gesture_available=False, score=(2, 1)))
    assert "gesture_failed" in quiet.steps
    assert "score_display" in quiet.steps and "speech" in quiet.steps
    assert quiet.score == (2, 1)

    blind = run_cycle(CycleInput(screen_available=False, score=(3, 2)))
    assert "screen_unavailable" in blind.steps and "web_scoreboard" in blind.steps
    assert "speech" in blind.steps
    assert "screen_release" not in blind.steps
    assert blind.score_kept

    offline = run_cycle(CycleInput(robot_online=False, score=(4, 3)))
    assert offline.steps[0] == "fake_adapter"
    assert offline.simulated
    assert offline.score == (4, 3)
