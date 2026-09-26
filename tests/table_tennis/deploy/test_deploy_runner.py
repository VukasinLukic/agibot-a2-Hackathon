"""scripts/titansudija_deploy.py: pure helpers and safety gates, no robot, no network."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "titansudija_deploy.py"
spec = importlib.util.spec_from_file_location("titansudija_deploy", SCRIPT)
deploy = importlib.util.module_from_spec(spec)
sys.modules["titansudija_deploy"] = deploy
spec.loader.exec_module(deploy)


def _args(**over):
    base = dict(command="up", robot="agi@192.168.2.50", supervisor_url="http://192.168.2.50:8070",
                remote_dir="/agibot/humanoid-platform", sync="skip", git_remote="origin", vision_cmd="",
                vision_venv=".venv", speech_test=False, skip_tests=True, dry_run=True)
    base.update(over)
    return argparse.Namespace(**base)


def test_merge_env_appends_missing_and_never_overwrites():
    existing = "LIVEKIT_URL=wss://x\nexport TT_AUTH_MODE='local'\n# TT_ROBOT_TOKEN=commented\n"
    text, added, conflicts = deploy.merge_env(existing, {"TT_AUTH_MODE": "token", "TT_ROBOT_TOKEN": "r", "LIVEKIT_URL": "wss://x"})
    assert added == ["TT_ROBOT_TOKEN"] and conflicts == ["TT_AUTH_MODE"]
    assert text.startswith(existing) and text.endswith("TT_ROBOT_TOKEN=r\n")
    assert "TT_AUTH_MODE=token" not in text
    same, added, _ = deploy.merge_env(text, {"TT_ROBOT_TOKEN": "r"})
    assert same == text and added == []


def test_merge_env_source_is_self_contained():
    # it is shipped to the robot as source and exec'd there without this module
    namespace: dict = {}
    exec(deploy.inspect.getsource(deploy.merge_env), namespace)
    assert namespace["merge_env"]("", {"A": "1"})[1] == ["A"]


def test_resolve_tokens_prefers_robot_and_generates_distinct():
    counter = iter(range(100))
    tokens, generated, pulled = deploy.resolve_tokens(
        {"TT_VISION_TOKEN": "v"}, {"TT_OPERATOR_TOKEN": "op"}, lambda: f"gen{next(counter)}")
    assert tokens["TT_OPERATOR_TOKEN"] == "op" and pulled == ["TT_OPERATOR_TOKEN"]
    assert tokens["TT_VISION_TOKEN"] == "v"
    assert set(generated) == {"TT_ROBOT_TOKEN", "TT_PERSONA_TOKEN"}
    with pytest.raises(deploy.Abort):
        deploy.resolve_tokens({"TT_OPERATOR_TOKEN": "a"}, {"TT_OPERATOR_TOKEN": "b"}, lambda: "x")
    with pytest.raises(deploy.Abort):  # one token must map to exactly one actor
        deploy.resolve_tokens({}, {k: "same" for k in deploy.TOKEN_KEYS}, lambda: "x")


def test_dry_run_gates_never_approve(capsys):
    runner = deploy.Runner(_args())
    assert runner.gate("ARM robota", ["POST /api/nav/arm"], physical=True) is False
    assert runner.gate(".env", ["x"]) is False
    assert "dry-run" in capsys.readouterr().out


def test_gate_refuses_without_a_terminal(monkeypatch):
    runner = deploy.Runner(_args(dry_run=False))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(deploy.Abort):
        runner.gate("ARM robota", [], physical=True)


def test_physical_gate_needs_the_word_mentor(monkeypatch):
    runner = deploy.Runner(_args(dry_run=False))
    monkeypatch.setattr(runner, "log", lambda *a, **k: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    answers = iter(["da", "mentor"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert runner.gate("ARM", [], physical=True) is False
    answers = iter(["da", "MENTOR"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert runner.gate("ARM", [], physical=True) is True


@pytest.mark.parametrize("ips", ["192.168.100.100 192.168.2.50", "10.0.0.5"])
def test_identity_aborts_when_not_pc2(monkeypatch, ips):
    runner = deploy.Runner(_args())
    monkeypatch.setattr(runner, "ssh", lambda *a, **k: f"IPS:{ips}\nSHA:abc\nDIRTY:0\nSUP:yes\nVIS:no\nENV:yes\nCFG:yes\n")
    with pytest.raises(deploy.Abort):
        runner.robot_identity()


def test_identity_accepts_pc2_and_records_rollback_sha(monkeypatch):
    runner = deploy.Runner(_args())
    monkeypatch.setattr(runner, "ssh", lambda *a, **k: "IPS:192.168.2.50 192.168.100.110\nSHA:deadbeef\nDIRTY:0\nSUP:yes\nVIS:no\nENV:yes\nCFG:no\n")
    runner.robot_identity()
    assert runner.state["robot_sha_before"] == "deadbeef" and runner.facts["CFG"] == "no"


def test_vision_tmux_command_keeps_token_off_the_command_line(monkeypatch):
    runner = deploy.Runner(_args(dry_run=False, vision_cmd="python -m table_tennis.vision.run --camera X"))
    runner.facts = {"VIS": "no"}
    sent = []
    monkeypatch.setattr(runner, "gate", lambda *a, **k: True)
    monkeypatch.setattr(runner, "log", lambda *a, **k: None)
    monkeypatch.setattr(deploy.time, "sleep", lambda s: None)
    monkeypatch.setattr(runner, "ssh", lambda cmd, **k: sent.append(cmd) or "yes")
    runner.tokens = {"TT_VISION_TOKEN": "secret-value"}
    runner.vision_process()
    assert sent[0].startswith("tmux new-session -d -s tt_vision")
    assert "secret-value" not in sent[0] and "source <(sed" in sent[0]
