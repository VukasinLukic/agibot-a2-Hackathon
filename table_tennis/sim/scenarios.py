"""Scenarios built only from legal commands. Used by fixtures, tests and the CLI.

Each scenario takes a Driver and asserts on backend answers. The fixture names
from contract section 12 map 1:1 to functions here; ``manual-game`` and
``disputed-point`` are the longer CLI demos.
"""

from __future__ import annotations

from typing import Callable

from .driver import Driver

CALIBRATION = "table-1-camera-a-v1"


def _setup(d: Driver, *, scoring_mode: str = "assisted", persona: str = "regular", calibration: bool = True,
           camera: bool = False, robot: str = "manual") -> None:
    kwargs = {"scoring_mode": scoring_mode, "persona": persona}
    if calibration:
        kwargs["calibration_id"] = CALIBRATION
    r = d.create(**kwargs)
    d.expect(r.status == 201, "match created (201)")
    if robot == "manual":
        d.ok("robot.ready.set", {"ready": True, "reason": "manual_arrival"}, expected_revision=None)
    elif robot == "call":
        call = d.robot_call()
        d.expect(call.status == 202, "robot call accepted (202, not 'arrived')")
        d.wait_robot(call.body["call_id"], {"ready"})
        d.expect(d.snapshot()["ready"]["robot_ready"], "robot_ready set by robot adapter on arrival")
    if camera:
        d.ok("camera.ready.set", {"ready": True, "reason": "sim camera"}, actor="sim", expected_revision=None)
    d.ok("match.start")


def _play_to(d: Driver, p1: int, p2: int) -> None:
    """Alternate points so the score passes through realistic states."""
    a, b = d.score()
    while (a, b) != (p1, p2):
        if a < p1 and (a <= b or b >= p2):
            d.point("p1")
        else:
            d.point("p2")
        a, b = d.score()


def _proposal(d: Driver, winner: str = "p1", **over) -> dict:
    snap = d.snapshot()
    body = {
        "proposal_id": d.new_id(),
        "rally_id": snap["active_rally_id"],
        "winner_id": winner,
        "confidence": 0.91,
        "reason": "missed_return",
        "calibration_id": snap["calibration_id"],
        "assignment_version": snap["assignment_version"],
        "capture_start_seq": 420,
        "capture_end_seq": 480,
        "evidence_ref": None,
    }
    body.update(over)
    return body


# --------------------------------------------------------------------------- fixtures (section 12)


def new_match(d: Driver) -> None:
    r = d.create(scoring_mode="assisted", calibration_id=CALIBRATION)
    s = r.body
    d.expect(s["status"] == "setup" and s["revision"] == 1, "new match is in setup at revision 1")
    d.expect(s["score_by_player"] == {"p1": 0, "p2": 0} and s["server_id"] == "p1", "0:0, p1 serves")


def manual_point(d: Driver) -> None:
    _setup(d, scoring_mode="manual")
    res = d.point("p1")
    d.expect(res["snapshot"]["score_by_player"] == {"p1": 1, "p2": 0}, "manual award -> 1:0")


def duplicate_command(d: Driver) -> None:
    _setup(d, scoring_mode="manual")
    rally = d.arm()
    rev = d.snapshot()["revision"]
    cid = d.new_id()
    first = d.ok("point.award", {"rally_id": rally, "winner_id": "p1"}, command_id=cid, expected_revision=rev)
    again = d.cmd("point.award", {"rally_id": rally, "winner_id": "p1"}, command_id=cid, expected_revision=rev)
    d.expect(again.ok and again.body["duplicate"] is True, "same command_id + same payload -> stored answer")
    d.expect(again.body["event_ids"] == first["event_ids"], "duplicate returns the original event ids")
    d.expect(d.score() == (1, 0), "no second point")
    other = d.cmd("point.award", {"rally_id": rally, "winner_id": "p2"}, command_id=cid, expected_revision=rev)
    d.expect(other.status == 409 and other.code == "command_id_conflict", "same command_id + other payload -> 409")
    d.rejected("stale_rally", "point.award", {"rally_id": rally, "winner_id": "p1"})


def left_right_swap(d: Driver) -> None:
    _setup(d, scoring_mode="manual")
    d.point("p1")
    d.point("p1")
    d.rejected("invalid_state", "sides.set", {
        "court_end_by_player": {"p1": "end_b", "p2": "end_a"},
        "robot_side_by_player": {"p1": "right", "p2": "left"},
    })
    d.ok("match.pause", {"reason": "players switch ends"})
    res = d.ok("sides.set", {
        "court_end_by_player": {"p1": "end_b", "p2": "end_a"},
        "robot_side_by_player": {"p1": "right", "p2": "left"},
    })
    s = res["snapshot"]
    d.expect(s["assignment_version"] == 2, "assignment_version incremented")
    d.expect(s["score_by_player"] == {"p1": 2, "p2": 0}, "score stays with player ids, not sides")
    d.ok("match.resume")
    d.point("p1")
    d.expect(d.score() == (3, 0), "p1 keeps scoring after swap")


def stale_proposal(d: Driver) -> None:
    _setup(d, camera=True)
    rally = d.arm()
    old = _proposal(d)
    d.ok("match.pause", {"reason": "camera bumped"})
    d.ok("calibration.set", {"calibration_id": "table-1-camera-a-v2"})
    d.ok("match.resume")
    d.rejected("invalid_state", "point.propose", old, actor="sim")
    d.arm()
    stale_cal = _proposal(d, calibration_id=CALIBRATION)
    d.rejected("stale_calibration", "point.propose", stale_cal, actor="sim")
    stale_asg = _proposal(d, assignment_version=2)
    d.rejected("stale_assignment", "point.propose", stale_asg, actor="sim")
    old_rally = _proposal(d, rally_id=rally)
    d.rejected("stale_rally", "point.propose", old_rally, actor="sim")
    d.rejected("stale_revision", "point.propose", _proposal(d), actor="sim", expected_revision=1)
    d.expect(d.score() == (0, 0), "no stale proposal changed the score")


def pending_proposal(d: Driver) -> None:
    _setup(d, camera=True)
    d.arm()
    prop = _proposal(d, winner="p2")
    res = d.ok("point.propose", prop, actor="sim")
    s = res["snapshot"]
    d.expect(s["status"] == "pending_decision" and s["active_proposal_id"] == prop["proposal_id"], "proposal pending")
    d.expect(s["score_by_player"] == {"p1": 0, "p2": 0}, "proposal does not change the score")
    second = _proposal(d, winner="p1")
    d.rejected("proposal_already_pending", "point.propose", second, actor="sim")
    d.rejected("forbidden_actor", "point.confirm", {"proposal_id": prop["proposal_id"]}, actor="sim", status=403)


def let(d: Driver) -> None:
    _setup(d, scoring_mode="manual")
    d.point("p1")
    before = d.snapshot()
    rally = d.arm()
    res = d.ok("rally.let", {"rally_id": rally, "reason": "net on serve"})
    s = res["snapshot"]
    d.expect(s["score_by_player"] == before["score_by_player"], "let keeps the score")
    d.expect(s["server_id"] == before["server_id"], "let keeps the server")
    d.expect(s["active_rally_id"] is None, "let closes the rally")
    d.rejected("stale_rally", "point.award", {"rally_id": rally, "winner_id": "p1"})
    new_rally = d.arm()
    d.expect(new_rally != rally, "next rally gets a new id")


def deuce_10_10(d: Driver) -> None:
    _setup(d, scoring_mode="manual")
    servers = [d.snapshot()["server_id"]]
    _play_to(d, 10, 10)
    s = d.snapshot()
    d.expect(s["score_by_player"] == {"p1": 10, "p2": 10}, "10:10 reached")
    d.expect(s["status"] == "between_rallies" and s["winner_id"] is None, "10:10 is not finished")
    d.expect(s["server_id"] == "p1", "10:10 -> first server serves")
    d.expect(servers[0] == "p1", "0:0 -> p1 serves")


def finish_12_10(d: Driver) -> None:
    deuce_10_10(d)
    d.point("p1")
    s = d.snapshot()
    d.expect(s["score_by_player"] == {"p1": 11, "p2": 10} and s["status"] != "finished", "11:10 is not the end")
    d.expect(s["server_id"] == "p2", "11:10 -> p2 serves")
    res = d.point("p1")
    s = res["snapshot"]
    d.expect(s["status"] == "finished" and s["winner_id"] == "p1", "12:10 finishes the game")
    d.expect(s["server_id"] is None, "no server after the game")
    d.rejected("match_finished", "rally.arm")


def undo_finish(d: Driver) -> None:
    _setup(d, scoring_mode="manual")
    _play_to(d, 10, 5)
    fin = d.point("p1")["snapshot"]
    d.expect(fin["status"] == "finished" and fin["score_by_player"] == {"p1": 11, "p2": 5}, "11:5 finished")
    target = fin["last_point_event_id"]
    res = d.ok("point.undo", {"target_event_id": target, "reason": "operator error"})
    s = res["snapshot"]
    d.expect(s["status"] == "between_rallies" and s["winner_id"] is None, "undo of the final point reopens the game")
    d.expect(s["score_by_player"] == {"p1": 10, "p2": 5}, "score back to 10:5")
    d.expect(s["server_id"] == "p2", "server re-derived (10:5 -> p2)")
    d.rejected("already_undone", "point.undo", {"target_event_id": target})
    d.point("p2")
    d.expect(d.score() == (10, 6), "play continues with a new rally")


def reconnect(d: Driver) -> None:
    _setup(d, scoring_mode="manual")
    d.point("p1")
    d.point("p2")
    d.point("p2")


def robot_busy(d: Driver) -> None:
    d.create(scoring_mode="manual")
    first = d.robot_call()
    d.expect(first.status == 202 and first.body["state"] == "requested", "first call accepted, not yet arrived")
    dup = d.robot_call(command_id=d.steps[-1]["request"]["command_id"])
    d.expect(dup.status == 202 and dup.body["call_id"] == first.body["call_id"], "same command_id -> same call")
    busy = d.robot_call()
    d.expect(busy.status == 409 and busy.code == "robot_busy", "second call while moving -> robot_busy")
    d.wait_robot(first.body["call_id"], {"ready"})
    d.expect(d.snapshot()["ready"]["robot_ready"], "arrival marks robot_ready on the match")


def camera_missing(d: Driver) -> None:
    _setup(d, camera=False)
    d.arm()
    d.rejected("camera_not_ready", "point.propose", _proposal(d), actor="sim")
    rally = d.snapshot()["active_rally_id"]
    d.ok("point.award", {"rally_id": rally, "winner_id": "p2"})
    d.expect(d.score() == (0, 1), "manual award still works without a camera")


def persona_change(d: Driver) -> None:
    _setup(d, scoring_mode="manual")
    d.point("p1")
    before = d.snapshot()
    res = d.ok("persona.set", {"persona": "corporate"})
    s = res["snapshot"]
    d.expect(s["persona"] == "corporate", "persona changed between rallies")
    d.expect(s["score_by_player"] == before["score_by_player"] and s["server_id"] == before["server_id"],
             "persona change does not touch score or server")
    d.arm()
    d.rejected("invalid_state", "persona.set", {"persona": "regular"})


# --------------------------------------------------------------------------- CLI demos


def manual_game(d: Driver) -> None:
    """Call fake robot -> start -> points (with a retried command and an undo) -> 11:x."""
    _setup(d, scoring_mode="manual", robot="call")
    d.point("p1")
    rally = d.arm()
    rev = d.snapshot()["revision"]
    cid = d.new_id()
    d.ok("point.award", {"rally_id": rally, "winner_id": "p2"}, command_id=cid, expected_revision=rev)
    retry = d.cmd("point.award", {"rally_id": rally, "winner_id": "p2"}, command_id=cid, expected_revision=rev)
    d.expect(retry.body.get("duplicate") is True and d.score() == (1, 1), "network retry did not add a point")
    last = d.snapshot()["last_point_event_id"]
    d.ok("point.undo", {"target_event_id": last, "reason": "wrong button"})
    d.expect(d.score() == (1, 0), "undo removed the last point")
    _play_to(d, 10, 10)
    d.point("p2")
    d.point("p1")
    d.point("p1")
    d.point("p1")
    s = d.snapshot()
    d.expect(s["status"] == "finished" and s["score_by_player"] == {"p1": 13, "p2": 11}, "13:11, game over")
    d.settle()
    out = d.outputs()
    if out and out.get("display"):
        d.expect("13 : 11" in out["display"]["text"], "fake screen shows the same final score")


def disputed_point(d: Driver) -> None:
    """Assisted mode: CV proposal -> operator confirm; commentary uses the exact score."""
    _setup(d, scoring_mode="assisted", persona="corporate", camera=True)
    d.point("p1")
    d.arm()
    prop = _proposal(d, winner="p2", confidence=0.74, reason="double_bounce")
    res = d.ok("point.propose", prop, actor="sim")
    d.expect(res["snapshot"]["score_by_player"] == {"p1": 1, "p2": 0}, "proposal alone changes nothing")
    d.rejected("forbidden_actor", "point.award",
               {"rally_id": prop["rally_id"], "winner_id": "p2"}, actor="sim", status=403)
    conf = d.ok("point.confirm", {"proposal_id": prop["proposal_id"]})
    d.expect(conf["snapshot"]["score_by_player"] == {"p1": 1, "p2": 1}, "operator confirmation -> 1:1")
    d.settle()
    out = d.outputs()
    if out:
        lines = [x["text"] for x in out.get("speech", [])]
        d.expect(any("Jedan prema jedan" in t for t in lines), "commentary states the confirmed score")


SCENARIOS: dict[str, Callable[[Driver], None]] = {
    "new_match": new_match,
    "manual_point": manual_point,
    "duplicate_command": duplicate_command,
    "left_right_swap": left_right_swap,
    "stale_proposal": stale_proposal,
    "pending_proposal": pending_proposal,
    "let": let,
    "deuce_10_10": deuce_10_10,
    "finish_12_10": finish_12_10,
    "undo_finish": undo_finish,
    "reconnect": reconnect,
    "robot_busy": robot_busy,
    "camera_missing": camera_missing,
    "persona_change": persona_change,
    "manual-game": manual_game,
    "disputed-point": disputed_point,
}

FIXTURE_SCENARIOS = [name for name in SCENARIOS if "-" not in name]
