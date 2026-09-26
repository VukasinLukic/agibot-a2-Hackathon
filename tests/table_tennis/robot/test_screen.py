"""Phase 2: the scoreboard keeps the latest score until release. No robot."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from table_tennis.contracts import (
    CourtEndByPlayer,
    GameRules,
    MatchSnapshot,
    Player,
    Readiness,
    RobotSideByPlayer,
    ScoreByPlayer,
)
from table_tennis.core.fake_log import FakeOutputLog
from table_tennis.robot.a2_adapters import A2ScoreDisplay
from table_tennis.robot.fake import FakeScoreDisplay
from table_tennis.robot.score_display import ScreenSlotBusy, ScoreboardSession

MATCH_A = "00000000-0000-4000-8000-0000000000a1"
MATCH_B = "00000000-0000-4000-8000-0000000000b2"


def _snap(match_id: str, revision: int, p1: int, p2: int, status: str = "between_rallies", name: str = "Ana") -> MatchSnapshot:
    return MatchSnapshot(
        match_id=match_id,
        revision=revision,
        status=status,  # type: ignore[arg-type]
        players=[Player(id="p1", display_name=name), Player(id="p2", display_name="Marko")],
        config=GameRules(),
        score_by_player=ScoreByPlayer(p1=p1, p2=p2),
        first_server_id="p1",
        server_id=None if status == "finished" else "p1",
        winner_id="p1" if status == "finished" else None,
        assignment_version=1,
        court_end_by_player=CourtEndByPlayer(p1="end_a", p2="end_b"),
        robot_side_by_player=RobotSideByPlayer(p1="left", p2="right"),
        calibration_id=None,
        active_rally_id=None,
        active_proposal_id=None,
        persona="regular",
        scoring_mode="assisted",
        ready=Readiness(),
        updated_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )


def test_fast_revisions_play_only_the_latest() -> None:
    played: list[int] = []
    session = ScoreboardSession(lambda frame: played.append(frame.revision))
    try:
        assert session.provision_count == 1
        first = session.accept(_snap(MATCH_A, 2, 1, 0))
        second = session.accept(_snap(MATCH_A, 3, 2, 0))
        assert first.accepted and not first.shown
        assert second.accepted and not second.shown
        shown = session.pump()
        assert played == [3]
        assert shown is not None and shown.shown and shown.revision == 3
        assert session.shown_revision(MATCH_A) == 3
        assert session.face == "scoreboard"
        assert session.restores == 0
        assert session.provision_count == 1
    finally:
        session.release()


def test_duplicate_revision_does_not_play_again() -> None:
    played: list[int] = []
    session = ScoreboardSession(lambda frame: played.append(frame.revision))
    try:
        session.render(_snap(MATCH_A, 4, 1, 0))
        again = session.render(_snap(MATCH_A, 4, 1, 0))
        assert played == [4]
        assert again.reason == "duplicate" and not again.shown
    finally:
        session.release()


def test_undo_drops_pending_frames_and_shows_the_corrected_score() -> None:
    played: list[int] = []
    session = ScoreboardSession(lambda frame: played.append(frame.revision))
    try:
        session.accept(_snap(MATCH_A, 5, 1, 0))
        session.accept(_snap(MATCH_A, 6, 2, 0))
        assert session.drop_pending(MATCH_A) == 1
        session.accept(_snap(MATCH_A, 7, 1, 0))
        session.pump()
        assert played == [7]
        assert session.face == "scoreboard"
    finally:
        session.release()


def test_new_match_resets_the_watermark() -> None:
    played: list[str] = []
    session = ScoreboardSession(lambda frame: played.append(frame.match_id))
    try:
        session.render(_snap(MATCH_A, 8, 4, 2))
        opened = session.render(_snap(MATCH_B, 1, 0, 0, status="setup"))
        assert opened.shown
        assert played == [MATCH_A, MATCH_B]
        assert session.shown_revision(MATCH_B) == 1
    finally:
        session.release()


def test_render_failure_keeps_the_score_and_does_not_restore_the_face() -> None:
    def play(frame) -> None:
        if frame.revision == 3:
            raise RuntimeError("playback broke")

    session = ScoreboardSession(play)
    try:
        session.render(_snap(MATCH_A, 2, 1, 0))
        with pytest.raises(RuntimeError, match="playback broke"):
            session.render(_snap(MATCH_A, 3, 2, 0))
        assert session.shown_revision(MATCH_A) == 2
        assert session.face == "scoreboard"
        assert session.restores == 0
        assert session.acks[-1].reason == "render_failed"
        assert session.acks[-1].accepted and not session.acks[-1].shown
    finally:
        session.release()


def test_default_face_returns_only_on_release() -> None:
    session = ScoreboardSession(lambda frame: None)
    try:
        session.render(_snap(MATCH_A, 1, 0, 0, status="setup"))
        assert session.face == "scoreboard" and session.restores == 0
        assert session.release() is True
        assert session.face == "default" and session.restores == 1
        assert session.release() is False
        assert session.restores == 1
    finally:
        if session.leased:
            session.release()


def test_second_worker_cannot_take_the_same_slot() -> None:
    holder = ScoreboardSession(lambda frame: None)
    try:
        with pytest.raises(ScreenSlotBusy):
            ScoreboardSession(lambda frame: None)
    finally:
        holder.release()


def test_fake_screen_transliterates_and_names_the_server() -> None:
    log = FakeOutputLog()
    screen = FakeScoreDisplay(log)
    try:
        screen.render(_snap(MATCH_A, 1, 1, 0, name="Čeda"))
        assert screen.renders == 1
        text = log.display.text if log.display else ""
        assert "CEDA" in text and "Č" not in text
        assert "1 : 0" in text and "SERVIS" in text
        screen.close()
        assert screen.closed
        assert log.display is not None and log.display.text == "DEFAULT FACE"
    finally:
        if screen.session.leased:
            screen.session.release()


def test_fake_screens_do_not_compete_for_the_physical_slot() -> None:
    first = FakeScoreDisplay(FakeOutputLog())
    second = FakeScoreDisplay(FakeOutputLog())
    try:
        assert first.session.leased
        assert second.session.leased
    finally:
        first.close()
        second.close()


def test_dry_run_show_is_not_a_face_reset() -> None:
    screen = A2ScoreDisplay(dry_run=True)
    try:
        screen.session.accept(_snap(MATCH_A, 1, 1, 0))
        screen.session.accept(_snap(MATCH_A, 2, 2, 0))
        screen.session.pump()
        shows = [item for item in screen.sent if item[0] == "screen.show"]
        assert len(shows) == 1
        assert shows[0][1]["revision"] == 2
        assert shows[0][1]["slot"] == "emoticon_ct_message"
        assert not any(item[0] == "screen.release" for item in screen.sent)
        screen.close()
        assert [item[0] for item in screen.sent].count("screen.release") == 1
    finally:
        if screen.session.leased:
            screen.session.release()
