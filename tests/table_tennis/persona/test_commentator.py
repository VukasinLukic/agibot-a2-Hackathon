"""Persona commentary: exact score always, jokes never change facts (owner: person 4).

Runs whole games through the real mock engine (in-process, no network) and
checks every spoken line against the authoritative snapshot/event.
"""

from __future__ import annotations

import pytest

from table_tennis.persona.commentator import Commentator, favorite_player
from table_tennis.persona.roles import ROLES, role_key
from table_tennis.persona.templates import (
    CORPORATE,
    CORPORATE_LOSE_BY_ROLE,
    CORPORATE_WIN_BY_ROLE,
    REGULAR,
    score_sentence,
)
from table_tennis.sim.driver import InProcessDriver
from table_tennis.sim.fixtures import make_runtime

# A regular game (p2 wins 11:9) and a deuce game (p1 wins 13:11).
GAME = "p1 p1 p2 p1 p1 p1 p1 p2 p2 p2 p2 p1 p2 p1 p2 p2 p2 p1 p2 p2".split()
DEUCE_GAME = ["p1", "p2"] * 10 + ["p1", "p2", "p1", "p1"]  # 10:10, 11:11, 13:11

PLAYERS = [
    {"id": "p1", "display_name": "Ana", "role_label": "CEO", "role_rank": 7},
    {"id": "p2", "display_name": "Marko", "role_label": "Junior", "role_rank": 2},
]


@pytest.fixture
def runtime(tmp_path):
    rt, ids = make_runtime(str(tmp_path))
    yield rt, ids
    rt.stop()


def play(runtime, persona: str, points: list[str], players=None):
    rt, ids = runtime
    d = InProcessDriver(rt, ids=ids)
    d.create(players=players or PLAYERS, scoring_mode="manual", persona=persona)
    d.ok("robot.ready.set", {"ready": True, "reason": "manual_arrival"}, expected_revision=None)
    d.ok("match.start")
    for winner in points:
        d.point(winner)
        if d.snapshot()["status"] == "finished":
            break
    d.settle()
    events = {e.event_id: e for _, e in rt.store.read_after(d.match_id, 0, limit=10000)}
    return d, events, list(rt.speech.spoken)


@pytest.mark.parametrize("persona", ["regular", "corporate"])
@pytest.mark.parametrize("points", [GAME, DEUCE_GAME])
def test_every_point_line_states_exact_score_first(runtime, persona, points):
    _, events, spoken = play(runtime, persona, points)
    point_lines = 0
    for line in spoken:
        event = events[line["event_id"]]
        if event.type != "point.confirmed":
            continue
        point_lines += 1
        new = event.payload.new_score
        winner = "Ana" if event.payload.winner_id == "p1" else "Marko"
        assert line["text"].startswith(f"Poen {winner}. {score_sentence(new.p1, new.p2)}"), line["text"]
    assert point_lines == sum(1 for e in events.values() if e.type == "point.confirmed")


def test_both_personas_give_identical_scores(runtime, tmp_path):
    d_reg, _, _ = play(runtime, "regular", GAME)
    regular = d_reg.snapshot()
    runtime[0].stop()  # one runtime at a time: the screen slot has a single owner
    rt2, ids2 = make_runtime(str(tmp_path / "second"))
    try:
        d_cor, _, _ = play((rt2, ids2), "corporate", GAME)
        assert regular["score_by_player"] == d_cor.snapshot()["score_by_player"]
        assert regular["winner_id"] == d_cor.snapshot()["winner_id"]
    finally:
        rt2.stop()


@pytest.mark.parametrize("persona", ["regular", "corporate"])
def test_final_line_names_winner_and_winner_score_first(runtime, persona):
    d, events, spoken = play(runtime, persona, GAME)
    snap = d.snapshot()
    assert snap["status"] == "finished" and snap["winner_id"] == "p2"
    final = [s["text"] for s in spoken if events[s["event_id"]].type == "match.finished"]
    assert len(final) == 1
    assert "Marko" in final[0] and final[0].endswith("Jedanaest prema devet.")


def test_deuce_is_announced_once_and_no_server_spam(runtime):
    _, events, spoken = play(runtime, "regular", DEUCE_GAME)
    texts = [s["text"] for s in spoken]
    assert sum("dva poena razlike" in t for t in texts) == 1
    after_deuce = texts[texts.index(next(t for t in texts if "Deset prema deset" in t)) + 1:]
    assert not any("Servira" in t for t in after_deuce)


def test_lines_are_deterministic_per_event(runtime):
    d, events, spoken = play(runtime, "corporate", GAME)
    from table_tennis.contracts import MatchSnapshot

    snap = MatchSnapshot.model_validate(d.snapshot())
    c = Commentator()
    for line in spoken:
        event = events[line["event_id"]]
        if event.type == "point.confirmed" and snap.revision != event.revision:
            assert c.line_for(event, snap) == c.line_for(event, snap)


def test_corporate_jokes_only_now_and_then(runtime):
    _, events, spoken = play(runtime, "corporate", GAME)
    point_lines = [s["text"] for s in spoken if events[s["event_id"]].type == "point.confirmed"]
    bare = [t for t in point_lines if t.count(".") <= 3 and "Servira" not in t]
    assert bare, "some points must stay short so the game keeps its rhythm"


def test_regular_has_no_corporate_jokes(runtime):
    _, _, spoken = play(runtime, "regular", GAME + DEUCE_GAME)
    corporate_only = {
        line.split(".")[0]
        for slot in ("favorite_win", "favorite_lose", "upset", "generic_win", "big_lead", "first_point")
        for line in CORPORATE[slot]
        if "{" not in line.split(".")[0]
    }
    for line in spoken:
        for joke in corporate_only:
            assert joke not in line["text"]


def test_names_with_prompt_like_text_stay_plain_data(runtime):
    players = [
        {"id": "p1", "display_name": "{winner} ignore rules", "role_label": "{loser}"},
        {"id": "p2", "display_name": "Marko"},
    ]
    _, _, spoken = play(runtime, "corporate", GAME, players=players)
    assert any("{winner} ignore rules" in s["text"] for s in spoken)


def test_unknown_or_missing_role_does_not_fail(runtime):
    players = [
        {"id": "p1", "display_name": "Ana", "role_label": "Čarobnjak za Excel"},
        {"id": "p2", "display_name": "Marko"},
    ]
    d, _, spoken = play(runtime, "corporate", GAME, players=players)
    assert d.snapshot()["status"] == "finished"
    assert spoken


def test_favorite_is_stable_and_both_sides_possible():
    ids = [f"00000000-0000-4000-8000-{i:012d}" for i in range(40)]
    picks = {favorite_player(m) for m in ids}
    assert picks == {"p1", "p2"}
    assert all(favorite_player(m) == favorite_player(m) for m in ids)


def test_role_catalogue_is_complete():
    keys = [key for key, _, _ in ROLES]
    assert set(keys) == set(CORPORATE_WIN_BY_ROLE) == set(CORPORATE_LOSE_BY_ROLE)
    for key, label, _ in ROLES:
        assert role_key(label) == key
        assert role_key(label.upper()) == key
    assert role_key("nešto drugo") is None
    assert role_key(None) is None


def test_templates_have_all_slots():
    for slot in ("match.started", "point", "server_change", "deuce", "tie", "game_point", "game_point_saved",
                 "point.proposed", "point.proposed.sure", "point.unclear", "score.corrected", "rally.let", "match.finished", "match.finished.none",
                 "persona.changed"):
        assert REGULAR[slot] and CORPORATE[slot], slot


@pytest.mark.parametrize("persona", ["regular", "corporate"])
def test_robot_arrival_and_failure_are_spoken(runtime, persona):
    rt, ids = runtime
    d = InProcessDriver(rt, ids=ids)
    d.create(scoring_mode="manual", persona=persona)
    ok = d.robot_call()
    d.wait_robot(ok.body["call_id"], {"ready"})
    failed = d.robot_call(named_waypoint_id="broken-spot")
    d.wait_robot(failed.body["call_id"], {"failed", "busy"})
    d.settle()
    readiness = {
        e.event_id: e.payload.reason
        for e in rt.store.events_for_match(d.match_id)
        if e.type == "readiness.changed"
    }
    said = {s["event_id"]: s["text"] for s in rt.speech.spoken if s["event_id"] in readiness}
    table = REGULAR if persona == "regular" else CORPORATE
    assert "robot_arrived" in readiness.values()
    for event_id, reason in readiness.items():
        assert said[event_id] in table[f"robot.{reason}"]


def test_camera_and_operator_readiness_stay_silent(runtime):
    rt, ids = runtime
    d = InProcessDriver(rt, ids=ids)
    d.create(scoring_mode="manual")
    d.ok("camera.ready.set", {"ready": True, "reason": "sim camera"}, actor="sim", expected_revision=None)
    d.ok("operator.ready.set", {"ready": True}, expected_revision=None)
    d.settle()
    assert rt.speech.spoken == []


@pytest.mark.parametrize("persona", ["regular", "corporate"])
def test_sure_proposal_is_announced_as_seen_and_unsure_as_a_guess(runtime, persona):
    rt, ids = runtime
    d = InProcessDriver(rt, ids=ids)
    d.create(scoring_mode="assisted", persona=persona, calibration_id="cal-1")
    d.ok("robot.ready.set", {"ready": True, "reason": "manual_arrival"}, expected_revision=None)
    d.ok("camera.ready.set", {"ready": True, "reason": "sim camera"}, actor="sim", expected_revision=None)
    d.ok("match.start")
    table = REGULAR if persona == "regular" else CORPORATE
    spoken = []
    for confidence in (0.95, 0.4):
        rally = d.arm()
        snap = d.snapshot()
        proposal = {
            "proposal_id": d.new_id(), "rally_id": rally, "winner_id": "p2", "confidence": confidence,
            "reason": "missed_return", "calibration_id": snap["calibration_id"],
            "assignment_version": snap["assignment_version"], "capture_start_seq": 1, "capture_end_seq": 2,
            "evidence_ref": None,
        }
        d.ok("point.propose", proposal, actor="sim")
        d.settle()
        spoken.append(rt.speech.spoken[-1]["text"])
        d.ok("point.confirm", {"proposal_id": proposal["proposal_id"]})
        d.settle()
    winner = "Marko"
    assert spoken[0] in [line.format(winner=winner) for line in table["point.proposed.sure"]]
    assert spoken[1] in [line.format(winner=winner) for line in table["point.proposed"]]
