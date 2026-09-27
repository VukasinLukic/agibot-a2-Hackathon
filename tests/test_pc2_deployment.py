"""Offline deployment tests: no robot, AIMA, ROS or Supervisor API import."""
import importlib.util
import json
from pathlib import Path
import socket
import os
import shutil
import subprocess

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = load_script("pc2_deploy")
guard = load_script("check_git_secrets")


@pytest.fixture
def deployment(tmp_path):
    config = {
        "robot": {"id": "test", "platform": "agibot", "model": "agibot_a2_ultra"},
        "api": {"port": 8070},
        "services": [{"type": kind, "config": {}} for kind in ("livekit-server", "voice-agent", "audio-bridge")],
    }
    cfg = tmp_path / "robot_supervisor_v2/config.yaml"
    cfg.parent.mkdir()
    cfg.write_text(yaml.safe_dump(config), encoding="utf-8")
    for name in ("robot_supervisor_v2/dist/index.html", "robot_services/audio/audio_bridge.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test", encoding="utf-8")
    env = {
        "LIVEKIT_URL": "ws://127.0.0.1:7880", "LIVEKIT_API_KEY": "test-key", "LIVEKIT_API_SECRET": "test-only-value",
        "SONIOX_API_KEY": "test-only-soniox", "ELEVENLABS_API_KEY": "test-only-eleven",
        "AZURE_OPENAI_BASE": "https://example.invalid", "AZURE_OPENAI_API_KEY": "test-only-azure",
        "OPENAI_API_VERSION": "test-version", "TABLE_TENNIS_ENABLED": "1", "TT_AUTH_MODE": "token",
        "TT_API_URL": "http://127.0.0.1:8070", "TT_SUPERVISOR_URL": "http://127.0.0.1:8070",
        "RAG_COLLECTION_PREFIX": "team4_", "COMPANY_NAME": "Comtrade System Integration",
    }
    env.update({f"TT_{r}_TOKEN": f"test-only-{r}" for r in ("OPERATOR", "VISION", "PERSONA", "ROBOT", "SIM")})
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in env.items()), encoding="utf-8")
    return tmp_path


def test_valid_offline_check_preserves_literal_env(deployment):
    with (deployment / ".env").open("a", encoding="utf-8") as out:
        out.write('\nLITERAL="$(touch bad) ${UNSET} $HOME"\n')
    errors, warnings, _, env, port = deploy.collect(deployment, {})
    assert errors == []
    assert port == 8070
    assert env["COMPANY_NAME"] == "Comtrade System Integration"
    assert env["LITERAL"] == "$(touch bad) ${UNSET} $HOME"
    assert not (deployment / "bad").exists()
    assert any("speech.json absent" in w for w in warnings)


def test_missing_config_never_falls_back(deployment):
    (deployment / "robot_supervisor_v2/config.yaml").unlink()
    errors = deploy.collect(deployment, {})[0]
    assert any("fallback is NOT allowed" in e for e in errors)


def test_wrong_robot_rejected(deployment):
    cfg = deployment / "robot_supervisor_v2/config.yaml"
    data = yaml.safe_load(cfg.read_text())
    data["robot"].update(platform="unitree", model="unitree_g1_edu")
    cfg.write_text(yaml.safe_dump(data))
    assert any("agibot_a2_ultra" in e for e in deploy.collect(deployment, {})[0])


def test_port_and_hidden_env_rejected(deployment):
    app = deployment / "robot_supervisor_v2/app"
    app.mkdir()
    (app / ".env").write_text("TTS_PROVIDER=another")
    errors = deploy.collect(deployment, {"ROBOT_SUPERVISOR_PORT": "8080"})[0]
    assert any("app/.env" in e for e in errors)
    assert any("differs" in e for e in errors)


@pytest.mark.parametrize("payload", ["{", "[]", '{"voices": []}', '{"active_voice": null}'])
def test_corrupt_speech_does_not_silently_reset(deployment, payload):
    state = deployment / "robot_supervisor_v2/state"
    state.mkdir()
    (state / "speech.json").write_text(payload)
    assert any("Invalid speech.json" in e for e in deploy.collect(deployment, {})[0])


def test_canary_requires_separate_interpreter(deployment):
    errors = deploy.collect(deployment, {"VOICE_CANARY_ENABLED": "1"})[0]
    assert any("Canary voice interpreter" in e for e in errors)


def test_saved_voices_and_ct_selection(deployment):
    state = deployment / "robot_supervisor_v2/state"
    state.mkdir()
    voice = {"provider": "soniox", "model": "example", "voice_id": "test", "language": "en"}
    (state / "speech.json").write_text(json.dumps({"active_voice": voice, "voices": [voice, voice]}))
    (state / "environment.json").write_text('{"environment":"CT"}')
    errors, _, info, _, _ = deploy.collect(deployment, {})
    assert not errors
    assert "Saved voice count: 2" in info
    assert "Azure environment: CT" in info


def test_busy_port_never_kills_process(deployment):
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 0))
        listener.listen()
        port = listener.getsockname()[1]
        cfg = deployment / "robot_supervisor_v2/config.yaml"
        data = yaml.safe_load(cfg.read_text())
        data["api"]["port"] = port
        cfg.write_text(yaml.safe_dump(data))
        errors = deploy.collect(deployment, {}, require_free_port=True)[0]
        assert any("API port unavailable" in e for e in errors)
        assert listener.fileno() >= 0


def test_diagnostics_do_not_expose_values(deployment):
    errors, warnings, info, _, _ = deploy.collect(deployment, {})
    rendered = str((errors, warnings, info))
    assert "test-only-azure" not in rendered
    assert "test-only-eleven" not in rendered


@pytest.mark.parametrize("name", [".env", "nested/.env.bak_12", "x/private.pem", "robot_supervisor_v2/state/speech.json", "robot_supervisor_v2/config.yaml", ".deploy-private/config.yaml"])
def test_private_paths_blocked(name):
    assert guard.private_path(name)


@pytest.mark.parametrize("name", [".env.example", ".env.detector.template", "robot_supervisor_v2/config.example.yaml", "scripts/pc2_deploy.py"])
def test_public_templates_allowed(name):
    assert not guard.private_path(name)


@pytest.mark.parametrize("existing", [True, False])
def test_launcher_never_kills_existing_session(tmp_path, existing):
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).exists():
        pytest.skip("Bash not available")
    root = tmp_path / "checkout with spaces"
    root.mkdir()
    shutil.copyfile(SCRIPTS.parent / "run_robot_supervisor_v2.sh", root / "run_robot_supervisor_v2.sh")
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    python.chmod(0o755)
    bins = root / "testbin"
    bins.mkdir()
    tmux = bins / "tmux"
    status = 0 if existing else 1
    tmux.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$1" >> "$AUDIT_CALLS"\n'
        f'if [[ "$1" == has-session ]]; then exit {status}; fi\nexit 0\n',
        encoding="utf-8", newline="\n",
    )
    tmux.chmod(0o755)
    calls = root / "calls.txt"
    env = dict(os.environ, AUDIT_CALLS=str(calls), ROS_SELECTION="")
    # Set POSIX PATH inside Bash; Windows PATH uses a different separator.
    command = 'export PATH="$PWD/testbin:$PATH"; bash ./run_robot_supervisor_v2.sh'
    result = subprocess.run([bash, "-c", command], cwd=root, env=env, capture_output=True, text=True, timeout=10)
    invoked = calls.read_text().splitlines()
    assert "kill-session" not in invoked
    if existing:
        assert result.returncode == 1
        assert "NOT STARTED" in result.stderr
        assert "new-session" not in invoked
    else:
        assert result.returncode == 0, result.stderr
        assert "new-session" in invoked
        assert "Start requested" in result.stdout
