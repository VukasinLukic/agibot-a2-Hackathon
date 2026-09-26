"""Phase 3: one short gesture per point, greeting once, no hardware."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from table_tennis.contracts import (
    CourtEndByPlayer,
    GameRules,
    MatchSnapshot,
    Player,
    Readiness,
    RobotCallRequest,
    RobotSideByPlayer,
    ScoreByPlayer,
)
from table_tennis.core.fake_log import FakeOutputLog
from table_tennis.robot.a2_adapters import A2GestureOutput
from table_tennis.robot.fake import FakeGestureOutput, FakeRobotNavigator
from table_tennis.robot.gesture_output import GESTURE_HINTS, GestureNotPlayed, GestureSession, MotionCoordinator

MATCH = "00000000-0000-4000-8000-0000000000c3"
OTHER = "00000000-0000-4000-8000-0000000000d4"


def _snap(match_id: str = MATCH, *, revision: int = 1, status: str = "between_rallies", p1_side: str = "left") -> MatchSnapshot:
    p2_side = "right" if p1_side == "left" else "left"
    return MatchSnapshot(
        match_id=match_id,
        revision=revision,
        status=status,  # type: ignore[arg-type]
        players=[Player(id="p1", display_name="Ana"), Player(id="p2", display_name="Marko")],
        config=GameRules(),
        score_by_player=ScoreByPlayer(p1=1, p2=0),
        first_server_id="p1",
        server_id="p2",
        winner_id=None,
        assignment_version=1 if p1_side == "left" else 2,
        court_end_by_player=CourtEndByPlayer(p1="end_a", p2="end_b"),
        robot_side_by_player=RobotSideByPlayer(p1=p1_side, p2=p2_side),  # type: ignore[arg-type]
        calibration_id=None,
        active_rally_id=None,
        active_proposal_id=None,
        persona="regular",
        scoring_mode="assisted",
        ready=Readiness(),
        updated_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )


def _point(revision: int, winner: str = "p1", match_id: str = MATCH, event_id: str | None = None):
    return SimpleNamespace(
        type="point.confirmed",
        event_id=event_id or str(uuid4()),
        match_id=match_id,
        revision=revision,
        payload=SimpleNamespace(winner_id=winner),
    )


def test_latest_point_wins_and_maps_the_robot_side() -> None:
    played: list[str] = []
    session = GestureSession(lambda job: played.append(job.name), lambda: None)
    first = session.accept(_point(2), _snap(revision=2))
    second = session.accept(_point(4, winner="p2"), _snap(revision=4))
    assert first.accepted and not first.completed
    assert second.accepted and not second.completed
    shown = session.pump()
    assert played == ["point right"]
    assert shown is not None and shown.completed and shown.robot_side == "right"
    assert session.neutral_releases == 1


def test_duplicate_event_is_not_played_twice() -> None:
    played: list[str] = []
    session = GestureSession(lambda job: played.append(job.event_id), lambda: None)
    event = _point(3)
    session.present(event, _snap(revision=3))
    again = session.present(event, _snap(revision=3))
    assert played == [event.event_id]
    assert again.reason == "duplicate" and not again.completed


def test_undo_drops_pending_and_a_started_gesture_is_not_reversed() -> None:
    played: list[str] = []
    session = GestureSession(lambda job: played.append(job.name), lambda: None)
    session.accept(_point(5), _snap(revision=5))
    assert session.cancel_pending(MATCH) == 1
    assert session.pump() is None
    assert played == []
    session.present(_point(6), _snap(revision=6))
    assert played == ["point left"]
    assert session.cancel_pending(MATCH) == 0
    assert played == ["point left"]
    assert session.neutral_releases == 1


def test_side_swap_is_applied_before_playback() -> None:
    played: list[str] = []
    session = GestureSession(lambda job: played.append(job.name), lambda: None)
    session.accept(_point(2), _snap(revision=2, p1_side="left"))
    session.note_snapshot(_snap(revision=3, p1_side="right"))
    session.pump()
    assert played == ["point right"]


def test_no_gesture_during_rally_navigation_or_after_ttl() -> None:
    clock = {"t": 0.0}
    played: list[str] = []
    session = GestureSession(lambda job: played.append(job.name), lambda: None, ttl_s=20, now=lambda: clock["t"])
    rally = session.present(_point(2), _snap(revision=2, status="rally"))
    assert rally.reason == "active_rally" and played == []
    session.set_navigating(True)
    walking = session.present(_point(3), _snap(revision=3))
    assert walking.reason == "navigating" and played == []
    session.set_navigating(False)
    session.accept(_point(4), _snap(revision=4))
    clock["t"] = 25
    expired = session.pump()
    assert expired is not None and expired.reason == "expired" and not expired.completed
    assert played == []


def test_active_rally_discards_a_queued_gesture() -> None:
    played: list[str] = []
    session = GestureSession(lambda job: played.append(job.name), lambda: None)
    session.accept(_point(3), _snap(revision=3))
    session.note_snapshot(_snap(revision=4, status="rally"))
    held = session.pump()
    assert held is not None and held.reason == "active_rally"
    assert played == []


def test_wave_once_on_arrival_and_not_a_handshake() -> None:
    played: list[str] = []
    session = GestureSession(lambda job: played.append(job.name), lambda: None)
    assert "handshake" not in GESTURE_HINTS
    assert session.greet(MATCH).completed
    assert session.greet(MATCH).reason == "duplicate"
    assert played == ["wave"]
    assert session.neutral_releases == 1


def test_arrival_waves_once_then_a_point_can_play() -> None:
    log = FakeOutputLog()
    motion = MotionCoordinator()
    gesture = FakeGestureOutput(log, coordinator=motion)
    navigator = FakeRobotNavigator(coordinator=motion)
    call = navigator.request_call(
        RobotCallRequest(
            command_id=str(uuid4()),
            table_id="table-1",
            named_waypoint_id="referee-spot",
            match_id=MATCH,
        ),
        str(uuid4()),
    )
    blocked = gesture.session.present(_point(2), _snap(revision=2))
    assert call.state == "requested"
    assert blocked.reason == "navigating"
    with pytest.raises(GestureNotPlayed):
        gesture.present_point(_point(2), _snap(revision=2))
    for _ in range(4):
        navigator.tick()
    assert navigator.get_call(call.call_id).state == "ready"  # type: ignore[union-attr]
    assert [item["gesture"] for item in gesture.performed] == ["wave"]
    gesture.present_point(_point(5), _snap(revision=5))
    assert [item["gesture"] for item in gesture.performed] == ["wave", "point left"]
    assert "winner=p1" in (log.view().gestures[-1].text)
    assert "robot_side=left" in log.view().gestures[-1].text


def test_dry_run_plays_only_the_latest_then_returns_neutral() -> None:
    screen = A2GestureOutput(dry_run=True)
    screen.session.accept(_point(1), _snap(revision=1))
    screen.session.accept(_point(2, winner="p2"), _snap(revision=2))
    screen.session.pump()
    plays = [item for item in screen.sent if item[0] == "gesture.play"]
    assert len(plays) == 1
    assert plays[0][1]["name"] == "point right"
    assert plays[0][1]["hint"] == GESTURE_HINTS["point right"]
    assert plays[0][1]["accepted_is_not_completed"] is True
    assert [item[0] for item in screen.sent].count("gesture.neutral") == 1
    screen.session.accept(_point(3), _snap(revision=3))
    screen.cancel_pending(MATCH)
    assert any(item[0] == "gesture.cancel_pending" for item in screen.sent)
    assert [item[0] for item in screen.sent].count("gesture.play") == 1
