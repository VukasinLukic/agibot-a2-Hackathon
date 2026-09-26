"""HTTP API tests for /api/table-tennis (contract v1, section 8)."""

import socket
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from table_tennis.api.app import create_app
from table_tennis.api.runtime import build_runtime
from table_tennis.api.supervisor import include_table_tennis
from table_tennis.config import check_bind_allowed, load_settings
from table_tennis import run_demo

P = "/api/table-tennis"
TOKEN_ENV = {"TT_AUTH_MODE": "token", "TT_OPERATOR_TOKEN": "op-secret", "TT_VISION_TOKEN": "cv-secret",
             "TT_PERSONA_TOKEN": "voice-secret"}
OP = {"Authorization": "Bearer op-secret"}
CV = {"Authorization": "Bearer cv-secret"}
VOICE = {"Authorization": "Bearer voice-secret"}


def cid():
    return str(uuid.uuid4())


def create_body(command_id=None, **config):
    cfg = {"scoring_mode": "manual"}
    cfg.update(config)
    return {
        "command_id": command_id or cid(),
        "players": [
            {"id": "p1", "display_name": "Ana", "role_label": "direktorka", "role_rank": 3},
            {"id": "p2", "display_name": "Marko", "role_label": "inzenjer", "role_rank": 2},
        ],
        "calibration_id": "table-1-camera-a-v1",
        "config": cfg,
    }


def cmd(type_, payload=None, rev=None, command_id=None):
    return {"command_id": command_id or cid(), "expected_revision": rev, "type": type_, "payload": payload or {}}


def make_client(tmp_path, env=None):
    e = {"TT_AUTH_MODE": "local"}
    if env is not None:
        e = dict(env)
    settings = load_settings(env=e, storage__db_path=str(tmp_path / "tt.sqlite"), outputs__fake_log_path="")
    app = create_app(settings=settings, runtime=build_runtime(settings, background=False))
    return TestClient(app)


@pytest.fixture
def c(tmp_path):
    with make_client(tmp_path) as client:
        yield client


@pytest.fixture
def tc(tmp_path):
    with make_client(tmp_path, TOKEN_ENV) as client:
        yield client


def new_match(c, headers=None, **config):
    r = c.post(f"{P}/matches", json=create_body(**config), headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


def to_rally(c, headers=None, camera_headers=None, **config):
    h = headers or {}
    m = new_match(c, h, **config)
    mid = m["match_id"]
    r = c.post(f"{P}/matches/{mid}/commands", json=cmd("robot.ready.set", {"ready": True, "reason": "manual_arrival"}), headers=h)
    assert r.status_code == 200, r.text
    if config.get("scoring_mode") == "assisted":
        r = c.post(f"{P}/matches/{mid}/commands", json=cmd("camera.ready.set", {"ready": True, "reason": "test"}), headers=camera_headers or {"X-TT-Actor": "vision"})
        assert r.status_code == 200, r.text
    rev = r.json()["snapshot"]["revision"]
    r = c.post(f"{P}/matches/{mid}/commands", json=cmd("match.start", rev=rev), headers=h)
    assert r.status_code == 200, r.text
    r = c.post(f"{P}/matches/{mid}/commands", json=cmd("rally.arm", rev=r.json()["snapshot"]["revision"]), headers=h)
    assert r.status_code == 200, r.text
    return r.json()["snapshot"]


# ---------------------------------------------------------------- health / matches

def test_health(c):
    r = c.get(f"{P}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock" and body["simulated"] is True and body["automatic_scoring_enabled"] is False


def test_create_match_idempotency(c):
    body = create_body()
    r1 = c.post(f"{P}/matches", json=body)
    assert r1.status_code == 201
    r2 = c.post(f"{P}/matches", json=body)
    assert r2.status_code == 200 and r2.json()["match_id"] == r1.json()["match_id"]
    other = create_body(command_id=body["command_id"])
    other["players"][0]["display_name"] = "Jelena"
    r3 = c.post(f"{P}/matches", json=other)
    assert r3.status_code == 409


def test_create_match_best_of_and_malformed(c):
    r = c.post(f"{P}/matches", json=create_body(best_of=3))
    assert r.status_code == 422 and r.json()["code"] == "unsupported_best_of"
    r = c.post(f"{P}/matches", json={"players": "nope"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"
    assert "errors" in r.json()["details"]


def test_command_errors(c):
    r = c.get(f"{P}/matches/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["code"] == "match_not_found"

    snap = to_rally(c)
    mid, rid = snap["match_id"], snap["active_rally_id"]
    award = {"rally_id": rid, "winner_id": "p1", "reason": "unknown"}

    r = c.post(f"{P}/matches/{mid}/commands", json=cmd("point.award", award, rev=snap["revision"] - 1))
    assert r.status_code == 409 and r.json()["current_revision"] == snap["revision"]

    r = c.post(f"{P}/matches/{mid}/commands", json=cmd("no.such.command", {}, rev=snap["revision"]))
    assert r.status_code == 422

    prop = {"proposal_id": cid(), "rally_id": rid, "winner_id": "p2", "confidence": 1.5,
            "reason": "missed_return", "calibration_id": "table-1-camera-a-v1",
            "assignment_version": 1, "capture_start_seq": 1, "capture_end_seq": 2}
    r = c.post(f"{P}/matches/{mid}/commands", json=cmd("point.propose", prop, rev=snap["revision"]), headers={"X-TT-Actor": "vision"})
    assert r.status_code == 422

    r = c.post(f"{P}/matches/{mid}/commands", json=cmd("point.award", award, rev=snap["revision"]), headers={"X-TT-Actor": "vision"})
    assert r.status_code == 403
    r = c.post(f"{P}/matches/{mid}/commands", json=cmd("point.award", award, rev=snap["revision"]), headers={"X-TT-Actor": "mallory"})
    assert r.status_code == 401
    r = c.post(f"{P}/matches/{mid}/commands", json=cmd("point.award", award, rev=snap["revision"]), headers={"X-TT-Actor": "persona"})
    assert r.status_code == 403


def test_full_manual_flow(c):
    snap = to_rally(c)
    mid = snap["match_id"]
    r = c.post(f"{P}/matches/{mid}/commands",
               json=cmd("point.award", {"rally_id": snap["active_rally_id"], "winner_id": "p1", "reason": "unknown"}, rev=snap["revision"]))
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["snapshot"]["score_by_player"] == {"p1": 1, "p2": 0}
    got = c.get(f"{P}/matches/{mid}")
    assert got.status_code == 200 and got.json() == result["snapshot"]
    assert mid in c.get(f"{P}/matches").json()

    out = c.get(f"{P}/debug/outputs", params={"match_id": mid})
    assert out.status_code == 200
    display = out.json()["display"]
    assert display is not None and "1" in display["text"] and "0" in display["text"]


# ---------------------------------------------------------------- robot

def test_robot_calls(c):
    body = {"command_id": cid(), "table_id": "table-1", "named_waypoint_id": "referee-spot"}
    r1 = c.post(f"{P}/robot/calls", json=body)
    assert r1.status_code == 202, r1.text
    call_id = r1.json()["call_id"]
    r2 = c.post(f"{P}/robot/calls", json=body)
    assert r2.status_code == 202 and r2.json()["call_id"] == call_id
    r3 = c.post(f"{P}/robot/calls", json=dict(body, command_id=cid()))
    assert r3.status_code == 409 and r3.json()["code"] == "robot_busy"

    assert c.get(f"{P}/robot/calls/{call_id}").json()["call_id"] == call_id
    st = c.get(f"{P}/robot/status")
    assert st.status_code == 200 and "availability" in st.json()

    r = c.post(f"{P}/robot/calls/{call_id}/cancel", json={"command_id": cid()})
    assert r.status_code == 202 and r.json()["call_id"] == call_id

    r = c.post(f"{P}/robot/calls", json=dict(body, command_id=cid(), named_waypoint_id="nowhere"))
    assert r.status_code == 422
    assert c.get(f"{P}/robot/calls/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------- token mode

def test_token_mode(tc):
    assert tc.get(f"{P}/health").status_code == 401
    assert tc.post(f"{P}/matches", json=create_body()).status_code == 401
    assert tc.get(f"{P}/health", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert tc.get(f"{P}/health", params={"token": "op-secret"}).status_code == 401
    assert tc.get(f"{P}/health", params={"access_token": "op-secret"}).status_code == 401
    # X-TT-Actor is ignored in token mode
    assert tc.get(f"{P}/health", headers={"X-TT-Actor": "operator"}).status_code == 401

    snap = to_rally(tc, OP, camera_headers=CV, scoring_mode="assisted")
    mid, rid = snap["match_id"], snap["active_rally_id"]
    assert tc.get(f"{P}/matches/{mid}/events").status_code == 401
    assert tc.get(f"{P}/matches/{mid}", headers=OP).status_code == 200

    award = {"rally_id": rid, "winner_id": "p1", "reason": "unknown"}
    r = tc.post(f"{P}/matches/{mid}/commands", json=cmd("point.award", award, rev=snap["revision"]), headers=CV)
    assert r.status_code == 403
    prop = {"proposal_id": cid(), "rally_id": rid, "winner_id": "p2", "confidence": 0.9,
            "reason": "missed_return", "calibration_id": "table-1-camera-a-v1",
            "assignment_version": 1, "capture_start_seq": 1, "capture_end_seq": 2}
    r = tc.post(f"{P}/matches/{mid}/commands", json=cmd("point.propose", prop, rev=snap["revision"]), headers=CV)
    assert r.status_code == 200, r.text
    assert r.json()["snapshot"]["active_proposal_id"] == prop["proposal_id"]


def test_persona_token_is_read_only(tc):
    """The voice agent (referee_mode.py) reads the match with TT_PERSONA_TOKEN but can never write."""
    snap = to_rally(tc, OP)
    mid, rid = snap["match_id"], snap["active_rally_id"]

    assert tc.get(f"{P}/health", headers=VOICE).status_code == 200
    r = tc.get(f"{P}/matches", headers=VOICE)
    assert r.status_code == 200 and mid in r.json()
    r = tc.get(f"{P}/matches/{mid}", headers=VOICE)
    assert r.status_code == 200 and r.json()["status"] == "rally"

    # no command, no match creation, no robot call
    award = {"rally_id": rid, "winner_id": "p1", "reason": "unknown"}
    r = tc.post(f"{P}/matches/{mid}/commands", json=cmd("point.award", award, rev=snap["revision"]), headers=VOICE)
    assert r.status_code == 403
    r = tc.post(f"{P}/matches/{mid}/commands", json=cmd("match.pause", rev=snap["revision"]), headers=VOICE)
    assert r.status_code == 403
    assert tc.post(f"{P}/matches", json=create_body(), headers=VOICE).status_code == 403
    call = {"command_id": cid(), "table_id": "table-1", "named_waypoint_id": "referee-spot"}
    assert tc.post(f"{P}/robot/calls", json=call, headers=VOICE).status_code == 403
    assert tc.get(f"{P}/matches/{mid}", headers=OP).json()["revision"] == snap["revision"]


# ---------------------------------------------------------------- settings

def test_settings_validation(tmp_path):
    with pytest.raises(ValueError):
        load_settings(env={}, features__automatic_scoring=True)
    with pytest.raises(ValueError):
        load_settings(env={"TT_AUTH_MODE": "token", "TT_OPERATOR_TOKEN": ""})
    check_bind_allowed(load_settings(env={}, server__host="0.0.0.0"))
    check_bind_allowed(load_settings(env=TOKEN_ENV, server__host="0.0.0.0"))
    assert load_settings(env=TOKEN_ENV).auth.tokens.persona == "voice-secret"
    with pytest.raises(ValueError):  # one token must map to exactly one actor
        load_settings(env={**TOKEN_ENV, "TT_PERSONA_TOKEN": "op-secret"})
    check_bind_allowed(load_settings(env={}))


# ---------------------------------------------------------------- supervisor hook

def _routes(app):
    # FastAPI may include routers lazily, so read the paths from the schema
    return [path for path in app.openapi().get("paths", {}) if path.startswith(P)]


def test_supervisor_disabled_by_default():
    app = FastAPI()
    assert include_table_tennis(app, env={}) is False
    assert _routes(app) == []


def test_supervisor_enabled_without_token(tmp_path):
    app = FastAPI()
    env = {"TABLE_TENNIS_ENABLED": "1", "TT_DB_PATH": str(tmp_path / "s.sqlite")}
    assert include_table_tennis(app, env=env) is True
    assert f"{P}/health" in _routes(app)


def test_supervisor_enabled_with_token(tmp_path, monkeypatch):
    import table_tennis.api.runtime as runtime_mod

    created = []
    real_build = runtime_mod.build_runtime

    def spy(*a, **kw):
        created.append(real_build(*a, **kw))
        return created[-1]

    monkeypatch.setattr(runtime_mod, "build_runtime", spy)
    app = FastAPI()
    env = dict(TOKEN_ENV, TABLE_TENNIS_ENABLED="1", TT_DB_PATH=str(tmp_path / "s.sqlite"),
               TT_FAKE_LOG_PATH=str(tmp_path / "fake.log"))
    assert include_table_tennis(app, env=env) is True
    assert f"{P}/health" in _routes(app)
    with TestClient(app) as client:
        assert client.get(f"{P}/health").status_code == 401
        r = client.get(f"{P}/health", headers=OP)
        assert r.status_code == 200 and r.json()["auth_mode"] == "token"
    assert len(created) == 1
    for runtime in created:
        runtime.stop()

# ---------------------------------------------------------------- run_demo

def test_run_demo_port_and_host(tmp_path, monkeypatch):
    for k in list(TOKEN_ENV) + ["TT_CONFIG", "TT_HOST", "TT_PORT", "TT_MODE"]:
        monkeypatch.delenv(k, raising=False)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert run_demo.port_is_free("127.0.0.1", port) is False
        assert run_demo.main(["--port", str(port), "--db", str(tmp_path / "d.sqlite")]) == 2
    finally:
        s.close()
    assert run_demo.main(["--host", "0.0.0.0", "--db", str(tmp_path / "d.sqlite")]) == 2
