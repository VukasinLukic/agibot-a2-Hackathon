#!/usr/bin/env python3
"""PC3 manager API for the Humanoid LiveKit audio bridge.

The manager is control-plane only: it advertises PC3 audio devices and owns the
lifecycle of an actual ``audio_bridge.py`` child process. It does not connect to
LiveKit or open audio devices itself.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from robot_services.agibot.aima_em import AgibotAimaConfig, AgibotAimaManager


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRIDGE_SCRIPT = "robot_services/audio/audio_bridge.py"
DEFAULT_CHILD_LOG = "robot_supervisor_v2/logs/audio_bridge_pc3_child.log"
CHILD_LOG_TAIL_BYTES = 4096


class ChildStartupError(RuntimeError):
    """Raised when the manager-owned bridge child fails to become ready."""


class ManagerConfig(BaseModel):
    """Long-lived PC3 manager configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = 8766
    manager_token: str | None = None
    python_executable: str = sys.executable
    working_dir: str = str(REPO_ROOT)
    bridge_script: str = DEFAULT_BRIDGE_SCRIPT
    bridge_name: str = "audio-streamer"
    mic_track_name: str = "g1-mic"
    child_control_host: str = "127.0.0.1"
    child_control_port: int = 8767
    child_startup_timeout_seconds: float = 10.0
    child_request_timeout_seconds: float = 2.0
    child_stop_timeout_seconds: float = 5.0
    device_list_timeout_seconds: float = 5.0
    child_log_path: str = DEFAULT_CHILD_LOG
    livekit_url: str | None = None
    livekit_room: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    agibot_aima: AgibotAimaConfig = Field(default_factory=AgibotAimaConfig)

    def sanitized(self) -> dict[str, Any]:
        data = self.model_dump()
        for key in ("manager_token", "livekit_api_key", "livekit_api_secret"):
            if data.get(key):
                data[key] = "<redacted>"
        return data


class BridgeStartRequest(BaseModel):
    """Per-run bridge options. These values are not persisted by the manager."""

    input_device: str | int | None = None
    output_devices: list[str | int] = Field(default_factory=list)
    input_capture_channels: int | None = None
    input_mix_channels: list[int] = Field(default_factory=list)
    enable_aec: bool = True
    enable_rnnoise: bool = False

    @field_validator("input_capture_channels")
    @classmethod
    def _validate_input_capture_channels(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("input_capture_channels must be at least 1")
        return value

    @field_validator("input_mix_channels")
    @classmethod
    def _validate_input_mix_channels(cls, value: list[int]) -> list[int]:
        if any(channel < 0 for channel in value):
            raise ValueError("input_mix_channels cannot contain negative channel indexes")
        return value

    @model_validator(mode="after")
    def _validate_mix_with_capture_channels(self) -> "BridgeStartRequest":
        capture_channels = self.input_capture_channels or 1
        invalid_channels = [
            channel for channel in self.input_mix_channels
            if channel >= capture_channels
        ]
        if invalid_channels:
            raise ValueError(
                "input_mix_channels must be lower than input_capture_channels"
            )
        return self


class InputGainRequest(BaseModel):
    input_mic_gain_db: float


class OutputGainRequest(BaseModel):
    output_speaker_gain_db: float


class MuteRequest(BaseModel):
    muted: bool


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def load_manager_config(config_path: str | Path | None = None) -> ManagerConfig:
    """Load manager config from YAML, then apply PC3 environment overrides."""
    raw: dict[str, Any] = {}
    if config_path:
        path = Path(config_path)
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"Manager config must be a mapping: {path}")
            raw.update(loaded)

    env_overrides = {
        "manager_token": _env_value("AUDIO_BRIDGE_MANAGER_TOKEN"),
        "livekit_url": _env_value("LIVEKIT_URL"),
        "livekit_room": _env_value("LIVEKIT_ROOM"),
        "livekit_api_key": _env_value("LIVEKIT_API_KEY"),
        "livekit_api_secret": _env_value("LIVEKIT_API_SECRET"),
        "bridge_name": _env_value("AUDIO_BRIDGE_IDENTITY"),
        "mic_track_name": _env_value("LIVEKIT_TRACK_NAME"),
    }
    raw.update({key: value for key, value in env_overrides.items() if value is not None})
    return ManagerConfig(**raw)


class AudioBridgeManager:
    """Owns the actual audio bridge child process on PC3."""

    def __init__(self, config: ManagerConfig):
        self.config = config
        self.process: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.last_error: str | None = None
        self.last_start_request: BridgeStartRequest | None = None
        self._log_handle = None
        self._log_path: Path | None = None
        self.aima = AgibotAimaManager(config.agibot_aima)
        try:
            self.aima.reconcile_on_startup()
        except RuntimeError as exc:
            self.last_error = str(exc)

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def child_control_url(self) -> str:
        return f"http://{self.config.child_control_host}:{self.config.child_control_port}"

    def _script_path(self) -> str:
        script = Path(self.config.bridge_script)
        if script.is_absolute():
            return str(script)
        return str(Path(self.config.working_dir) / script)

    def _bridge_env(self) -> dict[str, str]:
        env = os.environ.copy()
        mapping = {
            "LIVEKIT_URL": self.config.livekit_url,
            "LIVEKIT_ROOM": self.config.livekit_room,
            "LIVEKIT_API_KEY": self.config.livekit_api_key,
            "LIVEKIT_API_SECRET": self.config.livekit_api_secret,
            "AUDIO_BRIDGE_IDENTITY": self.config.bridge_name,
            "LIVEKIT_TRACK_NAME": self.config.mic_track_name,
        }
        for key, value in mapping.items():
            if value:
                env[key] = value
        return env

    def _build_child_command(self, request: BridgeStartRequest) -> list[str]:
        cmd = [
            self.config.python_executable,
            self._script_path(),
            "--name",
            self.config.bridge_name,
            "--control-port",
            str(self.config.child_control_port),
            "--mic-track-name",
            self.config.mic_track_name,
        ]
        if request.input_device is not None:
            cmd.extend(["--input-device", str(request.input_device)])
        if request.input_capture_channels is not None:
            cmd.extend(["--input-capture-channels", str(request.input_capture_channels)])
        for input_mix_channel in request.input_mix_channels:
            cmd.extend(["--input-mix-channel", str(input_mix_channel)])
        for output_device in request.output_devices:
            cmd.extend(["--output-device", str(output_device)])
        if not request.enable_aec:
            cmd.append("--disable-aec")
        if request.enable_rnnoise:
            cmd.append("--enable-rnnoise")
        return cmd

    def _child_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.child_control_url()}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(
            request,
            timeout=self.config.child_request_timeout_seconds,
        ) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}

    def _resolve_child_log_path(self) -> Path:
        log_path = Path(self.config.child_log_path)
        if not log_path.is_absolute():
            log_path = Path(self.config.working_dir) / log_path
        return log_path

    def _tail_child_log(self) -> str:
        log_path = self._log_path or self._resolve_child_log_path()
        if self._log_handle is not None:
            try:
                self._log_handle.flush()
            except Exception:
                pass
        try:
            with log_path.open("rb") as log_file:
                log_file.seek(0, os.SEEK_END)
                size = log_file.tell()
                log_file.seek(max(0, size - CHILD_LOG_TAIL_BYTES), os.SEEK_SET)
                raw_tail = log_file.read()
        except OSError:
            return ""

        tail = raw_tail.decode("utf-8", errors="replace").strip()
        return self._redact_log_content(tail)

    def _redact_log_content(self, content: str) -> str:
        for secret in (
            self.config.manager_token,
            self.config.livekit_api_key,
            self.config.livekit_api_secret,
        ):
            if secret:
                content = content.replace(secret, "<redacted>")
        return content

    def tail_logs(self, lines: int = 100) -> dict[str, Any]:
        lines = max(1, min(int(lines), 2000))
        log_path = self._resolve_child_log_path()
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as log_file:
                content = "".join(log_file.readlines()[-lines:])
        except FileNotFoundError:
            content = ""
        except OSError as exc:
            raise RuntimeError(f"Failed to read child log: {exc}") from exc

        return {
            "log_path": str(log_path),
            "lines": lines,
            "content": self._redact_log_content(content),
        }

    def _child_startup_failure(self, message: str) -> ChildStartupError:
        log_tail = self._tail_child_log()
        if log_tail:
            return ChildStartupError(f"{message}. Recent child log: {log_tail}")
        return ChildStartupError(message)

    @staticmethod
    def _child_is_ready(status_payload: dict[str, Any]) -> bool:
        return status_payload.get("running") is True and status_payload.get("ready") is True

    def _wait_for_child_running(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.child_startup_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise self._child_startup_failure(
                    f"Audio bridge exited before becoming ready with code {self.process.returncode}"
                )
            try:
                status_payload = self._child_request("GET", "/status")
                if self._child_is_ready(status_payload):
                    return status_payload
            except Exception as exc:
                last_error = exc
            time.sleep(0.2)

        if last_error:
            raise self._child_startup_failure(
                f"Timed out waiting for audio bridge child to become ready: {last_error}"
            )
        raise self._child_startup_failure("Timed out waiting for audio bridge child to become ready")

    def list_devices(self) -> dict[str, Any]:
        result = subprocess.run(
            [self.config.python_executable, self._script_path(), "--list-devices"],
            capture_output=True,
            text=True,
            timeout=self.config.device_list_timeout_seconds,
            cwd=self.config.working_dir,
            env=self._bridge_env(),
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"Audio device listing failed: {detail}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse audio device JSON: {exc}") from exc

    def start(self, request: BridgeStartRequest | None = None) -> dict[str, Any]:
        if self.is_running():
            raise RuntimeError("Audio bridge child is already running")
        self.aima.ensure_bridge_start_allowed()

        request = request or BridgeStartRequest()
        cmd = self._build_child_command(request)
        log_path = self._resolve_child_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        self.last_error = None
        self._log_path = log_path
        self._log_handle = log_path.open("a", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=self.config.working_dir,
                env=self._bridge_env(),
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.started_at = time.monotonic()
            self.last_start_request = request
            return self._wait_for_child_running()
        except Exception as exc:
            self.last_error = str(exc)
            self.stop()
            raise

    def stop(self) -> dict[str, Any]:
        process = self.process
        if process is None:
            self._close_log()
            self.started_at = None
            return self.status()

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self.config.child_stop_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.config.child_stop_timeout_seconds)

        self.process = None
        self.started_at = None
        self._close_log()
        return self.status()

    def restart(self, request: BridgeStartRequest | None = None) -> dict[str, Any]:
        self.stop()
        return self.start(request)

    def proxy_mute(self, muted: bool) -> dict[str, Any]:
        self._require_child_running()
        return self._child_request("POST", "/mute", {"muted": muted})

    def proxy_input_gain(self, gain_db: float) -> dict[str, Any]:
        self._require_child_running()
        return self._child_request("POST", "/input-gain", {"input_mic_gain_db": gain_db})

    def proxy_output_gain(self, gain_db: float) -> dict[str, Any]:
        self._require_child_running()
        return self._child_request("POST", "/output-gain", {"output_speaker_gain_db": gain_db})

    def proxy_remote_playback_release(self) -> dict[str, Any]:
        self._require_child_running()
        return self._child_request("POST", "/remote-playback/release", {})

    def status(self) -> dict[str, Any]:
        running = self.is_running()
        uptime = time.monotonic() - self.started_at if running and self.started_at else None
        child_status = None
        if running:
            try:
                child_status = self._child_request("GET", "/status")
            except Exception as exc:
                child_status = {"control_available": False, "error": str(exc)}

        return {
            "manager": {
                "running": True,
                "control_url": f"http://{self.config.host}:{self.config.port}",
            },
            "child": {
                "running": running,
                "pid": self.process.pid if running and self.process else None,
                "uptime_seconds": uptime,
                "last_error": self.last_error,
                "control_url": self.child_control_url(),
                "status": child_status,
            },
            "config": self.config.sanitized(),
            "aima": self.aima.status(),
        }

    def _require_child_running(self) -> None:
        if not self.is_running():
            raise RuntimeError("Audio bridge child is not running")

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def create_app(manager: AudioBridgeManager) -> FastAPI:
    app = FastAPI(title="Humanoid Audio Bridge Manager")

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        token = manager.config.manager_token
        if not token:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Audio bridge manager token is not configured",
            )
        expected = f"Bearer {token}"
        if not secrets.compare_digest(authorization or "", expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid audio bridge manager token",
            )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "audio-bridge-manager"}

    @app.get("/status", dependencies=[Depends(require_auth)])
    def get_status() -> dict[str, Any]:
        return manager.status()

    @app.get("/devices", dependencies=[Depends(require_auth)])
    def get_devices() -> dict[str, Any]:
        try:
            return manager.list_devices()
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Audio device listing timed out") from exc

    @app.get("/config", dependencies=[Depends(require_auth)])
    def get_config() -> dict[str, Any]:
        return manager.config.sanitized()

    @app.get("/aima/status", dependencies=[Depends(require_auth)])
    def get_aima_status() -> dict[str, Any]:
        return manager.aima.status()

    @app.post("/aima/mode/audio-bridge", dependencies=[Depends(require_auth)])
    def set_aima_audio_bridge_mode() -> dict[str, Any]:
        try:
            return manager.aima.switch_audio_bridge()
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/aima/mode/agibot", dependencies=[Depends(require_auth)])
    def set_aima_agibot_mode() -> dict[str, Any]:
        if manager.is_running():
            raise HTTPException(
                status_code=409,
                detail="Cannot switch AIMA to agibot mode while audio bridge child is running",
            )
        try:
            return manager.aima.switch_agibot()
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/aima/doctor", dependencies=[Depends(require_auth)])
    def run_aima_doctor() -> dict[str, Any]:
        try:
            return manager.aima.doctor()
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/logs", dependencies=[Depends(require_auth)])
    def get_logs(lines: int = Query(default=100, ge=1, le=2000)) -> dict[str, Any]:
        try:
            return manager.tail_logs(lines)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/start", dependencies=[Depends(require_auth)])
    def start_bridge(request: BridgeStartRequest | None = Body(default=None)) -> dict[str, Any]:
        try:
            child_status = manager.start(request)
            status_payload = manager.status()
            status_payload["child"]["status"] = child_status
            return status_payload
        except ChildStartupError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/stop", dependencies=[Depends(require_auth)])
    def stop_bridge() -> dict[str, Any]:
        return manager.stop()

    @app.post("/restart", dependencies=[Depends(require_auth)])
    def restart_bridge(request: BridgeStartRequest | None = Body(default=None)) -> dict[str, Any]:
        try:
            child_status = manager.restart(request)
            status_payload = manager.status()
            status_payload["child"]["status"] = child_status
            return status_payload
        except ChildStartupError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/mute", dependencies=[Depends(require_auth)])
    def mute_bridge(request: MuteRequest) -> dict[str, Any]:
        try:
            return manager.proxy_mute(request.muted)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/input-gain", dependencies=[Depends(require_auth)])
    def set_input_gain(request: InputGainRequest) -> dict[str, Any]:
        try:
            return manager.proxy_input_gain(request.input_mic_gain_db)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/output-gain", dependencies=[Depends(require_auth)])
    def set_output_gain(request: OutputGainRequest) -> dict[str, Any]:
        try:
            return manager.proxy_output_gain(request.output_speaker_gain_db)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/remote-playback/release", dependencies=[Depends(require_auth)])
    def release_remote_playback() -> dict[str, Any]:
        try:
            return manager.proxy_remote_playback_release()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


def _default_manager() -> AudioBridgeManager:
    config_path = os.getenv("AUDIO_BRIDGE_MANAGER_CONFIG")
    return AudioBridgeManager(load_manager_config(config_path))


app = create_app(_default_manager())


def main() -> None:
    parser = argparse.ArgumentParser(description="PC3 manager API for Humanoid LiveKit audio bridge")
    parser.add_argument("--config", default=os.getenv("AUDIO_BRIDGE_MANAGER_CONFIG"))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    config = load_manager_config(args.config)
    if args.host is not None:
        config.host = args.host
    if args.port is not None:
        config.port = args.port
    if not config.manager_token:
        raise SystemExit("AUDIO_BRIDGE_MANAGER_TOKEN or manager_token config is required")

    manager = AudioBridgeManager(config)
    uvicorn.run(create_app(manager), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
