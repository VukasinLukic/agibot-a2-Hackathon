"""SSE stream tests: service level (SyncSubscriber) and real HTTP (uvicorn + httpx)."""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn

from table_tennis.api.app import create_app
from table_tennis.api.runtime import build_runtime
from table_tennis.config import load_settings
from table_tennis.core.stream import SyncSubscriber
from table_tennis.sim.scenarios import _setup

PREFIX = "/api/table-tennis"


# --------------------------------------------------------------------------- helpers


def _data(msg):
    return json.loads(msg.data)


def _get(sub: SyncSubscriber, timeout: float = 2.0):
    return sub.queue.get(timeout=timeout)


def _events(msgs):
    return [m for m in msgs if m.event == "match_event"]


@pytest.fixture
def md(d):
    _setup(d, scoring_mode="manual")
    return d


# --------------------------------------------------------------------------- A) service level


def test_initial_snapshot_cursor_is_latest(md, rt):
    sub = SyncSubscriber()
    initial = rt.service.subscribe(md.match_id, sub)
    assert len(initial) == 1 and initial[0].event == "snapshot"
    body = _data(initial[0])
    assert body["cursor"] == rt.store.latest_cursor(md.match_id)
    assert body.get("resync") in (False, None)
    assert body["snapshot"]["revision"] == md.snapshot()["revision"]


def test_command_delivers_events_then_snapshot(md, rt):
    sub = SyncSubscriber()
    start = _data(rt.service.subscribe(md.match_id, sub)[0])["cursor"]
    md.arm()
    msgs = sub.drain()
    assert msgs and msgs[-1].event == "snapshot"
    evs = _events(msgs)
    assert evs and all(m.event == "match_event" for m in msgs[:-1])
    cursors = [_data(m)["cursor"] for m in evs]
    assert cursors == sorted(cursors) and cursors[0] > start
    assert len(set(cursors)) == len(cursors)
    for m in evs:
        assert m.id == _data(m)["event"]["event_id"]
    assert _data(msgs[-1])["cursor"] == cursors[-1]


def test_point_confirmed_and_match_finished_both_delivered(md, rt):
    for _ in range(10):
        md.point("p1")
    sub = SyncSubscriber()
    rt.service.subscribe(md.match_id, sub)
    res = md.point("p1")
    assert res["snapshot"]["status"] == "finished"
    msgs = sub.drain()
    types = [_data(m)["event"]["type"] for m in _events(msgs)]
    assert "point.confirmed" in types and "match.finished" in types
    assert msgs[-1].event == "snapshot"
    assert _data(msgs[-1])["snapshot"]["status"] == "finished"


def test_reconnect_replays_exactly_missed(md, rt):
    sub = SyncSubscriber()
    rt.service.subscribe(md.match_id, sub)
    md.point("p1")
    seen = _events(sub.drain())
    last_seen = seen[-1].id
    rt.service.unsubscribe(md.match_id, sub)
    md.point("p2")
    md.point("p1")
    expected = [e.event_id for _, e in rt.store.read_after(md.match_id, _data(seen[-1])["cursor"], limit=1000)]
    assert expected
    sub2 = SyncSubscriber()
    initial = rt.service.subscribe(md.match_id, sub2, last_event_id=last_seen)
    ids = [m.id for m in _events(initial)]
    assert ids == expected
    assert not set(ids) & {m.id for m in seen}
    assert initial[-1].event == "snapshot"
    snap = _data(initial[-1])
    assert snap["resync"] is False and snap["cursor"] == rt.store.latest_cursor(md.match_id)


def test_unknown_last_event_id_resyncs(md, rt):
    md.point("p1")
    initial = rt.service.subscribe(md.match_id, SyncSubscriber(), last_event_id="no-such-event")
    assert len(initial) == 1 and initial[0].event == "snapshot"
    assert _data(initial[0])["resync"] is True


def test_event_id_of_other_match_resyncs(d, rt):
    _setup(d, scoring_mode="manual")
    d.point("p1")
    other_event = rt.store.read_after(d.match_id, 0, limit=1000)[-1][1].event_id
    _setup(d, scoring_mode="manual")
    initial = rt.service.subscribe(d.match_id, SyncSubscriber(), last_event_id=other_event)
    assert len(initial) == 1 and _data(initial[0])["resync"] is True


def test_snapshot_boundary_race(md, rt):
    service = rt.service
    before_rev = md.snapshot()["revision"]
    errors: list = []
    started = threading.Event()

    def worker():
        started.set()
        try:
            md.arm()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t = threading.Thread(target=worker, daemon=True)

    def hook():
        t.start()
        started.wait(2)
        time.sleep(0.1)  # give the worker a chance to block on the match lock

    service._subscribe_hook = hook
    sub = SyncSubscriber()
    try:
        initial = service.subscribe(md.match_id, sub)
    finally:
        service._subscribe_hook = None
    t.join(5)
    assert not t.is_alive() and not errors
    snap = _data(initial[-1])
    assert snap["snapshot"]["revision"] == before_rev
    msgs = sub.drain()
    evs = _events(msgs)
    stored = [(c, e.event_id) for c, e in rt.store.read_after(md.match_id, snap["cursor"], limit=1000)]
    assert stored
    assert [(_data(m)["cursor"], m.id) for m in evs] == stored
    assert msgs[-1].event == "snapshot"
    assert _data(msgs[-1])["snapshot"]["revision"] > before_rev


def test_lagging_subscriber_does_not_block_engine(md, rt):
    sub = SyncSubscriber(maxsize=1)
    rt.service.subscribe(md.match_id, sub)
    for _ in range(3):
        md.point("p1")
    assert sub.lagging is True
    assert md.score() == (3, 0)
    rt.service.unsubscribe(md.match_id, sub)


# --------------------------------------------------------------------------- B) real HTTP


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    settings = load_settings(
        env={},
        storage__db_path=str(tmp_path / "sse.sqlite"),
        outputs__fake_log_path="",
        server__sse_heartbeat_s=0.5,
    )
    runtime = build_runtime(settings, background=False)
    app = create_app(runtime=runtime)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    deadline = time.time() + 5
    while not srv.started:
        assert time.time() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    try:
        yield base, runtime
    finally:
        srv.should_exit = True
        th.join(5)
        assert not th.is_alive()


class SseReader:
    """Reads an SSE stream on a background thread into a queue."""

    def __init__(self, base: str, path: str, headers=None):
        self.q: queue.Queue = queue.Queue()
        self.heartbeats = 0
        self._stop = threading.Event()
        self._client = httpx.Client(base_url=base, timeout=httpx.Timeout(10.0, read=5.0))
        self._path = path
        self._headers = headers or {}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            with self._client.stream("GET", self._path, headers=self._headers) as resp:
                self.status = resp.status_code
                self.content_type = resp.headers.get("content-type", "")
                cur: dict = {}
                for line in resp.iter_lines():
                    if self._stop.is_set():
                        return
                    if line == "":
                        if cur:
                            self.q.put(cur)
                            cur = {}
                        continue
                    if line.startswith(":"):
                        if "heartbeat" in line:
                            self.heartbeats += 1
                        continue
                    key, _, val = line.partition(":")
                    cur[key] = val[1:] if val.startswith(" ") else val
        except Exception as exc:
            if not self._stop.is_set():
                self.q.put({"error": repr(exc)})

    def next(self, timeout: float = 3.0) -> dict:
        msg = self.q.get(timeout=timeout)
        assert "error" not in msg, msg
        msg["json"] = json.loads(msg["data"])
        return msg

    def until_snapshot(self, timeout: float = 3.0) -> list[dict]:
        out = []
        while True:
            m = self.next(timeout)
            out.append(m)
            if m["event"] == "snapshot":
                return out

    def close(self):
        self._stop.set()
        self._client.close()
        self._thread.join(3)


def _post_cmd(c: httpx.Client, mid: str, type_: str, payload=None, auto_rev: bool = True):
    rev = c.get(f"{PREFIX}/matches/{mid}").json()["revision"] if auto_rev else None
    r = c.post(
        f"{PREFIX}/matches/{mid}/commands",
        json={"command_id": str(uuid.uuid4()), "expected_revision": rev, "type": type_, "payload": payload or {}},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _point(c, mid, winner):
    rally = _post_cmd(c, mid, "rally.arm")["snapshot"]["active_rally_id"]
    return _post_cmd(c, mid, "point.award", {"rally_id": rally, "winner_id": winner, "reason": "unknown"})


def test_http_sse_live_reconnect_heartbeat(server):
    base, runtime = server
    c = httpx.Client(base_url=base, timeout=5.0)
    try:
        r = c.post(
            f"{PREFIX}/matches",
            json={
                "command_id": str(uuid.uuid4()),
                "players": [
                    {"id": "p1", "display_name": "Ana", "role_label": "a", "role_rank": 1},
                    {"id": "p2", "display_name": "Marko", "role_label": "b", "role_rank": 2},
                ],
                "config": {"scoring_mode": "manual"},
            },
        )
        assert r.status_code == 201, r.text
        mid = r.json()["match_id"]
        _post_cmd(c, mid, "robot.ready.set", {"ready": True, "reason": "manual_arrival"}, auto_rev=False)
        _post_cmd(c, mid, "match.start")
        path = f"{PREFIX}/matches/{mid}/events"

        # unrelated route semantics: no generic /api/events on this app
        assert c.get("/api/events").status_code == 404

        rd = SseReader(base, path)
        first = rd.next()
        assert rd.status == 200 and rd.content_type.startswith("text/event-stream")
        assert first["event"] == "snapshot"
        assert first["json"]["cursor"] == runtime.store.latest_cursor(mid)

        # commands sent in parallel from another thread, read live
        t = threading.Thread(target=lambda: (_point(httpx.Client(base_url=base, timeout=5.0), mid, "p1")), daemon=True)
        t.start()
        live = []
        while len([m for m in live if m["event"] == "snapshot"]) < 2:
            live.append(rd.next())
        t.join(5)
        live_events = [m for m in live if m["event"] == "match_event"]
        assert live_events and all(m["id"] == m["json"]["event"]["event_id"] for m in live_events)
        cursors = [m["json"]["cursor"] for m in live_events]
        assert cursors == sorted(cursors) and len(set(cursors)) == len(cursors)

        # heartbeat
        deadline = time.time() + 3
        while rd.heartbeats == 0 and time.time() < deadline:
            time.sleep(0.05)
        assert rd.heartbeats >= 1
        rd.close()

        last_seen = live_events[-1]["id"]
        seen_ids = {m["id"] for m in live_events}

        # missed commands while disconnected
        _point(c, mid, "p2")
        expected = [e.event_id for _, e in runtime.store.read_after(mid, cursors[-1], limit=1000)]
        assert expected

        for kwargs in ({"path": f"{path}?last_event_id={last_seen}"}, {"path": path, "headers": {"Last-Event-ID": last_seen}}):
            rd2 = SseReader(base, **kwargs)
            try:
                got = rd2.until_snapshot()
                ids = [m["id"] for m in got if m["event"] == "match_event"]
                assert ids == expected
                assert not set(ids) & seen_ids
                assert got[-1]["json"]["resync"] is False
                assert got[-1]["json"]["cursor"] == runtime.store.latest_cursor(mid)
            finally:
                rd2.close()

        # unknown id over HTTP -> resync snapshot
        rd3 = SseReader(base, f"{path}?last_event_id=bogus")
        try:
            m = rd3.next()
            assert m["event"] == "snapshot" and m["json"]["resync"] is True
        finally:
            rd3.close()
    finally:
        c.close()
