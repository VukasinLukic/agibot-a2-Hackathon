import uuid

import pytest
from pydantic import ValidationError

from table_tennis.contracts import parse_command
from table_tennis.sim.scenarios import CALIBRATION, _play_to, _proposal, _setup


CID_CMD_X = str(uuid.uuid4())
CID_RETRY_1 = str(uuid.uuid4())


def snap(d):
    return d.snapshot()


def at(d, p1, p2, mode="manual"):
    _setup(d, scoring_mode=mode)
    _play_to(d, p1, p2)
    assert d.score() == (p1, p2)


def test_10_9_p1_finishes_11_9(d):
    at(d, 10, 9)
    s = d.point("p1")["snapshot"]
    assert s["status"] == "finished" and s["winner_id"] == "p1"
    assert s["score_by_player"] == {"p1": 11, "p2": 9}


def test_10_10_p1_is_11_10_not_end(d):
    at(d, 10, 10)
    s = d.point("p1")["snapshot"]
    assert s["score_by_player"] == {"p1": 11, "p2": 10}
    assert s["status"] == "between_rallies" and s["winner_id"] is None
    assert s["server_id"] == "p2"


def test_11_10_p2_is_11_11_continue(d):
    at(d, 11, 10)
    s = d.point("p2")["snapshot"]
    assert s["score_by_player"] == {"p1": 11, "p2": 11}
    assert s["status"] == "between_rallies" and s["server_id"] == "p1"


def test_11_10_p1_finishes_12_10(d):
    at(d, 11, 10)
    s = d.point("p1")["snapshot"]
    assert s["status"] == "finished" and s["winner_id"] == "p1" and s["server_id"] is None


def test_same_command_id_twice_one_point(d):
    _setup(d, scoring_mode="manual")
    rally = d.arm()
    rev = snap(d)["revision"]
    body = {"rally_id": rally, "winner_id": "p1"}
    first = d.ok("point.award", body, command_id=CID_CMD_X, expected_revision=rev)
    again = d.cmd("point.award", body, command_id=CID_CMD_X, expected_revision=rev)
    assert again.ok and again.body["duplicate"] is True
    assert again.body["event_ids"] == first["event_ids"]
    assert d.score() == (1, 0)


def test_second_command_id_same_closed_rally_rejected(d):
    _setup(d, scoring_mode="manual")
    rally = d.arm()
    d.ok("point.award", {"rally_id": rally, "winner_id": "p1"})
    r = d.cmd("point.award", {"rally_id": rally, "winner_id": "p1"})
    # between_rallies after the point: rally no longer active
    assert r.status == 409 and r.code in ("stale_rally", "invalid_state")
    d.arm()
    r = d.cmd("point.award", {"rally_id": rally, "winner_id": "p1"})
    assert r.status == 409 and r.code == "stale_rally"
    assert d.score() == (1, 0)


def test_undo_final_point_reopens_and_rederives_server(d):
    at(d, 10, 9)
    fin = d.point("p1")["snapshot"]
    assert fin["status"] == "finished"
    s = d.ok("point.undo", {"target_event_id": fin["last_point_event_id"], "reason": "x"})["snapshot"]
    assert s["status"] == "between_rallies" and s["winner_id"] is None
    assert s["score_by_player"] == {"p1": 10, "p2": 9}
    assert s["server_id"] == "p2"  # total 19 -> (19//2)%2 == 1 -> p2


def test_let_keeps_score_server_needs_new_arm(d):
    _setup(d, scoring_mode="manual")
    d.point("p1")
    before = snap(d)
    rally = d.arm()
    s = d.ok("rally.let", {"rally_id": rally, "reason": "net"})["snapshot"]
    assert s["score_by_player"] == before["score_by_player"]
    assert s["server_id"] == before["server_id"]
    assert s["active_rally_id"] is None
    r = d.cmd("point.award", {"rally_id": rally, "winner_id": "p1"})
    assert r.status == 409
    assert d.arm() != rally


def test_sides_set_keeps_score_by_player_id(d):
    _setup(d, scoring_mode="manual")
    d.point("p1")
    d.point("p2")
    d.point("p1")
    d.ok("match.pause", {"reason": "switch"})
    s = d.ok("sides.set", {
        "court_end_by_player": {"p1": "end_b", "p2": "end_a"},
        "robot_side_by_player": {"p1": "right", "p2": "left"},
    })["snapshot"]
    assert s["score_by_player"] == {"p1": 2, "p2": 1}
    assert s["assignment_version"] == 2


def test_regular_and_corporate_identical_score(rt_ids, tmp_path):
    from table_tennis.sim.driver import InProcessDriver
    from table_tennis.sim.fixtures import make_runtime

    results = []
    for i, persona in enumerate(("regular", "corporate")):
        sub = tmp_path / str(i)
        sub.mkdir()
        runtime, ids = make_runtime(str(sub))
        try:
            d = InProcessDriver(runtime, ids=ids)
            _setup(d, scoring_mode="manual", persona=persona)
            for w in ("p1", "p2", "p2", "p1", "p1"):
                d.point(w)
            s = d.snapshot()
            results.append((s["score_by_player"], s["server_id"], s["status"], s["revision"]))
        finally:
            runtime.stop()
    assert results[0] == results[1]


def test_best_of_3_unsupported(d):
    r = d.create(best_of=3)
    assert r.status == 422 and r.code == "unsupported_best_of"


def test_automatic_scoring_rejected(d):
    r = d.create(scoring_mode="automatic")
    assert r.status == 422


def test_award_during_pause_rejected(d):
    _setup(d, scoring_mode="manual")
    rally = d.arm()
    d.ok("match.pause", {"reason": "towel"})
    # pause during a rally cancels it, so the old rally id is stale
    r = d.cmd("point.award", {"rally_id": rally, "winner_id": "p1"})
    assert r.status == 409 and r.code == "stale_rally"
    assert d.score() == (0, 0)


def test_award_during_pause_with_pending_proposal_rejected(d):
    _setup(d, scoring_mode="assisted", camera=True)
    rally = d.arm()
    d.ok("point.propose", _proposal(d), actor="sim")
    d.ok("match.pause", {"reason": "discussion"})
    # rally is still alive (pending kept across pause) but no decision while paused
    r = d.cmd("point.award", {"rally_id": rally, "winner_id": "p1"})
    assert r.status == 409 and r.code == "invalid_state"
    assert d.score() == (0, 0)


def test_award_after_finished_rejected(d):
    at(d, 10, 0)
    d.point("p1")
    r = d.cmd("rally.arm")
    assert r.status == 409 and r.code == "match_finished"
    r = d.cmd("point.award", {"rally_id": str(uuid.uuid4()), "winner_id": "p2"})
    assert r.status == 409 and r.code == "match_finished"
    assert d.score() == (11, 0)


def test_propose_does_not_change_score(d):
    _setup(d, scoring_mode="assisted", camera=True)
    d.arm()
    s = d.ok("point.propose", _proposal(d, winner="p2"), actor="sim")["snapshot"]
    assert s["score_by_player"] == {"p1": 0, "p2": 0}
    assert s["status"] == "pending_decision"


def _proposal_body(**over):
    payload = {
        "proposal_id": str(uuid.uuid4()), "rally_id": str(uuid.uuid4()), "winner_id": "p1", "confidence": 0.9,
        "reason": "missed_return", "calibration_id": CALIBRATION, "assignment_version": 1,
        "capture_start_seq": 1, "capture_end_seq": 2, "evidence_ref": None,
    }
    payload.update(over)
    return {"command_id": str(uuid.uuid4()), "expected_revision": 5, "type": "point.propose", "payload": payload}


def test_valid_proposal_parses():
    parse_command(_proposal_body())


@pytest.mark.parametrize("over", [{"confidence": 1.5}, {"winner_id": "p3"}])
def test_contract_rejects_bad_proposal(over):
    with pytest.raises(ValidationError):
        parse_command(_proposal_body(**over))


def test_award_winner_p3_rejected_by_contract():
    with pytest.raises(ValidationError):
        parse_command({"command_id": str(uuid.uuid4()), "expected_revision": 1, "type": "point.award",
                       "payload": {"rally_id": str(uuid.uuid4()), "winner_id": "p3"}})


def test_stale_revision_409_with_current(d):
    _setup(d, scoring_mode="manual")
    cur = snap(d)["revision"]
    r = d.cmd("rally.arm", expected_revision=cur - 1)
    assert r.status == 409 and r.code == "stale_revision"
    assert r.body["current_revision"] == cur


def test_idempotency_before_revision(d):
    _setup(d, scoring_mode="manual")
    rally = d.arm()
    rev = snap(d)["revision"]
    body = {"rally_id": rally, "winner_id": "p2"}
    d.ok("point.award", body, command_id=CID_RETRY_1, expected_revision=rev)
    d.point("p1")  # revision moves on
    assert snap(d)["revision"] > rev
    again = d.cmd("point.award", body, command_id=CID_RETRY_1, expected_revision=rev)
    assert again.ok and again.body["duplicate"] is True
    assert d.score() == (1, 1)


def test_undo_with_armed_rally_pauses_and_voids_rally(d):
    _setup(d, scoring_mode="manual")
    d.point("p1")
    last = snap(d)["last_point_event_id"]
    rally = d.arm()
    s = d.ok("point.undo", {"target_event_id": last, "reason": "x"})["snapshot"]
    assert s["status"] == "paused"
    assert s["score_by_player"] == {"p1": 0, "p2": 0}
    d.ok("match.resume")
    new = d.arm()
    assert new != rally
    r = d.cmd("point.award", {"rally_id": rally, "winner_id": "p1"})
    assert r.status == 409 and r.code == "stale_rally"


def test_undo_with_no_points_errors_and_no_negative(d):
    _setup(d, scoring_mode="manual")
    r = d.cmd("point.undo", {"target_event_id": str(uuid.uuid4()), "reason": "x"})
    assert r.status == 409 and r.code == "unknown_point"
    assert d.score() == (0, 0)


def test_double_undo_rejected(d):
    _setup(d, scoring_mode="manual")
    d.point("p2")
    last = snap(d)["last_point_event_id"]
    d.ok("point.undo", {"target_event_id": last, "reason": "x"})
    r = d.cmd("point.undo", {"target_event_id": last, "reason": "x"})
    assert r.status == 409 and r.code == "already_undone"
    assert d.score() == (0, 0)


def test_closed_rally_is_stale_rally_even_between_rallies(d):
    _setup(d, scoring_mode="manual")
    rally = d.arm()
    d.ok("point.award", {"rally_id": rally, "winner_id": "p1"})
    d.rejected("stale_rally", "point.award", {"rally_id": rally, "winner_id": "p1"})
    d.rejected("stale_rally", "rally.let", {"rally_id": rally})
    assert d.score() == (1, 0)


def test_persona_rejected_while_paused_with_pending_proposal(d):
    _setup(d, scoring_mode="assisted", camera=True)
    d.arm()
    d.ok("point.propose", _proposal(d), actor="sim")
    d.ok("match.pause", {"reason": "discussion"})
    assert d.snapshot()["paused_from"] == "pending_decision"
    d.rejected("invalid_state", "persona.set", {"persona": "corporate"})
    assert d.snapshot()["persona"] == "regular"


def test_undo_pause_event_records_real_previous_status(d, rt):
    _setup(d, scoring_mode="manual")
    d.point("p1")
    last = d.snapshot()["last_point_event_id"]
    d.arm()
    res = d.ok("point.undo", {"target_event_id": last})
    events = rt.store.events_for_match(d.match_id)
    paused = [e for e in events if e.event_id in res["event_ids"] and e.type == "match.paused"]
    assert len(paused) == 1
    assert paused[0].payload.previous_status == "rally"
    assert paused[0].payload.resume_to == "between_rallies"
