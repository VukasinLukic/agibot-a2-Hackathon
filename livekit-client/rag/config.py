from dataclasses import dataclass
from utils.env_vars import (
    RAG_EXTERNAL_API_URL,
    RAG_EXTERNAL_SEARCH_PATH,
    RAG_EXTERNAL_HEALTH_PATH,
    RAG_EXTERNAL_TIMEOUT_SECONDS,
    RAG_EXTERNAL_TOP_K,
    RAG_EXTERNAL_INCLUDE_SCORES,
    RAG_EXTERNAL_HEALTHCHECK_ON_INIT,
    RAG_EXTERNAL_ENABLE,
    RAG_EXTERNAL_RETRY_SECONDS,
)

@dataclass(frozen=True)
class RagConfig:
    api_url: str = RAG_EXTERNAL_API_URL
    search_path: str = RAG_EXTERNAL_SEARCH_PATH
    health_path: str = RAG_EXTERNAL_HEALTH_PATH
    timeout_s: float = RAG_EXTERNAL_TIMEOUT_SECONDS
    top_k: int = RAG_EXTERNAL_TOP_K
    include_scores: bool = RAG_EXTERNAL_INCLUDE_SCORES
    healthcheck_on_init: bool = RAG_EXTERNAL_HEALTHCHECK_ON_INIT
    enabled: bool = RAG_EXTERNAL_ENABLE
    retry_seconds: int = RAG_EXTERNAL_RETRY_SECONDS
