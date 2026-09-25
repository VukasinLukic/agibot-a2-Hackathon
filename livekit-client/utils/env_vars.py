"""
env_vars.py  -  centralised runtime configuration
Load-time validation ensures all mandatory secrets are present.
Import this file once, early, and keep it read-only elsewhere.
"""

from __future__ import annotations
from utils.logging_utils import global_logger

import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

_repo_root = Path(__file__).resolve().parents[2]
_initial_process_env = os.environ.copy()
_candidate_env_files = [
    _repo_root / ".env",
    _repo_root / "robot_supervisor_v2" / "app" / ".env",
]


def _load_dotenv_preserving_process(path: str | Path, *, override: bool) -> None:
    """Load dotenv values without clobbering env explicitly passed by parent."""
    load_dotenv(path, override=override)
    for _key, _value in _initial_process_env.items():
        os.environ[_key] = _value


_loaded_any_env = False
for _index, _env_file in enumerate(_candidate_env_files):
    if _env_file.exists():
        # app/.env still overrides repo .env, but never overrides process env
        # explicitly supplied by the supervisor.
        _load_dotenv_preserving_process(_env_file, override=_index > 0)
        _loaded_any_env = True

if not _loaded_any_env:
    # Backward-compatible fallback when running outside the repo layout above.
    _found = find_dotenv()
    if _found:
        _load_dotenv_preserving_process(_found, override=False)

_robot_env_file = os.getenv("ROBOT_ENV_FILE")
if _robot_env_file:
    _robot_env_path = Path(_robot_env_file)
    if _robot_env_path.exists():
        # Selected Azure env overlays take precedence over repo/app .env files,
        # while explicit parent-process values remain authoritative.
        _load_dotenv_preserving_process(_robot_env_path, override=True)

_ALLOW_MISSING_ENVS = os.getenv("ALLOW_MISSING_ENVS") == "1"

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def str_to_bool(value: str | bool | None, /, *, default: bool = False) -> bool:
    """Convert typical truthy / falsy strings to bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _require(var_name: str) -> str:
    """Fetch required env vars, fail fast unless skipping is enabled."""
    value = os.getenv(var_name)
    if not value and not _ALLOW_MISSING_ENVS:
        raise RuntimeError(f"Missing required environment variable: {var_name}")
    return value or ""  # return empty string when skipping

def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default

def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        global_logger.warning("Invalid int for %s=%r; using default=%s", name, value, default)
        return default

def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except Exception:
        global_logger.warning("Invalid float for %s=%r; using default=%s", name, value, default)
        return default

def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str_to_bool(value, default=default)

# ---------------------------------------------------------------------------
#  Core runtime
# ---------------------------------------------------------------------------

ENVIRONMENT              = os.getenv("ENVIRONMENT", "CT-dev")            # e.g. prod / test / dev
BOT_NAME                 = os.getenv("BOT_NAME", "Milica")

ALLOW_INTERRUPTIONS       = str_to_bool(os.getenv("ALLOW_INTERRUPTIONS", "True"))
USE_CUSTOM_NOISE_CANCELLING = str_to_bool(os.getenv("USE_CUSTOM_NOISE_CANCELLING", "True"))

# ---------------------------------------------------------------------------
#  Feature Flags / Misc
# ---------------------------------------------------------------------------

DEBUG                    = str_to_bool(os.getenv("DEBUG", "False"))

LLM_CONTEXT_RESULTS      = int(os.getenv("LLM_CONTEXT_RESULTS", "5"))
MAX_HISTORY_ROUNDS       = int(os.getenv("MAX_HISTORY_ROUNDS ", "5"))

AZURE_MANAGED_IDENTITY_CLIENT_ID = os.getenv("AZURE_MANAGED_IDENTITY_CLIENT_ID", "")

# ---------------------------------------------------------------------------
#  Rate Limiting
# ---------------------------------------------------------------------------

MAX_USER_CALLS_DAILY     = int(os.getenv("MAX_USER_CALLS_DAILY", "100"))
MAX_USER_TOKENS_DAILY    = int(os.getenv("MAX_USER_TOKENS_DAILY", "100000"))
MAX_TOTAL_TOKENS_DAILY   = int(os.getenv("MAX_TOTAL_TOKENS_DAILY", "1000000"))

# ---------------------------------------------------------------------------
#  Internal APIs
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  Azure AI Search (legacy, optional: RAG now uses the local Qdrant rag_service)
# ---------------------------------------------------------------------------

AI_SEARCH_ENDPOINT      = os.getenv("AI_SEARCH_ENDPOINT", "")
AI_SEARCH_ADMIN_KEY     = os.getenv("AI_SEARCH_ADMIN_KEY", "")
INDEX_NAME              = os.getenv("INDEX_NAME", "kc-crm-ai-knowledgebase")

# ---------------------------------------------------------------------------
#  Azure OpenAI
# ---------------------------------------------------------------------------

AZURE_OPENAI_BASE       = _require("AZURE_OPENAI_BASE")
AZURE_OPENAI_API_KEY    = _require("AZURE_OPENAI_API_KEY")
OPENAI_API_VERSION      = os.getenv("OPENAI_API_VERSION", "2025-01-01-preview")

CHOSEN_COMPLETION_MODEL = os.getenv("CHOSEN_COMPLETION_MODEL", "gpt-4.1")
if str_to_bool(os.getenv("VOICE_CANARY_ENABLED"), default=False):
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_LLM_DEPLOYMENT", "gpt-5.4-nano")
else:
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", CHOSEN_COMPLETION_MODEL)

TEMPERATURE             = float(os.getenv("TEMPERATURE", "0"))

# ---------------------------------------------------------------------------
#  Vector / Embeddings
# ---------------------------------------------------------------------------

CHOSEN_EMB_MODEL       = os.getenv("CHOSEN_EMB_MODEL", "text-embedding-ada-002")
VECTOR_FIELD           = os.getenv("VECTOR_FIELD", "embedding")

# ---------------------------------------------------------------------------
#  Data Stores
# ---------------------------------------------------------------------------

#AZURE_STORAGE_ACCOUNT         = _require("AZURE_STORAGE_ACCOUNT")
#AZURE_STORAGE_KEY             = _require("AZURE_STORAGE_KEY")
#AZURE_STORAGE_CONTAINER_NAME  = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "document-input")

#SAS_EXPIRY_HOURS              = int(os.getenv("SAS_EXPIRY_HOURS", "8"))

# ---------------------------------------------------------------------------
#  External RAG
# ---------------------------------------------------------------------------

RAG_EXTERNAL_API_URL = env_str(
    "RAG_EXTERNAL_API_URL",
    env_str("RAG_SERVICE_BASE_URL", "http://127.0.0.1:8098"),
).rstrip("/")
RAG_EXTERNAL_SEARCH_PATH = env_str("RAG_EXTERNAL_SEARCH_PATH", "/v1/search")
RAG_EXTERNAL_HEALTH_PATH = env_str("RAG_EXTERNAL_HEALTH_PATH", "/health")
RAG_EXTERNAL_TIMEOUT_SECONDS = max(0.1, env_float("RAG_EXTERNAL_TIMEOUT_SECONDS", 4.0))
RAG_EXTERNAL_TOP_K = max(1, env_int("RAG_EXTERNAL_TOP_K", 5))
RAG_EXTERNAL_INCLUDE_SCORES = env_bool("RAG_EXTERNAL_INCLUDE_SCORES", False)
RAG_EXTERNAL_HEALTHCHECK_ON_INIT = env_bool("RAG_EXTERNAL_HEALTHCHECK_ON_INIT", True)
RAG_EXTERNAL_ENABLE = env_bool("RAG_EXTERNAL_ENABLE", False)
RAG_EXTERNAL_RETRY_SECONDS = max(1, env_int("RAG_EXTERNAL_RETRY_SECONDS", 10))


# ---------------------------------------------------------------------------
#  Sanity log
# ---------------------------------------------------------------------------

global_logger.debug("Configuration loaded for ENVIRONMENT=%s, BOT_NAME=%s", ENVIRONMENT, BOT_NAME)
