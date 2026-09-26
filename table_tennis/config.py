"""Configuration: named options instead of hard-coded tokens/IPs.

Load order: defaults -> YAML file (``--config`` or ``TT_CONFIG``) -> env vars.
Secrets (tokens) should come from env vars, never from a committed file.

Env overrides: TT_MODE, TT_HOST, TT_PORT, TT_DB_PATH, TT_AUTH_MODE,
TT_OPERATOR_TOKEN, TT_VISION_TOKEN, TT_ROBOT_TOKEN, TT_SIM_TOKEN, TT_PERSONA_TOKEN,
TT_FAKE_LOG_PATH, TT_ROBOT_SIM_STEP_S, TT_ADAPTER_DISPLAY, TT_ADAPTER_GESTURE,
TT_ADAPTER_SPEECH, TT_ADAPTER_NAVIGATOR.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAR_DIR = Path(__file__).resolve().parent / "var"


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_Cfg):
    host: str = "127.0.0.1"
    port: int = Field(default=8099, ge=1, le=65535)
    sse_heartbeat_s: float = Field(default=15.0, gt=0)


class StorageSettings(_Cfg):
    db_path: str = str(DEFAULT_VAR_DIR / "table_tennis_mock.sqlite")


class FeatureSettings(_Cfg):
    # Stays False in the bootstrap. Turning it on is a startup error until
    # person 2 adds the benchmark-gated automatic mode.
    automatic_scoring: bool = False


class Tokens(_Cfg):
    operator: Optional[str] = "operator_secret"
    vision: Optional[str] = "vision_secret"
    robot: Optional[str] = "robot_secret"
    sim: Optional[str] = "sim_secret"
    # Voice agent (livekit-client/referee_mode.py). Read-only: no command,
    # match creation or robot call lists "persona" as an allowed actor.
    persona: Optional[str] = "persona_secret"


class AuthSettings(_Cfg):
    # local: actor from X-TT-Actor header (default operator); loopback only.
    # token: Authorization: Bearer <token> per actor; required for network use.
    mode: Literal["local", "token"] = "token"
    tokens: Tokens = Field(default_factory=Tokens)


class RobotSettings(_Cfg):
    # Allowlist: table_id -> named waypoints. The UI can only pick from these.
    tables: dict[str, list[str]] = Field(default_factory=lambda: {"table-1": ["referee-spot"]})
    sim_step_s: float = Field(default=0.7, gt=0)
    # Mock only: calls to these waypoints end in "failed" (to test the UI).
    fail_waypoints: list[str] = Field(default_factory=lambda: ["broken-spot"])


class AdapterSettings(_Cfg):
    """Which implementation backs each output port.

    fake: in-process simulation recorded in the fake log (default).
    a2 / livekit: the real adapter classes. In mode=mock they always run as
    dry-run (they log what they would send and touch no hardware). In
    mode=real they need a real transport, which does not exist yet, so startup
    fails with a clear message instead of silently falling back to fake.
    """

    display: Literal["fake", "a2"] = "fake"
    gesture: Literal["fake", "a2"] = "fake"
    speech: Literal["fake", "livekit"] = "fake"
    navigator: Literal["fake", "a2"] = "fake"


class OutputSettings(_Cfg):
    fake_log_path: Optional[str] = str(DEFAULT_VAR_DIR / "fake_outputs.log")
    speech_ttl_s: float = Field(default=20.0, gt=0)


class Settings(_Cfg):
    mode: Literal["mock", "real"] = "mock"
    server: ServerSettings = Field(default_factory=ServerSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    robot: RobotSettings = Field(default_factory=RobotSettings)
    outputs: OutputSettings = Field(default_factory=OutputSettings)
    adapters: AdapterSettings = Field(default_factory=AdapterSettings)

    @model_validator(mode="after")
    def _checks(self) -> "Settings":
        if self.features.automatic_scoring:
            raise ValueError(
                "features.automatic_scoring=true is not supported by the bootstrap; "
                "it is enabled only after the vision benchmark (02_BACKEND.md phase 5)"
            )
        if self.auth.mode == "token" and not self.auth.tokens.operator:
            raise ValueError("auth.mode=token requires at least an operator token (TT_OPERATOR_TOKEN)")
        configured = [t for t in self.auth.tokens.model_dump().values() if t]
        if len(configured) != len(set(configured)):
            raise ValueError("auth tokens must be distinct: one token maps to exactly one actor")
        return self

    @property
    def simulated(self) -> bool:
        return self.mode == "mock"


def is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind_allowed(settings: Settings) -> None:
    """Refuse a non-loopback bind unless token auth is configured."""
    if not is_loopback(settings.server.host) and settings.auth.mode != "token":
        raise ValueError(
            f"refusing to listen on {settings.server.host}: without auth.mode=token any client on the "
            "network could send operator commands. Use 127.0.0.1 or configure tokens."
        )


def _env_overrides(data: dict, env: Mapping[str, str]) -> dict:
    def put(path: list[str], value):
        cur = data
        for key in path[:-1]:
            cur = cur.setdefault(key, {})
        cur[path[-1]] = value

    mapping = {
        "TT_MODE": ["mode"],
        "TT_HOST": ["server", "host"],
        "TT_PORT": ["server", "port"],
        "TT_DB_PATH": ["storage", "db_path"],
        "TT_AUTH_MODE": ["auth", "mode"],
        "TT_OPERATOR_TOKEN": ["auth", "tokens", "operator"],
        "TT_VISION_TOKEN": ["auth", "tokens", "vision"],
        "TT_ROBOT_TOKEN": ["auth", "tokens", "robot"],
        "TT_SIM_TOKEN": ["auth", "tokens", "sim"],
        "TT_PERSONA_TOKEN": ["auth", "tokens", "persona"],
        "TT_FAKE_LOG_PATH": ["outputs", "fake_log_path"],
        "TT_ROBOT_SIM_STEP_S": ["robot", "sim_step_s"],
        "TT_ADAPTER_DISPLAY": ["adapters", "display"],
        "TT_ADAPTER_GESTURE": ["adapters", "gesture"],
        "TT_ADAPTER_SPEECH": ["adapters", "speech"],
        "TT_ADAPTER_NAVIGATOR": ["adapters", "navigator"],
    }
    for var, path in mapping.items():
        if env.get(var):
            put(path, env[var])
    return data


def load_settings(path: Optional[str | Path] = None, env: Optional[Mapping[str, str]] = None, **overrides) -> Settings:
    env = os.environ if env is None else env
    data: dict = {}
    path = path or env.get("TT_CONFIG")
    if path:
        import yaml  # only needed when a file is used

        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{path}: expected a mapping")
        data = loaded
    data = _env_overrides(data, env)
    for key, value in overrides.items():
        if value is None:
            continue
        section, _, field = key.partition("__")
        if field:
            data.setdefault(section, {})[field] = value
        else:
            data[section] = value
    return Settings.model_validate(data)
