"""Faza 4: adapter selection by config, output failure visible in health, camera loss."""

import pytest
from fastapi.testclient import TestClient

from table_tennis.api.app import create_app
from table_tennis.api.runtime import RealModeNotAvailable, build_runtime
from table_tennis.config import load_settings
from table_tennis.robot.a2_adapters import A2GestureOutput, A2RobotNavigator, A2ScoreDisplay
from table_tennis.persona.speech import LiveKitSpeechOutput
from table_tennis.sim.driver import InProcessDriver
from table_tennis.sim.scenarios import _proposal, _setup


def _settings(tmp_path, **kw):
    return load_settings(env={}, storage__db_path=str(tmp_path / "p4.sqlite"), outputs__fake_log_path="", **kw)


def test_dry_run_a2_adapters_selected_by_config(tmp_path):
    s = _settings(tmp_path, adapters={"display": "a2", "gesture": "a2", "speech": "livekit", "navigator": "a2"})
    rt = build_runtime(s, background=False)
    rt.start()
    try:
        assert isinstance(rt.display, A2ScoreDisplay) and rt.display.dry_run
        assert isinstance(rt.gesture, A2GestureOutput) and rt.gesture.dry_run
        assert isinstance(rt.speech, LiveKitSpeechOutput) and rt.speech.dry_run
        assert isinstance(rt.navigator, A2RobotNavigator) and rt.navigator.dry_run
        d = InProcessDriver(rt)
        _setup(d, scoring_mode="manual")
        d.point("p2")
        shows = [args for action, args in rt.display.sent if action == "screen.show"]
        assert shows and "0 : 1" in shows[-1]["primary"]
        assert shows[-1]["revision"] == d.snapshot()["revision"]
        assert any(action == "gesture.play" and args["name"] == "point right" for action, args in rt.gesture.sent)
        assert any("Nula prema jedan" in t for t in rt.speech.sent)
        assert rt.adapter_info["screen"] == {"adapter": "a2", "dry_run": True, "simulated": True}
    finally:
        rt.stop()


def test_real_mode_rejects_fake_adapters(tmp_path):
    with pytest.raises(RealModeNotAvailable, match="fake adapters"):
        build_runtime(_settings(tmp_path, mode="real"))


def test_real_mode_without_transport_refuses(tmp_path):
    s = _settings(tmp_path, mode="real", adapters={"display": "a2", "gesture": "a2", "speech": "livekit", "navigator": "a2"})
    with pytest.raises(RealModeNotAvailable, match="transport|not implemented"):
        build_runtime(s)
    assert not (tmp_path / "p4.sqlite").exists(), "refusal happens before the DB is opened"


class _FlakySpeech:
    def __init__(self):
        self.fail = True
        self.spoken = []

    def announce(self, event, snapshot):
        if self.fail:
            raise RuntimeError("tts offline")
        self.spoken.append(event.event_id)

    def cancel_pending(self, match_id):
        pass


def test_output_failure_visible_in_health_and_point_kept(tmp_path):
    rt = build_runtime(_settings(tmp_path), background=False)
    flaky = _FlakySpeech()
    rt.dispatcher.speech = flaky
    with TestClient(create_app(runtime=rt)) as c:
        d = InProcessDriver(rt)
        _setup(d, scoring_mode="manual")
        d.point("p1")
        assert d.score() == (1, 0)
        h = c.get("/api/table-tennis/health").json()
        assert h["capabilities"]["speech"]["available"] is False
        assert "tts offline" in h["capabilities"]["speech"]["detail"]
        assert h["capabilities"]["screen"]["available"] is True
        flaky.fail = False
        d.point("p1")
        h = c.get("/api/table-tennis/health").json()
        assert h["capabilities"]["speech"]["available"] is True
        assert d.score() == (2, 0)


def test_camera_loss_pauses_cv_but_manual_score_stays(d):
    _setup(d, scoring_mode="assisted", camera=True)
    d.arm()
    d.ok("camera.ready.set", {"ready": False, "reason": "camera unplugged"}, actor="sim", expected_revision=None)
    s = d.snapshot()
    assert s["ready"]["camera_ready"] is False and s["scoring_mode"] == "assisted"
    d.rejected("camera_not_ready", "point.propose", _proposal(d), actor="sim")
    d.ok("point.award", {"rally_id": s["active_rally_id"], "winner_id": "p2"})
    assert d.score() == (0, 1)
    assert d.snapshot()["scoring_mode"] == "assisted", "no hidden mode change"
