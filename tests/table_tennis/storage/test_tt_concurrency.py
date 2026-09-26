"""Faza 2: race conditions, idempotency and DB-level second-writer protection."""

from __future__ import annotations

import sqlite3
import threading
import uuid

import pytest

from table_tennis.contracts.commands import parse_command
from table_tennis.core.errors import ConflictError, ForbiddenError, RefereeError
from table_tennis.core.service import RefereeService
from table_tennis.sim.scenarios import _proposal, _setup
from table_tennis.storage.sqlite_store import SqliteEventStore

N = 8


def _cmd(type_, payload=None, *, rev, command_id=None):
    return parse_command(
        {"command_id": command_id or str(uuid.uuid4()), "expected_revision": rev, "type": type_, "payload": payload or {}}
    )


def _race(fns):
    """Run callables concurrently behind a barrier; return (results, errors)."""
    barrier = threading.Barrier(len(fns))
    results, errors = [], []
    guard = threading.Lock()

    def run(fn):
        barrier.wait()
        try:
            r = fn()
            with guard:
                results.append(r)
        except RefereeError as exc:
            with guard:
                errors.append(exc)

    threads = [threading.Thread(target=run, args=(f,)) for f in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return results, errors


def _total(d):
    a, b = d.score()
    return a + b


def _event_count(rt, match_id):
    return len(rt.store.events_for_match(match_id))


def _revisions_unique(rt, match_id):
    revs = [e.revision for e in rt.store.events_for_match(match_id)]
    return len(revs) == len(set(revs))


def test_concurrent_awards_same_rally_one_wins(rt, d):
    _setup(d)
    rally = d.arm()
    rev = d.snapshot()["revision"]
    before = _total(d)
    fns = [
        (lambda i=i: rt.service.handle(
            d.match_id, _cmd("point.award", {"rally_id": rally, "winner_id": "p1" if i % 2 else "p2", "reason": "unknown"}, rev=rev), "operator"))
        for i in range(N)
    ]
    results, errors = _race(fns)
    assert len(results) == 1 and not results[0].duplicate
    assert len(errors) == N - 1
    assert all(e.http_status == 409 and e.code in ("stale_revision", "stale_rally") for e in errors)
    assert _total(d) == before + 1
    assert _revisions_unique(rt, d.match_id)


def test_operator_award_vs_cv_proposal_race(rt, d):
    _setup(d, camera=True)
    rally = d.arm()
    snap = d.snapshot()
    rev = snap["revision"]
    prop = _proposal(d, "p2")
    fns = [
        lambda: rt.service.handle(
            d.match_id, _cmd("point.award", {"rally_id": rally, "winner_id": "p1", "reason": "unknown"}, rev=rev), "operator"),
        lambda: rt.service.handle(d.match_id, _cmd("point.propose", prop, rev=rev), "sim"),
    ]
    results, errors = _race(fns)
    assert len(results) == 1 and len(errors) == 1
    assert errors[0].http_status == 409
    assert _total(d) <= 1
    assert _revisions_unique(rt, d.match_id)


def test_same_command_id_concurrent_identical(rt, d):
    _setup(d)
    rally = d.arm()
    rev = d.snapshot()["revision"]
    cid = str(uuid.uuid4())
    body = {"rally_id": rally, "winner_id": "p1", "reason": "unknown"}
    fns = [(lambda: rt.service.handle(d.match_id, _cmd("point.award", body, rev=rev, command_id=cid), "operator"))
           for _ in range(N)]
    results, errors = _race(fns)
    assert not errors
    assert sum(1 for r in results if not r.duplicate) == 1
    assert sum(1 for r in results if r.duplicate) == N - 1
    assert len({tuple(r.event_ids) for r in results}) == 1
    assert d.score() == (1, 0)


def test_command_id_conflicts(rt, d):
    _setup(d)
    rally = d.arm()
    rev = d.snapshot()["revision"]
    cid = str(uuid.uuid4())
    rt.service.handle(d.match_id, _cmd("point.award", {"rally_id": rally, "winner_id": "p1", "reason": "unknown"}, rev=rev, command_id=cid), "operator")
    with pytest.raises(ConflictError) as ei:
        rt.service.handle(d.match_id, _cmd("point.award", {"rally_id": rally, "winner_id": "p2", "reason": "unknown"}, rev=rev, command_id=cid), "operator")
    assert ei.value.code == "command_id_conflict" and ei.value.http_status == 409

    first = d.match_id
    _setup(d)  # second match; driver now points at it
    assert d.match_id != first
    with pytest.raises(ConflictError) as ei:
        rt.service.handle(d.match_id, _cmd("rally.arm", rev=d.snapshot()["revision"], command_id=cid), "operator")
    assert ei.value.code == "command_id_conflict"
    assert d.score() == (0, 0)


def test_idempotency_checked_before_revision(rt, d):
    _setup(d)
    rally = d.arm()
    rev = d.snapshot()["revision"]
    cmd = _cmd("point.award", {"rally_id": rally, "winner_id": "p1", "reason": "unknown"}, rev=rev)
    first = rt.service.handle(d.match_id, cmd, "operator")
    d.point("p2")  # match moves on, rev is now outdated
    again = rt.service.handle(d.match_id, cmd, "operator")
    assert again.duplicate and again.event_ids == first.event_ids
    assert again.snapshot == first.snapshot
    assert d.score() == (1, 1)


def test_rally_uniqueness_at_db_level(rt, d):
    _setup(d)
    rally = d.arm()
    d.ok("point.award", {"rally_id": rally, "winner_id": "p1", "reason": "unknown"})
    snap = rt.service.get_snapshot(d.match_id)
    state = rt.service.get_state(d.match_id)
    before = _event_count(rt, d.match_id)
    cid = str(uuid.uuid4())
    with pytest.raises(ConflictError) as ei:
        rt.store.append_transaction(
            scope=f"match:{d.match_id}",
            command_id=cid,
            payload_hash="x",
            match_id=d.match_id,
            expected_previous_revision=snap.revision,
            events=[],
            snapshot=snap,
            state_json=state.model_dump_json(),
            response_json="{}",
            rally_decision=(rally, "fake-event"),
        )
    assert ei.value.code == "rally_already_decided"
    assert _event_count(rt, d.match_id) == before
    assert rt.store.get_command(cid) is None
    assert rt.store.load_match(d.match_id)[0].revision == snap.revision


def test_second_writer_gets_stale_revision(rt, d):
    _setup(d)
    other = RefereeService(SqliteEventStore(rt.store.path))
    try:
        rev = other.get_snapshot(d.match_id).revision  # loads + caches state now
        d.arm()  # first writer advances
        with pytest.raises(ConflictError) as ei:
            other.handle(d.match_id, _cmd("rally.arm", rev=rev), "operator")
        assert ei.value.code == "stale_revision"
        assert _revisions_unique(rt, d.match_id)
        assert rt.service.rebuild_from_events(d.match_id) == rt.service.get_state(d.match_id)
    finally:
        other.store.close()
    with sqlite3.connect(rt.store.path) as c:
        dup = c.execute("SELECT revision, COUNT(*) FROM events WHERE match_id=? GROUP BY revision HAVING COUNT(*)>1",
                        (d.match_id,)).fetchall()
    assert dup == []


@pytest.mark.parametrize(
    "type_,payload_fn,actor",
    [
        ("point.award", lambda r: {"rally_id": r, "winner_id": "p1", "reason": "unknown"}, "vision"),
        ("point.award", lambda r: {"rally_id": r, "winner_id": "p1", "reason": "unknown"}, "sim"),
        ("point.award", lambda r: {"rally_id": r, "winner_id": "p1", "reason": "unknown"}, "persona"),
        ("rally.arm", lambda r: {}, "persona"),
        ("persona.set", lambda r: {"persona": "regular"}, "persona"),
    ],
)
def test_forbidden_actors(rt, d, type_, payload_fn, actor):
    _setup(d)
    rally = d.arm()
    with pytest.raises(ForbiddenError) as ei:
        rt.service.handle(d.match_id, _cmd(type_, payload_fn(rally), rev=d.snapshot()["revision"]), actor)
    assert ei.value.http_status == 403
    assert d.score() == (0, 0)


def test_robot_cannot_start_match(rt, d):
    d.create(scoring_mode="assisted", calibration_id="table-1-camera-a-v1")
    with pytest.raises(ForbiddenError) as ei:
        rt.service.handle(d.match_id, _cmd("match.start", rev=d.snapshot()["revision"]), "robot")
    assert ei.value.http_status == 403
