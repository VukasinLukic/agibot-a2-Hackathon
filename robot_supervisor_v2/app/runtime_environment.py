"""Runtime environment selection for Robot Supervisor."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values


VALID_ENVIRONMENTS = ("DEV", "UAT", "PROD", "CT")
DEFAULT_ENVIRONMENT = "DEV"

REQUIRED_AZURE_ENV_KEYS = (
    "AZURE_OPENAI_BASE",
    "AZURE_OPENAI_API_KEY",
    "OPENAI_API_VERSION",
)

# Azure AI Search is no longer on the runtime path (RAG goes through the local
# Qdrant rag_service), so these are passed through when present but optional.
OPTIONAL_AZURE_ENV_KEYS = (
    "AI_SEARCH_ENDPOINT",
    "AI_SEARCH_ADMIN_KEY",
    "INDEX_NAME",
    "CHOSEN_COMPLETION_MODEL",
    "AZURE_OPENAI_DEPLOYMENT",
    "CHOSEN_EMB_MODEL",
    "VECTOR_FIELD",
)

REQUIRED_TRUEBAR_ENV_KEYS = (
    "TRUEBAR_USERNAME",
    "TRUEBAR_PASSWORD",
    "TRUEBAR_CLIENT_ID",
    "TRUEBAR_AUTH_URL",
    "TRUEBAR_API_BASE_URL",
    "TRUEBAR_STT_WS_URL",
    "TRUEBAR_TTS_WS_URL",
)

TRUEBAR_ENV_PREFIX = "TRUEBAR_"
AZURE_ENV_KEYS = REQUIRED_AZURE_ENV_KEYS + OPTIONAL_AZURE_ENV_KEYS
# Truebar STT/TTS was replaced by Soniox/ElevenLabs; TRUEBAR_* stay pass-through only.
REQUIRED_ENV_KEYS = REQUIRED_AZURE_ENV_KEYS
PROCESS_METADATA_KEYS = ("ROBOT_SUPERVISOR_ENVIRONMENT", "ROBOT_ENV_FILE")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENVS_DIR = _REPO_ROOT / ".envs"
_STATE_FILE = _REPO_ROOT / "robot_supervisor_v2" / "state" / "environment.json"
_LEGACY_ENV_FILES = (
    _REPO_ROOT / ".env",
    _REPO_ROOT / "robot_supervisor_v2" / "app" / ".env",
)


class EnvironmentConfigError(ValueError):
    """Raised when selected environment configuration is invalid."""


@dataclass(frozen=True)
class RuntimeEnvironment:
    environment: str
    env_file: Path
    values: dict[str, str]
    missing_required: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.missing_required


def normalize_environment(value: str | None) -> str:
    environment = (value or DEFAULT_ENVIRONMENT).strip().upper()
    if environment not in VALID_ENVIRONMENTS:
        raise EnvironmentConfigError(
            f"Invalid environment '{value}'. Use one of: {', '.join(VALID_ENVIRONMENTS)}"
        )
    return environment


def get_state_file() -> Path:
    return _STATE_FILE


def get_envs_dir() -> Path:
    return _ENVS_DIR


def get_env_file(environment: str) -> Path:
    normalized = normalize_environment(environment)
    return _ENVS_DIR / f"{normalized.lower()}.env"


def read_selected_environment() -> str:
    if not _STATE_FILE.exists():
        return DEFAULT_ENVIRONMENT

    try:
        raw = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise EnvironmentConfigError(f"Failed to read environment state: {exc}") from exc

    return normalize_environment(raw.get("environment"))


def write_selected_environment(environment: str) -> str:
    normalized = normalize_environment(environment)
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps({"environment": normalized}, indent=2) + "\n",
        encoding="utf-8",
    )
    return normalized


def _clean_env_values(values: dict[str, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return _clean_env_values(dict(dotenv_values(path)))


def _load_legacy_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in _LEGACY_ENV_FILES:
        values.update(_load_env_file(path))
    return values


def _is_managed_env_key(key: str) -> bool:
    return key in AZURE_ENV_KEYS or key.startswith(TRUEBAR_ENV_PREFIX)


def load_runtime_environment(environment: str | None = None) -> RuntimeEnvironment:
    selected = normalize_environment(environment or read_selected_environment())
    env_file = get_env_file(selected)

    values: dict[str, str] = {
        key: value
        for key, value in _load_legacy_env_values().items()
        if _is_managed_env_key(key)
    }
    values.update(
        {
            key: os_value
            for key, os_value in os.environ.items()
            if _is_managed_env_key(key) and os_value.strip()
        }
    )
    values.update(_load_env_file(_ENVS_DIR / "common.env"))
    values.update(_load_env_file(env_file))

    missing_required = tuple(
        key for key in REQUIRED_ENV_KEYS if not values.get(key)
    )

    return RuntimeEnvironment(
        environment=selected,
        env_file=env_file,
        values=values,
        missing_required=missing_required,
    )


def validate_runtime_environment(environment: str | None = None) -> RuntimeEnvironment:
    runtime_env = load_runtime_environment(environment)
    if runtime_env.missing_required:
        raise EnvironmentConfigError(
            "Missing required environment variables for "
            f"{runtime_env.environment}: {', '.join(runtime_env.missing_required)}"
        )
    return runtime_env


def build_child_environment_updates(environment: str | None = None) -> dict[str, str]:
    runtime_env = validate_runtime_environment(environment)
    updates = {
        key: value
        for key, value in runtime_env.values.items()
        if _is_managed_env_key(key)
    }
    updates["ROBOT_SUPERVISOR_ENVIRONMENT"] = runtime_env.environment
    updates["ROBOT_ENV_FILE"] = str(runtime_env.env_file)
    return updates


def apply_runtime_environment_to_process(environment: str | None = None) -> RuntimeEnvironment:
    runtime_env = validate_runtime_environment(environment)
    for key, value in build_child_environment_updates(runtime_env.environment).items():
        os.environ[key] = value
    return runtime_env


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def _hostname(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    return parsed.netloc or parsed.path or None


def runtime_environment_status(*, restart_required: bool = False) -> dict[str, Any]:
    runtime_env = load_runtime_environment()
    values = runtime_env.values
    return {
        "environment": runtime_env.environment,
        "available_environments": list(VALID_ENVIRONMENTS),
        "env_file": str(runtime_env.env_file),
        "env_file_exists": runtime_env.env_file.exists(),
        "valid": runtime_env.valid,
        "missing_required": list(runtime_env.missing_required),
        "restart_required": restart_required,
        "azure": {
            "openai_host": _hostname(values.get("AZURE_OPENAI_BASE")),
            "openai_key": _mask_secret(values.get("AZURE_OPENAI_API_KEY")),
            "api_version": values.get("OPENAI_API_VERSION"),
            "completion_model": values.get("CHOSEN_COMPLETION_MODEL"),
            "openai_deployment": values.get("AZURE_OPENAI_DEPLOYMENT"),
            "embedding_model": values.get("CHOSEN_EMB_MODEL"),
            "search_host": _hostname(values.get("AI_SEARCH_ENDPOINT")),
            "search_key": _mask_secret(values.get("AI_SEARCH_ADMIN_KEY")),
            "search_index": values.get("INDEX_NAME"),
            "vector_field": values.get("VECTOR_FIELD"),
        },
        "truebar": {
            "username": values.get("TRUEBAR_USERNAME"),
            "password": _mask_secret(values.get("TRUEBAR_PASSWORD")),
            "client_id": values.get("TRUEBAR_CLIENT_ID"),
            "auth_host": _hostname(values.get("TRUEBAR_AUTH_URL")),
            "api_host": _hostname(values.get("TRUEBAR_API_BASE_URL")),
            "stt_ws_host": _hostname(values.get("TRUEBAR_STT_WS_URL")),
            "tts_ws_host": _hostname(values.get("TRUEBAR_TTS_WS_URL")),
            "asr_tag": values.get("TRUEBAR_ASR_TAG"),
            "tts_tag": values.get("TRUEBAR_TTS_TAG"),
        },
    }
