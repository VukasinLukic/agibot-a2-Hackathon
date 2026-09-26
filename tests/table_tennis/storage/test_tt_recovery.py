"""Phase 4 recovery and output-outbox rules (contract section 10)."""

from __future__ import annotations

from pathlib import Path

from table_tennis.api.runtime import build_runtime
from table_tennis.config import load_settings
from table_tennis.core.ports import FixedClock, SequentialIdGenerator
from table_tennis.sim.driver import InProcessDriver

SIDES = {
    "court_end_by_player": {"p1": "end_b", "p2": "end_a"},
    "robot_side_by_player": {"p1": "right", "p2": "left"},
}


def _settings(tmp_path: Path):
    return load_settings(env={}, storage__db_path=str(tmp_path / "tt.sqlite"), outputs__fake_log_path="")


def _runtime(tmp_path: Path, *, drain: bool = True):
    ids = SequentialIdGenerator()
    rt = build_runtime(_settings(tmp_path), clock=FixedClock(), ids=ids, background=False)
    rt.start()
    if not drain:
        # commits only wake the (never started) worker: outbox rows stay pending
        # (runtime is already started, so no worker/ticker thread is launched;
        # rt.background also stops InProcessDriver from draining after each command)
        rt.dispatcher.background = True
        rt.background = True
    return rt, InProcessDriver(rt, ids=ids)


def _restart(tmp_path: Path):
    rt = build_runtime(_settings(tmp_path), background=False)
    return rt


def _setup(d: InProcessDriver) -> None:
    assert d.create(scoring_mode="manual", persona="regular").status == 201
    d.ok("robot.ready.set", {"ready": True, "reason": "manual_arrival"}, expected_revision=None)
    d.ok("match.start")


def _rows(rt, kind=None, event_id=None):
    out = []
    for r in rt.store.outbox_rows(None):
        if kind and r["kind"] != kind:
            continue
        if event_id and r["event_id"] != event_id:
            continue
        out.append(r)
    return out


def test_restart_restores_exact_state(tmp_path):
    rt, d = _runtime(tmp_path)
    try:
        _setup(d)
        d.point("p1")
        d.point("p2")
        last = d.point("p1")["snapshot"]["last_point_event_id"]
        d.ok("point.undo", {"target_event_id": last, "reason": "wrong button"})
        d.ok("match.pause", {"reason": "switch ends"})
        d.ok("sides.set", SIDES)
        d.ok("match.resume")
        d.point("p2")
        mid = d.match_id
        snap = rt.service.get_snapshot(mid)
        state = rt.service.get_state(mid)
    finally:
        rt.stop()

    rt2 = _restart(tmp_path)
    try:
        rt2.start()
        assert rt2.service.get_snapshot(mid) == snap
        assert rt2.service.get_state(mid) == state
        assert rt2.service.rebuild_from_events(mid) == state
        assert snap.score_by_player.p1 == 1 and snap.score_by_player.p2 == 2
        assert snap.assignment_version == 2
    finally:
        rt2.stop()


def test_no_replay_of_speech_or_gesture_on_restart(tmp_path):
    rt, d = _runtime(tmp_path, drain=False)
    try:
        _setup(d)
        d.point("p1")
        d.point("p1")
        mid = d.match_id
        snap = rt.service.get_snapshot(mid)
        assert rt.speech.spoken == [] and rt.gesture.performed == []
    finally:
        rt.stop()

    rt2 = _restart(tmp_path)
    try:
        rt2.start()
        rt2.dispatcher.drain()
        assert rt2.speech.spoken == []
        assert rt2.gesture.performed == []
        transient = [r for r in _rows(rt2) if r["kind"] in ("speech", "gesture")]
        assert transient and all(r["status"] == "skipped_restart" for r in transient)
        assert all(r["status"] == "superseded" for r in _rows(rt2, "display"))
        assert rt2.display.renders == 1
        assert rt2.display.latest == snap
    finally:
        rt2.stop()


def test_in_progress_rows_become_unknown_restart(tmp_path):
    rt, d = _runtime(tmp_path, drain=False)
    try:
        _setup(d)
        d.point("p1")
        gest = _rows(rt, "gesture")
        assert len(gest) == 1
        rt.store.outbox_mark(gest[0]["id"], "in_progress")
        row_id = gest[0]["id"]
    finally:
        rt.stop()

    rt2 = _restart(tmp_path)
    try:
        counts = rt2.dispatcher.startup()
        rt2.dispatcher.drain()
        assert counts["unknown"] == 1
        row = [r for r in _rows(rt2) if r["id"] == row_id][0]
        assert row["status"] == "unknown_restart"
        assert rt2.gesture.performed == []
    finally:
        rt2.store.close()


def test_undo_cancels_pending_outputs(tmp_path):
    rt, d = _runtime(tmp_path, drain=False)
    try:
        _setup(d)
        d.point("p1")
        rt.dispatcher.drain()
        spoken_before = len(rt.speech.spoken)
        target = d.point("p2")["snapshot"]["last_point_event_id"]
        d.ok("point.undo", {"target_event_id": target, "reason": "operator error"})
        rt.dispatcher.drain()
        for kind in ("speech", "gesture"):
            (row,) = _rows(rt, kind, target)
            assert row["status"] == "skipped_stale" and row["detail"] == "undone"
        assert all(g["event_id"] != target for g in rt.gesture.performed)
        new = rt.speech.spoken[spoken_before:]
        assert len(new) == 1
        corr = rt.store.get_event(new[0]["event_id"])
        assert corr.type == "score.corrected"
        assert rt.gesture.cancelled == [d.match_id]
        assert rt.display.latest.score_by_player.p1 == 1
        assert rt.display.latest.score_by_player.p2 == 0
        assert rt.display.latest == rt.service.get_snapshot(d.match_id)
    finally:
        rt.stop()


def test_stale_commentary_skipped_when_next_rally_started(tmp_path):
    rt, d = _runtime(tmp_path, drain=False)
    try:
        _setup(d)
        rt.dispatcher.drain()
        target = d.point("p1")["snapshot"]["last_point_event_id"]
        d.arm()
        rt.dispatcher.drain()
        (row,) = _rows(rt, "speech", target)
        assert row["status"] == "skipped_stale" and row["detail"] == "next_rally_started"
        assert all(s["event_id"] != target for s in rt.speech.spoken)
    finally:
        rt.stop()


def test_display_latest_revision_wins(tmp_path):
    rt, d = _runtime(tmp_path, drain=False)
    try:
        _setup(d)
        d.point("p1")
        d.point("p2")
        d.point("p1")
        renders = rt.display.renders
        rt.dispatcher.drain()
        assert rt.display.renders == renders + 1
        rev = rt.service.get_snapshot(d.match_id).revision
        assert rt.display.latest.revision == rev
        disp = _rows(rt, "display")
        assert disp[-1]["status"] == "done"
        assert all(r["status"] == "superseded" for r in disp[:-1])
    finally:
        rt.stop()


class _BrokenSpeech:
    def announce(self, event, snapshot):
        raise RuntimeError("speaker unplugged")

    def cancel_pending(self, match_id):
        pass


def test_adapter_failure_keeps_the_point(tmp_path):
    rt, d = _runtime(tmp_path)
    try:
        _setup(d)
        rt.dispatcher.speech = _BrokenSpeech()
        target = d.point("p1")["snapshot"]["last_point_event_id"]
        (row,) = _rows(rt, "speech", target)
        assert row["status"] == "failed" and "unplugged" in row["detail"]
        assert d.score() == (1, 0)
        assert rt.dispatcher.last_error and "speech" in rt.dispatcher.last_error
        assert rt.gesture.performed and rt.gesture.performed[-1]["event_id"] == target
    finally:
        rt.stop()


def test_undo_is_a_new_event(tmp_path):
    rt, d = _runtime(tmp_path)
    try:
        _setup(d)
        res = d.point("p1")["snapshot"]
        target, rev_before = res["last_point_event_id"], res["revision"]
        after = d.ok("point.undo", {"target_event_id": target, "reason": "x"})["snapshot"]
        assert after["revision"] > rev_before
        types = [e.type for e in rt.store.events_for_match(d.match_id)]
        assert "point.confirmed" in types and types[-1] == "score.corrected"
        assert rt.store.get_event(target).type == "point.confirmed"
        revs = [e.revision for e in rt.store.events_for_match(d.match_id)]
        assert revs == sorted(revs)
    finally:
        rt.stop()


def test_robot_call_interrupted_by_restart_fails(tmp_path):
    rt, d = _runtime(tmp_path)
    try:
        _setup(d)
        reply = d.robot_call()
        assert reply.status == 202
        call_id = reply.body["call_id"]
    finally:
        rt.stop()

    rt2 = _restart(tmp_path)
    try:
        rt2.start()
        call = rt2.robot.get(call_id)
        assert call.state == "failed"
        assert "outcome unknown" in call.reason
        assert rt2.navigator.native_calls == []
        rt2.robot.tick()
        assert rt2.robot.get(call_id).state == "failed"
        assert rt2.store.robot_calls_active() == []
    finally:
        rt2.stop()
