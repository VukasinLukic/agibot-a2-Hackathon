"""Read-only A2 deployment checks and explicit launch; no robot SDK imports.

check never starts services or writes config. run is an operator action.
Diagnostics never print secret values. Preserve mentor config/state privately.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit


def enabled(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def placeholder(value: object) -> bool:
    text = str(value or "").strip().lower()
    return not text or "replace-me" in text or text.endswith("_secret") or text == "your-secret-token-here"


def collect(root: Path, inherited: dict[str, str] | None = None, *, require_free_port: bool = False):
    """Return diagnostics and the environment used by the child process."""
    import yaml
    from dotenv import dotenv_values

    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    info = [f"Repository: {root}"]
    env = dict(os.environ if inherited is None else inherited)
    env_path = root / ".env"
    if not env_path.is_file():
        errors.append("Missing private .env; obtain mentor-approved credentials (never commit it).")
    else:
        # Spaces, quotes and $ characters are data, not executable shell commands.
        env.update({k: v for k, v in dotenv_values(env_path, interpolate=False).items() if v is not None})
    if (root / "robot_supervisor_v2/app/.env").exists():
        errors.append("app/.env would override root .env. Reconcile it with the mentor first.")

    config_path = root / "robot_supervisor_v2/config.yaml"
    info.append(f"Config: {config_path}")
    config: dict = {}
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError()
        config = raw
    except (OSError, ValueError, yaml.YAMLError):
        errors.append("Missing/unreadable private config.yaml; Unitree example fallback is NOT allowed here.")
    robot = config.get("robot") or {}
    if not isinstance(robot, dict) or robot.get("platform") != "agibot" or robot.get("model") != "agibot_a2_ultra":
        errors.append("config.yaml must explicitly select agibot / agibot_a2_ultra (PC2).")
    api = config.get("api") or {}
    if not isinstance(api, dict):
        api = {}
    try:
        port = int(api.get("port", 8070))
        if not 1 <= port <= 65535:
            raise ValueError()
        if env.get("ROBOT_SUPERVISOR_PORT") and int(env["ROBOT_SUPERVISOR_PORT"]) != port:
            errors.append("ROBOT_SUPERVISOR_PORT differs from config.yaml api.port.")
    except (TypeError, ValueError):
        errors.append("Invalid API port in config/environment.")
        port = 8070
    info.append(f"API port: {port}")
    services = config.get("services", [])
    if not isinstance(services, list) or any(not isinstance(s, dict) for s in services):
        errors.append("config services must be a list of mappings.")
        services = []
    types = {s.get("type") for s in services if isinstance(s.get("type"), str)}
    for needed in ("livekit-server", "voice-agent", "audio-bridge"):
        if needed not in types:
            errors.append(f"Missing mentor service definition: {needed}.")
    for service in services:
        settings = service.get("config") or {}
        if not isinstance(settings, dict):
            errors.append("Invalid service config mapping.")
            continue
        callback = settings.get("supervisor_url")
        if callback:
            try:
                if urlsplit(str(callback)).port != port:
                    errors.append(f"Service {service.get('type')} supervisor_url port differs from API port.")
            except ValueError:
                errors.append("Invalid service supervisor_url.")
        for key in ("python_bin", "python_path", "working_dir", "script"):
            value = settings.get(key)
            if not isinstance(value, str) or not value:
                continue
            if "/home/unitree/" in value or "HumanoidSupervisor2" in value:
                errors.append(f"Service {service.get('type')} has a legacy Unitree path in {key}.")
            path = Path(value).expanduser()
            if not path.is_absolute():
                base = Path(str(settings.get("working_dir", "."))).expanduser()
                if not base.is_absolute():
                    base = root / base
                path = (root / path) if key == "working_dir" else (base / path)
            if not path.exists():
                warnings.append(f"Service {service.get('type')} {key} is absent here; mentor must resolve it.")
    presets = config.get("command_presets") or []
    if "HumanoidSupervisor2" in json.dumps(presets) or "/home/unitree/" in json.dumps(presets):
        errors.append("command_presets still contain legacy checkout paths; do not use restart/build buttons.")

    required = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SONIOX_API_KEY"]
    if placeholder(env.get("ELEVEN_API_KEY") or env.get("ELEVENLABS_API_KEY")):
        errors.append("Missing/placeholder ElevenLabs API key required by current voice bootstrap.")
    selected = "DEV"
    environment_path = root / "robot_supervisor_v2/state/environment.json"
    if environment_path.exists():
        try:
            selected = json.loads(environment_path.read_text(encoding="utf-8"))["environment"].upper()
            if selected not in {"DEV", "CT", "UAT", "PROD"}:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            errors.append("Invalid environment.json; restore the intended DEV/CT selection.")
            selected = "DEV"
    azure = dict(env)
    for path in (root / ".envs/common.env", root / f".envs/{selected.lower()}.env"):
        if path.is_file():
            azure.update({k: v for k, v in dotenv_values(path, interpolate=False).items() if v})
    for key in ("AZURE_OPENAI_BASE", "AZURE_OPENAI_API_KEY", "OPENAI_API_VERSION"):
        if placeholder(azure.get(key)):
            errors.append(f"Missing/placeholder {key} in selected runtime environment.")
    info.append(f"Azure environment: {selected}")
    if not enabled(env.get("TABLE_TENNIS_ENABLED")):
        errors.append("TABLE_TENNIS_ENABLED must be enabled for the team4 deployment.")
    if env.get("TT_AUTH_MODE") != "token":
        errors.append("TT_AUTH_MODE must be token for LAN access.")
    roles = ("OPERATOR", "VISION", "PERSONA", "ROBOT", "SIM")
    required.extend(f"TT_{role}_TOKEN" for role in roles)
    for key in required:
        if placeholder(env.get(key)):
            errors.append(f"Missing/placeholder {key} (value hidden).")
    if len({env.get(f"TT_{role}_TOKEN") for role in roles}) != len(roles):
        errors.append("TT actor tokens must be distinct.")
    for key in ("TT_API_URL", "TT_SUPERVISOR_URL"):
        try:
            if urlsplit(env.get(key, "")).port != port:
                errors.append(f"{key} must point to the active API port.")
        except ValueError:
            errors.append(f"Invalid {key}.")
    if env.get("RAG_COLLECTION_PREFIX") != "team4_":
        errors.append("RAG_COLLECTION_PREFIX must be team4_ to isolate team data.")

    speech_path = root / "robot_supervisor_v2/state/speech.json"
    info.append(f"Speech state: {speech_path}")
    if speech_path.exists():
        try:
            speech = json.loads(speech_path.read_text(encoding="utf-8"))
            if not isinstance(speech, dict) or not isinstance(speech.get("voices"), list):
                raise ValueError()
            active = speech.get("active_voice")
            if not isinstance(active, dict) or not all(active.get(k) for k in ("provider", "model", "voice_id", "language")):
                raise ValueError()
            info.append(f"Saved voice count: {len(speech['voices'])}")
        except (OSError, ValueError, TypeError):
            errors.append("Invalid speech.json; restore it rather than silently falling back to one voice.")
    else:
        warnings.append("speech.json absent: UI shows only the env-default voice. Restore saved voices if needed.")
    if enabled(env.get("VOICE_CANARY_ENABLED")):
        voice_python = Path(env.get("VOICE_AGENT_PYTHON") or ".venv-voice-canary/bin/python")
        if not voice_python.is_absolute():
            voice_python = root / voice_python
        if not voice_python.is_file() or not os.access(voice_python, os.X_OK):
            errors.append("Canary voice interpreter missing/not executable; do not substitute the Supervisor venv.")
    else:
        warnings.append("VOICE_CANARY_ENABLED is off: runtime uses legacy ElevenLabs TTS, not the modern UI selection.")
    if not (root / "robot_supervisor_v2/dist/index.html").is_file():
        errors.append("Frontend dist/index.html missing. Build on laptop and transfer the release archive.")
    if not (root / "robot_services/audio/audio_bridge.py").is_file():
        errors.append("Tracked audio bridge source is missing.")
    if require_free_port:
        try:
            with socket.socket() as probe:
                probe.bind(("0.0.0.0", port))
        except OSError:
            errors.append("API port unavailable. Identify the existing process; do not kill it automatically.")
    return errors, warnings, info, env, port


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "run"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--require-free-port", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        errors, warnings, info, env, port = collect(root, require_free_port=args.require_free_port or args.action == "run")
    except ImportError:
        print("FAIL: preflight requires PyYAML and python-dotenv in the Supervisor environment.", file=sys.stderr)
        return 1
    for prefix, messages in (("INFO", info), ("WARN", warnings), ("FAIL", errors)):
        for message in messages:
            print(f"{prefix}: {message}")
    if errors:
        return 1
    print("Deployment checks passed; this is NOT a hardware/ARM/speech health confirmation.")
    if args.action == "run":
        os.chdir(root)
        env["PYTHONUNBUFFERED"] = "1"
        sys.stdout.flush()
        # Keep venv entrypoint; resolving its symlink would select system Python.
        os.execve(sys.executable, [sys.executable, str(root / "robot_supervisor_v2/run_api.py"), "--port", str(port)], env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
