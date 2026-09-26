#!/usr/bin/env python3
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Set

import fitz
import torch
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient, models
from pydantic import BaseModel, Field, model_validator
from dotenv import load_dotenv
from livekit_config.voice_latency import get_voice_latency_tracer


_repo_root = Path(__file__).resolve().parent.parent
load_dotenv(_repo_root / ".env", override=True)
load_dotenv(_repo_root / "robot_supervisor_v2" / "app" / ".env", override=True)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("rag-service")
latency_tracer = get_voice_latency_tracer("rag-service")

MAIN_INDEX_SLUG = "main"
# Separator plus the "Index | filename | page | chunk | doc_id" line that
# precedes every chunk in the assembled context.
_CONTEXT_METADATA_HEADROOM_CHARS = 400


def _effective_context_max_chars(configured: int, chunk_chars: int) -> int:
    """Never let the context budget fall below a single chunk.

    Assembly clips the last chunk that does not fit, so a budget smaller than
    one chunk truncates even the best-ranked hit mid-chunk and silently drops
    its tail -- for example the panelist list that follows a session title.
    """

    return max(configured, chunk_chars + _CONTEXT_METADATA_HEADROOM_CHARS)

_SEARCH_TRANSLATION = str.maketrans(
    {
        "č": "c",
        "ć": "c",
        "đ": "dj",
        "š": "s",
        "ž": "z",
        "Č": "c",
        "Ć": "c",
        "Đ": "dj",
        "Š": "s",
        "Ž": "z",
    }
)

_PERSON_NAME_RE = re.compile(
    r"\b([A-ZČĆŽŠĐ][a-zčćžšđ]{2,})\s+"
    r"([A-ZČĆŽŠĐ][a-zčćžšđ]{2,})\b"
)


def _person_name_candidates(text: str) -> Set[str]:
    return {
        f"{match.group(1)} {match.group(2)}"
        for match in _PERSON_NAME_RE.finditer(text)
        if not _is_non_name_token(match.group(1))
        and not _is_non_name_token(match.group(2))
    }


def _normalized_name_part(value: str) -> str:
    return value.translate(_SEARCH_TRANSLATION).casefold()


def _name_part_variants(value: str) -> Set[str]:
    """Return conservative variants for Serbian inflected names."""

    normalized = _normalized_name_part(value)
    variants = {normalized}
    for suffix in (
        "ijima",
        "iju",
        "jev",
        "ov",
        "im",
        "ih",
        "ovima",
        "evima",
        "ima",
        "ama",
        "evog",
        "ovog",
        "evom",
        "ovom",
        "og",
        "eg",
        "om",
        "em",
        "ju",
        "u",
        "a",
        "e",
        "i",
    ):
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 3:
            variants.add(normalized[: -len(suffix)])
    return variants


def _name_part_similarity(left: str, right: str) -> float:
    left_variants = _name_part_variants(left)
    right_variants = _name_part_variants(right)
    if left_variants & right_variants:
        return 1.0
    return max(
        (
            SequenceMatcher(None, left_variant, right_variant).ratio()
            for left_variant in left_variants
            for right_variant in right_variants
            if _name_initials_compatible(left_variant, right_variant)
        ),
        default=0.0,
    )


def _name_parts_match(left: str, right: str, *, threshold: float = 1.0) -> bool:
    return _name_part_similarity(left, right) >= threshold


def _name_initials_compatible(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_initial = left[:1]
    right_initial = right[:1]
    if left_initial == right_initial:
        return True
    return {left_initial, right_initial} in ({"j", "y"}, {"v", "w"})


def _name_part_loose_similarity(left: str, right: str) -> float:
    left_variants = _name_part_variants(left)
    right_variants = _name_part_variants(right)
    if left_variants & right_variants:
        return 1.0
    return max(
        (
            SequenceMatcher(None, left_variant, right_variant).ratio()
            for left_variant in left_variants
            for right_variant in right_variants
        ),
        default=0.0,
    )


def _query_name_pairs(text: str) -> List[tuple[str, str, str]]:
    """Return likely two-token person-name mentions, including lowercase STT."""

    candidates: List[tuple[str, str, str]] = []
    seen: Set[str] = set()
    for candidate in _person_name_candidates(text):
        first, last = candidate.split(" ", 1)
        key = _normalize_search_text(candidate)
        if key and key not in seen:
            candidates.append((candidate, first, last))
            seen.add(key)

    tokens = _search_tokens(text)
    for first, last in zip(tokens, tokens[1:]):
        if len(first) < 3 or len(last) < 3:
            continue
        if _is_non_name_token(first) or _is_non_name_token(last):
            continue
        heard = f"{first} {last}"
        key = _normalize_search_text(heard)
        if key and key not in seen:
            candidates.append((heard, first, last))
            seen.add(key)
    return candidates


def _fuzzy_person_alias(
    query: str,
    canonical_names: Set[str],
    *,
    surname_threshold: float = 0.68,
    ambiguity_margin: float = 0.10,
) -> Optional[tuple[str, str, float]]:
    """Match an exact first name plus a conservatively fuzzy surname."""

    query_names = _query_name_pairs(query)
    best_match: Optional[tuple[str, str, float]] = None
    for query_name, query_first, query_last in query_names:
        scored: List[tuple[float, str]] = []
        normalized_query_last = _normalized_name_part(query_last)
        query_last_variants = _name_part_variants(query_last)
        has_exact_normalized_match = False
        for canonical in canonical_names:
            canonical_first, canonical_last = canonical.split(" ", 1)
            first_score = _name_part_similarity(query_first, canonical_first)
            loose_first_score = first_score or _name_part_loose_similarity(
                query_first,
                canonical_first,
            )
            normalized_canonical_last = _normalized_name_part(canonical_last)
            canonical_last_variants = _name_part_variants(canonical_last)
            if normalized_query_last == normalized_canonical_last:
                has_exact_normalized_match = True
                break
            if query_last_variants & canonical_last_variants:
                if _normalize_search_text(query_name) != _normalize_search_text(canonical):
                    if best_match is None or 1.0 > best_match[2]:
                        best_match = (query_name, canonical, 1.0)
                has_exact_normalized_match = True
                break
            score = SequenceMatcher(
                None,
                normalized_query_last,
                normalized_canonical_last,
            ).ratio()
            if first_score >= 0.86 and score >= surname_threshold:
                scored.append(((first_score * 0.35) + (score * 0.65), canonical))
            elif score >= 0.74 and loose_first_score >= 0.68:
                scored.append(((loose_first_score * 0.25) + (score * 0.75), canonical))
        if has_exact_normalized_match:
            continue
        if not scored:
            continue
        scored.sort(reverse=True)
        score, canonical = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if score - runner_up < ambiguity_margin:
            continue
        if best_match is None or score > best_match[2]:
            best_match = (query_name, canonical, score)
    return best_match
_SEARCH_STOPWORDS = {
    "a",
    "ali",
    "da",
    "je",
    "i",
    "ili",
    "ko",
    "koja",
    "koje",
    "koji",
    "mi",
    "na",
    "ne",
    "o",
    "od",
    "on",
    "ona",
    "ono",
    "reci",
    "sta",
    "sto",
    "su",
    "to",
    "u",
    "za",
    "zna",
    "znas",
}

# Agenda/schedule vocabulary and ordinals. Capitalised headings such as
# "Tema Panela 1" otherwise look like a first name plus a surname, which makes
# them land in the canonical person index and lets a question like
# "prvi panel" fuzzy-match a bogus person.
_NON_NAME_TOKENS = {
    "agenda",
    "agendu",
    "cetvrti",
    "drugi",
    "drugog",
    "drugom",
    "first",
    "govornici",
    "govornik",
    "moderator",
    "moderatora",
    "moderatorka",
    "panel",
    "panela",
    "panele",
    "paneli",
    "panelist",
    "panelista",
    "paneliste",
    "panelisti",
    "panelists",
    "pauza",
    "pauze",
    "prvi",
    "prvog",
    "prvom",
    "raspored",
    "rasporeda",
    "second",
    "sesija",
    "sesije",
    "session",
    "speaker",
    "speakers",
    "tema",
    "teme",
    "temu",
    "third",
    "topic",
    "treci",
}


def _is_non_name_token(value: str) -> bool:
    return _normalized_name_part(value) in _NON_NAME_TOKENS


_ENTITY_MATCH_THRESHOLD = 0.82


def _entity_term_score(query: str, term: str) -> float:
    normalized_query = _normalize_search_text(query)
    normalized_term = _normalize_search_text(term)
    if not normalized_query or not normalized_term:
        return 0.0
    query_tokens = _search_tokens(query)
    term_tokens = _search_tokens(term)
    if not query_tokens or not term_tokens:
        return 0.0
    if normalized_term in normalized_query:
        if len(term_tokens) == 1 and normalized_term not in query_tokens:
            return 0.0
        return 1.0
    if len(term_tokens) == 1:
        if len(normalized_term) <= 4:
            return 0.0
        return max((_token_similarity(term_tokens[0], token) for token in query_tokens), default=0.0)
    if len(query_tokens) < len(term_tokens):
        return SequenceMatcher(None, normalized_query, normalized_term).ratio()
    scores: List[float] = []
    window = len(term_tokens)
    for index in range(0, len(query_tokens) - window + 1):
        candidate = " ".join(query_tokens[index : index + window])
        scores.append(SequenceMatcher(None, candidate, normalized_term).ratio())
    return max(scores or [0.0])


def _load_entity_dictionary(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed loading RAG entity dictionary %s: %s", path, exc)
        return []
    entries = raw.get("entities", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        logger.warning("Ignoring RAG entity dictionary %s: root is not a list", path)
        return []

    normalized: List[Dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical") or "").strip()
        if not canonical:
            continue
        aliases = [
            str(alias).strip()
            for alias in item.get("aliases", [])
            if str(alias).strip()
        ]
        related_terms = [
            str(term).strip()
            for term in item.get("related_terms", [])
            if str(term).strip()
        ]
        normalized.append(
            {
                "canonical": canonical,
                "type": str(item.get("type") or "").strip(),
                "aliases": aliases,
                "related_terms": related_terms,
            }
        )
    return normalized


def _resolve_query_entities(
    query: str,
    entity_dictionary: List[Dict[str, Any]],
    *,
    threshold: float = _ENTITY_MATCH_THRESHOLD,
    max_matches: int = 3,
) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for entity in entity_dictionary:
        terms = [entity["canonical"], *entity.get("aliases", [])]
        best_score = 0.0
        best_term = ""
        for term in terms:
            score = _entity_term_score(query, term)
            if score > best_score:
                best_score = score
                best_term = term
        if best_score >= threshold:
            scored.append({**entity, "matched_term": best_term, "score": best_score})

    scored.sort(key=lambda item: (float(item["score"]), len(str(item["matched_term"]))), reverse=True)
    exact_people = [
        item
        for item in scored
        if item.get("type") == "person" and float(item.get("score") or 0.0) >= 1.0
    ]
    if exact_people:
        return exact_people[:max_matches]
    return scored[:max_matches]


def _augment_query_with_entities(
    query: str,
    entity_dictionary: List[Dict[str, Any]],
) -> tuple[str, List[Dict[str, Any]]]:
    matches = _resolve_query_entities(query, entity_dictionary)
    if not matches:
        return query, []

    additions: List[str] = []
    seen: Set[str] = set()
    for match in matches:
        for term in [
            match.get("canonical"),
            *match.get("aliases", []),
            *match.get("related_terms", []),
        ]:
            text = str(term or "").strip()
            key = _normalize_search_text(text)
            if text and key and key not in seen:
                additions.append(text)
                seen.add(key)
    return f"{query} {' '.join(additions)}".strip(), matches


def _compact_query_with_entity_matches(
    query: str,
    matches: List[Dict[str, Any]],
) -> str:
    if not matches:
        return query

    additions: List[str] = []
    seen: Set[str] = set()
    for match in matches:
        for term in (match.get("canonical"), match.get("matched_term")):
            text = str(term or "").strip()
            key = _normalize_search_text(text)
            if text and key and key not in seen:
                additions.append(text)
                seen.add(key)
    return " ".join(additions).strip() or query


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _normalize_search_text(value: str) -> str:
    normalized = value.translate(_SEARCH_TRANSLATION).lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _search_tokens(value: str) -> List[str]:
    tokens = _normalize_search_text(value).split()
    return [
        token
        for token in tokens
        if len(token) >= 3 and token not in _SEARCH_STOPWORDS
    ]


def _token_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if left.startswith(right) or right.startswith(left):
        shorter = min(len(left), len(right))
        longer = max(len(left), len(right))
        return 0.78 + (0.18 * shorter / max(1, longer))
    return SequenceMatcher(None, left, right).ratio()


def _lexical_similarity(query: str, text: str) -> float:
    query_tokens = _search_tokens(query)
    if not query_tokens:
        return 0.0

    text_tokens = _search_tokens(text)
    if not text_tokens:
        return 0.0

    text_token_set = set(text_tokens)
    scores: List[float] = []
    for query_token in query_tokens:
        if query_token in text_token_set:
            scores.append(1.0)
            continue
        scores.append(max(_token_similarity(query_token, text_token) for text_token in text_token_set))

    token_score = sum(scores) / len(scores)
    phrase_bonus = 0.0
    normalized_query = _normalize_search_text(query)
    normalized_text = _normalize_search_text(text)
    if normalized_query and normalized_query in normalized_text:
        phrase_bonus = 0.12

    return _clamp_float(token_score + phrase_bonus, 0.0, 1.0)


def _contains_all_name_tokens(text: str, name: str) -> bool:
    text_tokens = set(_search_tokens(text))
    name_tokens = _search_tokens(name)
    return bool(name_tokens) and all(token in text_tokens for token in name_tokens)


def _person_profile_priority(payload: Dict[str, Any], priority_names: List[str]) -> float:
    if not priority_names:
        return 0.0

    filename = str(payload.get("filename") or "")
    text = LocalRAGService._payload_text(payload)
    text_head = text[:700]
    filename_norm = _normalize_search_text(filename)
    text_head_norm = _normalize_search_text(text_head)
    full_text_norm = _normalize_search_text(text)

    score = 0.0
    for name in priority_names:
        if _contains_all_name_tokens(filename, name):
            score += 0.30
        if _contains_all_name_tokens(text_head, name):
            score += 0.18
            if any(marker in text_head_norm for marker in ("founder", "president", "chairman", "osnivac", "predsednik")):
                score += 0.18
        if _contains_all_name_tokens(text, name) and "rag profil" in text_head_norm:
            score += 0.12
        if _contains_all_name_tokens(text, name) and "seed pitanja" in full_text_norm:
            score += 0.08

    low_value_source = (
        any(
            marker in filename_norm or marker in full_text_norm
            for marker in ("guideline", "scope", "taxonomy", "izvori i napomene")
        )
        or (full_text_norm.count("url") >= 3 and full_text_norm.count("napomena") >= 3)
    )
    filename_has_priority_name = any(
        _contains_all_name_tokens(filename, name) for name in priority_names
    )
    if low_value_source:
        score -= 0.18
        if not filename_has_priority_name:
            score -= 0.10

    return _clamp_float(score, -0.10, 0.55)


def _has_model_weights(model_path: Path) -> bool:
    weight_filenames = (
        "pytorch_model.bin",
        "model.safetensors",
        "tf_model.h5",
        "model.ckpt.index",
        "flax_model.msgpack",
        "pytorch_model.bin.index.json",
        "model.safetensors.index.json",
    )
    return any((model_path / name).exists() for name in weight_filenames)


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        logger.warning("Invalid int for %s=%r; using default=%s", name, value, default)
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except Exception:
        logger.warning("Invalid float for %s=%r; using default=%s", name, value, default)
        return default


def _resolve_device(requested: str) -> str:
    requested = requested.strip().lower()
    if requested == "cuda":
        if torch.cuda.is_available():
            return "cuda"
        logger.warning("RAG_DEVICE=cuda requested but CUDA unavailable; using CPU.")
    return "cpu"


def _safe_filename(name: Optional[str]) -> str:
    if not name:
        return "upload.pdf"
    return Path(name).name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "index"


def _env_csv(name: str, default: str) -> Set[str]:
    raw = _env_str(name, default)
    return {item.strip() for item in raw.split(",") if item.strip()}


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    include_scores: bool = False
    budget_ms: Optional[int] = Field(default=None, ge=50, le=30000)


class SearchHit(BaseModel):
    rank: int
    score: Optional[float]
    source: Optional[str]
    text: str
    payload: Dict[str, Any]


class SearchResponse(BaseModel):
    query: str
    top_k: int
    elapsed_ms: float
    context: str
    hits: List[SearchHit]

class IndexProfile(BaseModel):
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    kind: Literal["managed", "external"] = "managed"
    collection_name: str = Field(min_length=1)
    storage_path: Optional[str] = None
    immutable: bool = False

    @model_validator(mode="after")
    def validate_profile(self):
        if self.kind == "managed" and not self.storage_path:
            raise ValueError("storage_path is required for managed indexes")
        return self

class IndexState(BaseModel):
    active_slug: str
    query_slugs: List[str] = Field(default_factory=list)
    indexes: List[IndexProfile]

class CreateIndexRequest(BaseModel):
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    kind: Literal["managed", "external"] = "managed"
    collection_name: Optional[str] = None
    storage_path: Optional[str] = None

class ActivateIndexRequest(BaseModel):
    slug: str = Field(min_length=1)

class QueryIndexesRequest(BaseModel):
    slugs: List[str] = Field(min_length=1)

class HealthResponse(BaseModel):
    status: str
    active_index: IndexProfile
    collection: str
    model: str
    device: str
    storage_path: Optional[str]
    index_kind: str

class DocumentInfo(BaseModel):
    id: str
    filename: str
    chunks: int
    uploaded_at: str
    file_size: int


class KnowledgeState(BaseModel):
    documents: List[DocumentInfo]
    total_chunks: int
    last_indexed: Optional[str]


class LocalRAGService:
    def __init__(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        default_local_model = repo_root / "bge-m3-local"
        default_hf_model = "BAAI/bge-m3"

        self.qdrant_url = _env_str("RAG_QDRANT_URL", "http://127.0.0.1:6333")
        default_data_root = Path(__file__).resolve().parent / "data"
        self.data_root = Path(_env_str("RAG_DATA_ROOT", str(default_data_root))).resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_root / "index_state.json"
        self.entity_dictionary_path = Path(
            _env_str(
                "RAG_ENTITY_DICTIONARY_PATH",
                str(self.data_root / "entity_dictionary.json"),
            )
        ).expanduser()
        if not self.entity_dictionary_path.is_absolute():
            self.entity_dictionary_path = (repo_root / self.entity_dictionary_path).resolve()
        self.entity_dictionary = _load_entity_dictionary(self.entity_dictionary_path)
        
        configured_model = os.getenv("RAG_EMBED_MODEL")
        if configured_model and configured_model.strip():
            self.model_name = configured_model.strip()
        elif _has_model_weights(default_local_model):
            self.model_name = str(default_local_model)
        else:
            self.model_name = default_hf_model

        self.device = _resolve_device(_env_str("RAG_DEVICE", "cpu"))
        self.max_length = max(64, _env_int("RAG_MAX_LENGTH", 256))
        self.default_top_k = max(1, _env_int("RAG_TOP_K", 5))
        self.timeout_s = max(0.1, _env_float("RAG_QDRANT_TIMEOUT_SECONDS", 4.0))
        self.discovery_ttl_s = max(5.0, _env_float("RAG_COLLECTION_DISCOVERY_TTL_SECONDS", 30.0))
        self.hybrid_candidate_multiplier = max(1, _env_int("RAG_HYBRID_CANDIDATE_MULTIPLIER", 6))
        self.hybrid_vector_weight = _clamp_float(_env_float("RAG_HYBRID_VECTOR_WEIGHT", 0.72), 0.0, 1.0)
        self.hybrid_lexical_scan = _env_bool("RAG_HYBRID_LEXICAL_SCAN", True)
        self.hybrid_lexical_scan_limit = max(0, _env_int("RAG_HYBRID_LEXICAL_SCAN_LIMIT", 250))
        self.hybrid_min_lexical_score = _clamp_float(_env_float("RAG_HYBRID_MIN_LEXICAL_SCORE", 0.56), 0.0, 1.0)
        self.lexical_fast_path = _env_bool("RAG_LEXICAL_FAST_PATH", True)
        self.lexical_fast_path_min_score = _clamp_float(
            _env_float("RAG_LEXICAL_FAST_PATH_MIN_SCORE", 0.68),
            0.0,
            1.0,
        )
        self.lexical_fast_path_min_hits = max(
            1,
            _env_int("RAG_LEXICAL_FAST_PATH_MIN_HITS", 1),
        )
        self.person_query_min_top_k = max(1, _env_int("RAG_PERSON_QUERY_MIN_TOP_K", 4))
        self.hybrid_min_final_score = _clamp_float(
            _env_float("RAG_HYBRID_MIN_FINAL_SCORE", 0.32),
            0.0,
            1.0,
        )
        self.context_max_chars = max(200, _env_int("RAG_CONTEXT_MAX_CHARS", 2400))
        self.query_embedding_cache_size = max(
            0,
            _env_int("RAG_QUERY_EMBED_CACHE_SIZE", 128),
        )
        self.immutable_index_slugs = _env_csv("RAG_IMMUTABLE_INDEX_SLUGS", MAIN_INDEX_SLUG)
        self.immutable_index_slugs.add(MAIN_INDEX_SLUG)
        self.offline_mode = _env_bool("RAG_OFFLINE", False)
        self.local_model_only = self.offline_mode or Path(self.model_name).expanduser().exists()

        self.chunk_chars = max(200, _env_int("RAG_CHUNK_CHARS", 2500))
        self.overlap_chars = max(0, _env_int("RAG_OVERLAP_CHARS", 300))
        if self.overlap_chars >= self.chunk_chars:
            raise ValueError("RAG_OVERLAP_CHARS must be smaller than RAG_CHUNK_CHARS")

        effective_context_chars = _effective_context_max_chars(
            self.context_max_chars,
            self.chunk_chars,
        )
        if effective_context_chars != self.context_max_chars:
            logger.warning(
                "RAG_CONTEXT_MAX_CHARS=%d is smaller than one chunk (%d); raising to %d",
                self.context_max_chars,
                self.chunk_chars,
                effective_context_chars,
            )
            self.context_max_chars = effective_context_chars



        self.metadata_fields = ["filename", "page_number", "chunk_index", "document_id"]

        if self.offline_mode:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        self._qdrant = QdrantClient(url=self.qdrant_url, timeout=self.timeout_s)
        self._discovered_indexes_cache: List[IndexProfile] = []
        self._discovered_indexes_cache_ts = 0.0
        self._canonical_person_names: Set[str] = set()
        self._query_embedding_cache: OrderedDict[str, List[float]] = OrderedDict()
        self._query_embedding_cache_lock = threading.Lock()
        self._lexical_point_cache: Dict[str, List[tuple[Any, Set[str], str]]] = {}
        self._lexical_point_cache_lock = threading.Lock()
        model_load_kwargs = {"local_files_only": self.local_model_only}
        try:
            from transformers import AutoModel, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, **model_load_kwargs)
            self._model = AutoModel.from_pretrained(self.model_name, **model_load_kwargs).eval().to(self.device)
        except Exception as exc:
            if self.offline_mode:
                raise RuntimeError(
                    "Failed to load the embedding model in offline mode. "
                    f"Set RAG_EMBED_MODEL to a local directory with the BGE-M3 files. "
                    f"Current value: {self.model_name}"
                ) from exc
            raise
        self.embedding_dim = int(self._model.config.hidden_size)




        self.state = self._load_or_bootstrap_state()
        try:
            active = self.activate_index(self.state.active_slug, persist=False)
        except HTTPException as exc:
            fallback = self.state.indexes[0]
            logger.warning(
                "Failed to activate configured knowledge index '%s': %s. Falling back to '%s'.",
                self.state.active_slug,
                exc.detail,
                fallback.slug,
            )
            self.state.active_slug = fallback.slug
            self._save_state()
            active = self.activate_index(fallback.slug, persist=False)

        warmup_started = time.perf_counter()
        self._embed_texts(["RAG model warmup"])
        logger.info(
            "RAG embedding model warmed in %.2fms",
            (time.perf_counter() - warmup_started) * 1000.0,
        )
        self._refresh_canonical_person_names(self.get_query_indexes())
        if self.lexical_fast_path and self.hybrid_lexical_scan_limit > 0:
            lexical_warmup_started = time.perf_counter()
            for profile in self.get_query_indexes():
                self._lexical_records(profile)
            logger.info(
                "RAG lexical cache warmed in %.2fms",
                (time.perf_counter() - lexical_warmup_started) * 1000.0,
            )

        logger.info(
            "RAG service ready: qdrant=%s active_slug=%s collection=%s model=%s device=%s storage=%s chunk=%s overlap=%s hybrid_multiplier=%s lexical_scan=%s entity_dictionary=%d",
            self.qdrant_url,
            active.slug,
            active.collection_name,
            self.model_name,
            self.device,
            active.storage_path,
            self.chunk_chars,
            self.overlap_chars,
            self.hybrid_candidate_multiplier,
            self.hybrid_lexical_scan,
            len(self.entity_dictionary),
        )

    def _invalidate_lexical_cache(self, collection_name: str | None = None) -> None:
        with self._lexical_point_cache_lock:
            if collection_name is None:
                self._lexical_point_cache.clear()
            else:
                self._lexical_point_cache.pop(collection_name, None)

    def _refresh_canonical_person_names(
        self,
        profiles: IndexProfile | List[IndexProfile],
    ) -> None:
        names: Set[str] = set()
        for entity in getattr(self, "entity_dictionary", []):
            if entity.get("type") == "person":
                canonical = str(entity.get("canonical") or "").strip()
                if len(canonical.split()) >= 2:
                    names.add(canonical)

        profiles_to_scan = profiles if isinstance(profiles, list) else [profiles]
        try:
            for profile in profiles_to_scan:
                offset: Any = None
                while True:
                    records, offset = self._qdrant.scroll(
                        collection_name=profile.collection_name,
                        limit=64,
                        offset=offset,
                        with_payload=True,
                        with_vectors=False,
                    )
                    for record in records:
                        payload = dict(record.payload or {})
                        names.update(
                            _person_name_candidates(
                                self._payload_text(payload)
                            )
                        )
                        filename = str(payload.get("filename") or "")
                        if filename:
                            names.update(_person_name_candidates(filename))
                    if offset is None:
                        break
        except Exception as exc:
            logger.warning("Failed to build canonical person-name cache: %s", exc)
        self._canonical_person_names = names
        logger.info(
            "RAG canonical person-name cache ready: names=%d indexes=%d",
            len(names),
            len(profiles_to_scan),
        )

    def _normalize_storage_path(self, raw_path: str) -> str:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (self.data_root / path).resolve()
        return str(path)
    
    def _bootstrap_state(self) -> IndexState:
        slug = _env_str("RAG_ACTIVE_INDEX_SLUG", "main")
        kind = _env_str("RAG_INDEX_KIND", "managed").strip().lower()
        if kind not in {"managed", "external"}:
            kind = "managed"

        collection_name = _env_str(
            "RAG_QDRANT_COLLECTION",
            "robot_knowledge" if slug == "main" else f"robot_knowledge_{slug}",
        )

        storage_path = None
        if kind == "managed":
            default_storage = self.data_root / "knowledge" / slug
            storage_path = self._normalize_storage_path(
                _env_str("RAG_STORAGE_PATH", str(default_storage))
            )

        profile = IndexProfile(
            slug=slug,
            title=slug.replace("_", " ").title(),
            description="Bootstrap knowledge index",
            kind=kind,
            collection_name=collection_name,
            storage_path=storage_path,
            immutable=slug in self.immutable_index_slugs,
        )

        
        return IndexState(active_slug=profile.slug, query_slugs=[profile.slug], indexes=[profile])

    def _save_state(self) -> None:
        try:
            self.state_path.write_text(
                json.dumps(self.state.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except PermissionError as exc:
            logger.warning("Cannot persist index state to %s: %s", self.state_path, exc)

    def _load_or_bootstrap_state(self) -> IndexState:
        if self.state_path.exists():
            try:
                state = IndexState(**json.loads(self.state_path.read_text(encoding="utf-8")))
                if not state.indexes:
                    raise ValueError("No indexes in state file")
                if not state.active_slug:
                    state.active_slug = state.indexes[0].slug
                for item in state.indexes:
                    item.immutable = item.slug in self.immutable_index_slugs
                self.state = state
                self._normalize_query_slugs(force_refresh=True)
                self._save_state()
                return state
            except Exception as exc:
                logger.warning("Invalid index state file %s: %s", self.state_path, exc)

        self.state = self._bootstrap_state()
        self._save_state()
        return self.state

    def _normalize_query_slugs(self, *, force_refresh: bool = False) -> None:
        available_slugs = {
            item.slug
            for item in self._all_indexes(force_refresh=force_refresh)
        }
        normalized: List[str] = []
        for slug in self.state.query_slugs or [self.state.active_slug]:
            if slug in available_slugs and slug not in normalized:
                normalized.append(slug)

        if not normalized:
            if self.state.active_slug in available_slugs:
                normalized = [self.state.active_slug]
            elif self.state.indexes:
                normalized = [self.state.indexes[0].slug]

        self.state.query_slugs = normalized

    def _make_discovered_index(self, collection_name: str, used_slugs: Set[str]) -> IndexProfile:
        base_slug = _slugify(collection_name)
        slug = base_slug
        suffix = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        used_slugs.add(slug)

        return IndexProfile(
            slug=slug,
            title=collection_name.replace("_", " ").replace("-", " ").title(),
            description="Discovered from available Qdrant collections",
            kind="external",
            collection_name=collection_name,
            storage_path=None,
            immutable=slug in self.immutable_index_slugs,
        )

    def _discover_indexes(self, *, force_refresh: bool = False) -> List[IndexProfile]:
        now = time.time()
        if (
            not force_refresh
            and self._discovered_indexes_cache_ts > 0.0
            and (now - self._discovered_indexes_cache_ts) < self.discovery_ttl_s
        ):
            return self._discovered_indexes_cache

        configured_collections = {item.collection_name for item in self.state.indexes}
        used_slugs = {item.slug for item in self.state.indexes}

        try:
            collections = self._qdrant.get_collections().collections
            discovered: List[IndexProfile] = []
            for collection in sorted(collections, key=lambda item: item.name):
                collection_name = collection.name
                if collection_name in configured_collections:
                    continue
                discovered.append(self._make_discovered_index(collection_name, used_slugs))
        except Exception as exc:
            logger.warning("Failed to discover Qdrant collections: %s", exc)
            if self._discovered_indexes_cache_ts > 0.0:
                return self._discovered_indexes_cache
            return []

        self._discovered_indexes_cache = discovered
        self._discovered_indexes_cache_ts = now
        return discovered

    def _all_indexes(self, *, force_refresh: bool = False) -> List[IndexProfile]:
        return [*self.state.indexes, *self._discover_indexes(force_refresh=force_refresh)]

    def _get_index_by_slug(self, slug: str, *, force_refresh: bool = False) -> IndexProfile:
        for item in self._all_indexes(force_refresh=force_refresh):
            if item.slug == slug:
                return item
        raise HTTPException(status_code=404, detail=f"Unknown knowledge index: {slug}")

    def list_indexes(self) -> IndexState:
        self._normalize_query_slugs()
        active = self.get_active_index()
        return IndexState(active_slug=active.slug, query_slugs=self.state.query_slugs, indexes=self._all_indexes())
    
    def get_active_index(self) -> IndexProfile:
        return self._get_index_by_slug(self.state.active_slug)

    def get_query_indexes(self) -> List[IndexProfile]:
        self._normalize_query_slugs()
        return [
            self._get_index_by_slug(slug)
            for slug in self.state.query_slugs
        ]

    def update_query_indexes(self, slugs: List[str]) -> IndexState:
        normalized: List[str] = []
        for raw_slug in slugs:
            slug = str(raw_slug).strip()
            if slug and slug not in normalized:
                normalized.append(slug)

        if not normalized:
            raise HTTPException(status_code=400, detail="At least one knowledge index must be selected for RAG queries")

        profiles = [
            self._get_index_by_slug(slug, force_refresh=True)
            for slug in normalized
        ]
        for profile in profiles:
            self._ensure_collection(profile)

        self.state.query_slugs = [profile.slug for profile in profiles]
        self._save_state()
        self._refresh_canonical_person_names(profiles)
        return self.list_indexes()

    @staticmethod
    def _collection_vector_size(collection_info: Any) -> Optional[int]:
        params = getattr(getattr(collection_info, "config", None), "params", None)
        vectors = getattr(params, "vectors", None)
        if isinstance(vectors, dict):
            return None
        size = getattr(vectors, "size", None)
        return int(size) if size is not None else None

    def _validate_collection_vector_size(self, profile: IndexProfile, collection_info: Any) -> None:
        vector_size = self._collection_vector_size(collection_info)
        if vector_size is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Qdrant collection '{profile.collection_name}' has an unsupported vector "
                    f"configuration; expected unnamed vectors with size={self.embedding_dim}"
                ),
            )
        if vector_size != self.embedding_dim:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Qdrant collection '{profile.collection_name}' vector size is {vector_size}, "
                    f"but embedding model '{self.model_name}' outputs {self.embedding_dim}. "
                    "Use a matching embedding model or create a new collection."
                ),
            )

    def _ensure_collection(self, profile: IndexProfile) -> None:
        try:
            collection_info = self._qdrant.get_collection(profile.collection_name)
        except HTTPException:
            raise
        except Exception:
            collection_info = None
        else:
            self._validate_collection_vector_size(profile, collection_info)
            return

        if collection_info is None:
            if profile.kind == "external":
                raise HTTPException(
                    status_code=409,
                    detail=f"Qdrant collection '{profile.collection_name}' was not found for external index '{profile.slug}'",
                )
            logger.info("Qdrant collection %s not found; creating it.", profile.collection_name)

        self._qdrant.create_collection(
            collection_name=profile.collection_name,
            vectors_config=models.VectorParams(
                size=self.embedding_dim,
                distance=models.Distance.COSINE,
            ),
        )
    
    def _storage_root(self) -> Path:
        active = self.get_active_index()
        if active.kind != "managed" or not active.storage_path:
            raise RuntimeError("No local storage path is configured for the active knowledge index")
        return Path(active.storage_path).resolve()
    
    @torch.no_grad()
    def _embed_texts(self, texts: List[str], batch_size: int = 16) -> List[List[float]]:
        vectors: List[List[float]] = []

        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            enc = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            enc = {k: v.to(self.device) for k, v in enc.items()}
            out = self._model(**enc)
            last_hidden = out.last_hidden_state

            mask = enc["attention_mask"].unsqueeze(-1).expand(last_hidden.size()).float()
            summed = (last_hidden * mask).sum(dim=1)
            counts = mask.sum(dim=1).clamp(min=1e-9)
            mean = summed / counts
            mean = torch.nn.functional.normalize(mean, p=2, dim=1)

            vectors.extend(mean.detach().cpu().tolist())

        return vectors

    @staticmethod
    def _query_embedding_cache_key(query: str) -> str:
        return " ".join(_normalize_search_text(query).split())

    def _embed_query(self, query: str) -> List[float]:
        if not hasattr(self, "_query_embedding_cache"):
            return self._embed_texts([query])[0]

        cache_key = self._query_embedding_cache_key(query)
        if self.query_embedding_cache_size > 0 and cache_key:
            with self._query_embedding_cache_lock:
                cached = self._query_embedding_cache.get(cache_key)
                if cached is not None:
                    self._query_embedding_cache.move_to_end(cache_key)
                    latency_tracer.emit(
                        "rag.embedding",
                        duration_ms=0.0,
                        critical_path=True,
                        query_chars=len(query),
                        cache_hit=True,
                    )
                    return list(cached)

        embed_started = time.perf_counter()
        vector = self._embed_texts([query])[0]
        latency_tracer.emit(
            "rag.embedding",
            duration_ms=(time.perf_counter() - embed_started) * 1000.0,
            critical_path=True,
            query_chars=len(query),
            cache_hit=False,
        )
        if self.query_embedding_cache_size > 0 and cache_key:
            with self._query_embedding_cache_lock:
                self._query_embedding_cache[cache_key] = list(vector)
                self._query_embedding_cache.move_to_end(cache_key)
                while len(self._query_embedding_cache) > self.query_embedding_cache_size:
                    self._query_embedding_cache.popitem(last=False)
        return vector
    
    def _pdf_path(self, doc_id: str) -> Path:
        return self._storage_root() / f"{doc_id}.pdf"

    def _metadata_path(self, doc_id: str) -> Path:
        return self._storage_root() / f"{doc_id}.json"

    def _save_metadata(self, document: DocumentInfo) -> None:
        self._metadata_path(document.id).write_text(
            json.dumps(document.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_documents(self) -> List[DocumentInfo]:
        active = self.get_active_index()
        if active.kind != "managed":
            return []

        documents: List[DocumentInfo] = []
        for meta_path in self._storage_root().glob("*.json"):
            try:
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                documents.append(DocumentInfo(**raw))
            except Exception as exc:
                logger.warning("Skipping invalid metadata file %s: %s", meta_path, exc)

        documents.sort(key=lambda doc: doc.uploaded_at, reverse=True)
        return documents

    def list_documents(self) -> KnowledgeState:
        documents = self._load_documents()
        return KnowledgeState(
            documents=documents,
            total_chunks=sum(doc.chunks for doc in documents),
            last_indexed=documents[0].uploaded_at if documents else None,
        )
    
    def _extract_pdf_pages(self, file_path: Path) -> List[Dict[str, Any]]:
        pdf = fitz.open(file_path)
        pages: List[Dict[str, Any]] = []

        try:
            for page_number, page in enumerate(pdf, start=1):
                text = " ".join(page.get_text().split())
                if text:
                    pages.append({"page_number": page_number, "text": text})
        finally:
            pdf.close()

        if not pages:
            raise RuntimeError("No extractable text found in PDF")

        return pages

    def _chunk_pages(self, pages: List[Dict[str, Any]], doc_id: str, filename: str) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []

        for page in pages:
            page_number = int(page["page_number"])
            text = str(page["text"])
            start = 0
            chunk_index = 0

            while start < len(text):
                end = min(len(text), start + self.chunk_chars)
                chunk_text = text[start:end].strip()

                if chunk_text:
                    chunk_id = f"{doc_id}_p{page_number}_c{chunk_index}"
                    chunks.append(
                        {
                            "chunk_id": chunk_id,
                            "document_id": doc_id,
                            "filename": filename,
                            "page_number": page_number,
                            "chunk_index": chunk_index,
                            "start_pos": start,
                            "end_pos": end,
                            "text": chunk_text,
                        }
                    )

                if end >= len(text):
                    break

                start = end - self.overlap_chars
                chunk_index += 1

        return chunks

    def _upsert_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        if not chunks:
            return

        active = self.get_active_index()
        embeddings = self._embed_texts([chunk["text"] for chunk in chunks])
        points: List[models.PointStruct] = []

        for chunk, vector in zip(chunks, embeddings):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk["chunk_id"]))
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "document_id": chunk["document_id"],
                        "chunk_id": chunk["chunk_id"],
                        "filename": chunk["filename"],
                        "page_number": chunk["page_number"],
                        "chunk_index": chunk["chunk_index"],
                        "start_pos": chunk["start_pos"],
                        "end_pos": chunk["end_pos"],
                        "text": chunk["text"],
                    },
                )
            )

        for i in range(0, len(points), 64):
            self._qdrant.upsert(
                collection_name=active.collection_name,
                points=points[i:i + 64],
            )
        self._invalidate_lexical_cache(active.collection_name)
        for chunk in chunks:
            self._canonical_person_names.update(
                _person_name_candidates(str(chunk.get("text") or ""))
            )

    def _delete_document_chunks(self, doc_id: str) -> None:
        active = self.get_active_index()
        doc_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=doc_id),
                )
            ]
        )
        self._qdrant.delete(
            collection_name=active.collection_name,
            points_selector=models.FilterSelector(filter=doc_filter),
        )
        self._invalidate_lexical_cache(active.collection_name)

    def _index_pdf(self, pdf_path: Path, doc_id: str, filename: str) -> int:
        pages = self._extract_pdf_pages(pdf_path)
        chunks = self._chunk_pages(pages, doc_id, filename)
        if not chunks:
            raise RuntimeError("No chunks were generated from PDF")
        self._upsert_chunks(chunks)
        return len(chunks)

    def _require_managed_index(self) -> None:
        active = self.get_active_index()
        if active.immutable:
            raise HTTPException(
                status_code=409,
                detail="Active knowledge index is immutable/read-only. Upload, delete, and reindex are disabled.",
            )
        if active.kind != "managed":
            raise HTTPException(
                status_code=409,
                detail="Active knowledge index is external/search-only. Upload, delete, and reindex are disabled.",
            )
        
    async def upload_document(self, file: UploadFile) -> DocumentInfo:
        self._require_managed_index()
        filename = _safe_filename(file.filename)

        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are supported")

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        doc_id = f"doc_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        pdf_path = self._pdf_path(doc_id)
        pdf_path.write_bytes(content)

        try:
            chunk_count = self._index_pdf(pdf_path, doc_id, filename)
        except Exception:
            pdf_path.unlink(missing_ok=True)
            raise

        document = DocumentInfo(
            id=doc_id,
            filename=filename,
            chunks=chunk_count,
            uploaded_at=_now_iso(),
            file_size=len(content),
        )
        self._save_metadata(document)
        return document

    def delete_document(self, doc_id: str) -> Dict[str, Any]:
        self._require_managed_index()
        pdf_path = self._pdf_path(doc_id)
        meta_path = self._metadata_path(doc_id)

        try:
            self._delete_document_chunks(doc_id)
        except Exception as exc:
            logger.warning("Failed deleting Qdrant chunks for %s: %s", doc_id, exc)

        deleted_any = False
        if pdf_path.exists():
            pdf_path.unlink()
            deleted_any = True
        if meta_path.exists():
            meta_path.unlink()
            deleted_any = True

        if not deleted_any:
            return {"success": False, "message": f"Document {doc_id} not found"}

        return {"success": True, "message": f"Document {doc_id} deleted"}

    def reindex_all_documents(self) -> Dict[str, Any]:
        self._require_managed_index()
        documents = self._load_documents()
        total_chunks = 0

        for document in documents:
            pdf_path = self._pdf_path(document.id)
            if not pdf_path.exists():
                logger.warning("Skipping missing PDF for %s", document.id)
                continue

            self._delete_document_chunks(document.id)
            chunk_count = self._index_pdf(pdf_path, document.id, document.filename)
            updated = DocumentInfo(
                id=document.id,
                filename=document.filename,
                chunks=chunk_count,
                uploaded_at=document.uploaded_at,
                file_size=pdf_path.stat().st_size,
            )
            self._save_metadata(updated)
            total_chunks += chunk_count

        return {
            "indexed_documents": len(documents),
            "total_chunks": total_chunks,
            "timestamp": _now_iso(),
        }

    @staticmethod
    def _payload_text(payload: Dict[str, Any]) -> str:
        for key in ("text", "chunk_content", "content", "chunk_text"):
            value = payload.get(key)
            if value is not None:
                text = " ".join(str(value).split())
                if text:
                    return text
        return ""

    @staticmethod
    def _candidate_key(point: Any, profile: IndexProfile) -> str:
        payload = dict(getattr(point, "payload", None) or {})
        stable_id = payload.get("chunk_id") or payload.get("document_id") or getattr(point, "id", None)
        return f"{profile.collection_name}:{stable_id}"

    @staticmethod
    def _lexical_text(payload: Dict[str, Any]) -> str:
        parts = [
            str(payload.get("filename") or ""),
            str(payload.get("document_id") or ""),
            str(payload.get("chunk_id") or ""),
            LocalRAGService._payload_text(payload),
        ]
        return " ".join(part for part in parts if part)

    def _lexical_records(self, profile: IndexProfile) -> List[tuple[Any, Set[str], str]]:
        cache = getattr(self, "_lexical_point_cache", None)
        cache_lock = getattr(self, "_lexical_point_cache_lock", None)
        if cache is not None and cache_lock is not None:
            with cache_lock:
                cached = cache.get(profile.collection_name)
                if cached is not None:
                    return cached

        started = time.perf_counter()
        records_all: List[tuple[Any, Set[str], str]] = []
        offset: Any = None
        while True:
            try:
                records, offset = self._qdrant.scroll(
                    collection_name=profile.collection_name,
                    limit=128,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception as exc:
                logger.warning(
                    "Lexical cache load failed for knowledge index '%s': %s",
                    profile.slug,
                    exc,
                )
                break

            if not records:
                break

            for record in records:
                payload = dict(record.payload or {})
                lexical_text = self._lexical_text(payload)
                records_all.append(
                    (
                        record,
                        set(_search_tokens(lexical_text)),
                        _normalize_search_text(lexical_text),
                    )
                )
            if offset is None:
                break

        if cache is not None and cache_lock is not None:
            with cache_lock:
                cached = cache.setdefault(profile.collection_name, records_all)
        else:
            cached = records_all

        latency_tracer.emit(
            "rag.lexical_cache_load",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            critical_path=True,
            collection=profile.collection_name,
            records=len(cached),
        )
        return cached

    @staticmethod
    def _point_with_payload(point: Any, payload: Dict[str, Any]) -> Any:
        if hasattr(point, "model_copy"):
            return point.model_copy(update={"payload": payload})
        if hasattr(point, "copy"):
            return point.copy(update={"payload": payload})
        point.payload = payload
        return point

    def _lexical_scan_points(self, profile: IndexProfile, query: str, limit: int) -> List[tuple[Any, float]]:
        if limit <= 0:
            return []

        query_tokens = sorted(set(_search_tokens(query)))
        if not query_tokens:
            return []
        normalized_query = _normalize_search_text(query)
        results: List[tuple[Any, float]] = []
        for record, text_token_set, normalized_text in self._lexical_records(profile)[:limit]:
            scores: List[float] = []
            for token in query_tokens:
                if token in text_token_set:
                    scores.append(1.0)
                    continue
                candidate_tokens = [
                    text_token
                    for text_token in text_token_set
                    if text_token[:1] == token[:1]
                    and abs(len(text_token) - len(token)) <= max(2, len(token) // 3)
                ]
                scores.append(
                    max(
                        (_token_similarity(token, text_token) for text_token in candidate_tokens),
                        default=0.0,
                    )
                )
            lexical_score = sum(scores) / len(scores)
            if normalized_query and normalized_query in normalized_text:
                lexical_score = _clamp_float(lexical_score + 0.12, 0.0, 1.0)
            if lexical_score >= self.hybrid_min_lexical_score:
                results.append((record, lexical_score))

        results.sort(key=lambda item: item[1], reverse=True)
        return results[:limit]

    def _search_points(
        self,
        query: str,
        limit: int,
        *,
        deadline: float | None = None,
        lexical_fast_path_min_score: float | None = None,
        priority_names: Optional[List[str]] = None,
    ) -> List[tuple[Any, IndexProfile]]:
        priority_names = priority_names or []
        lexical_started = time.perf_counter()
        lexical_fast_path = getattr(self, "lexical_fast_path", True)
        lexical_fast_path_min_score_default = getattr(
            self,
            "lexical_fast_path_min_score",
            0.68,
        )
        lexical_fast_path_min_hits = getattr(
            self,
            "lexical_fast_path_min_hits",
            1,
        )
        if lexical_fast_path and self.hybrid_lexical_scan_limit > 0:
            fast_path_min_score = (
                lexical_fast_path_min_score_default
                if lexical_fast_path_min_score is None
                else _clamp_float(lexical_fast_path_min_score, 0.0, 1.0)
            )
            lexical_candidates: Dict[str, Dict[str, Any]] = {}
            for profile_index, profile in enumerate(self.get_query_indexes()):
                if (
                    profile_index > 0
                    and deadline is not None
                    and time.perf_counter() >= deadline
                ):
                    break
                for point, lexical_score in self._lexical_scan_points(
                    profile,
                    query,
                    self.hybrid_lexical_scan_limit,
                ):
                    key = self._candidate_key(point, profile)
                    entry = lexical_candidates.setdefault(
                        key,
                        {
                            "point": point,
                            "profile": profile,
                            "lexical_score": 0.0,
                        },
                    )
                    entry["lexical_score"] = max(
                        entry["lexical_score"],
                        lexical_score,
                    )
                    entry["priority_score"] = max(
                        float(entry.get("priority_score") or 0.0),
                        _person_profile_priority(
                            dict(getattr(point, "payload", None) or {}),
                            priority_names,
                        ),
                    )

            lexical_ranked = sorted(
                lexical_candidates.values(),
                key=lambda entry: (
                    float(entry["lexical_score"]) + float(entry.get("priority_score") or 0.0),
                    float(entry["lexical_score"]),
                ),
                reverse=True,
            )
            strong = [
                entry
                for entry in lexical_ranked
                if float(entry["lexical_score"]) >= fast_path_min_score
            ]
            latency_tracer.emit(
                "rag.lexical_fast_path",
                duration_ms=(time.perf_counter() - lexical_started) * 1000.0,
                critical_path=True,
                hits=len(strong),
                best_score=(
                    float(lexical_ranked[0]["lexical_score"])
                    if lexical_ranked
                    else 0.0
                ),
                min_score=fast_path_min_score,
                used=len(strong) >= lexical_fast_path_min_hits,
            )
            if len(strong) >= lexical_fast_path_min_hits:
                results: List[tuple[Any, IndexProfile]] = []
                for rank, entry in enumerate(strong[:limit], start=1):
                    payload = dict(entry["point"].payload or {})
                    lexical_score = float(entry["lexical_score"])
                    payload["retrieval_rank"] = rank
                    payload["vector_score"] = 0.0
                    payload["lexical_score"] = lexical_score
                    payload["priority_score"] = float(entry.get("priority_score") or 0.0)
                    payload["hybrid_score"] = lexical_score + payload["priority_score"]
                    entry["point"] = self._point_with_payload(entry["point"], payload)
                    results.append((entry["point"], entry["profile"]))
                return results

        vector = self._embed_query(query)
        candidate_limit = max(limit, min(50, limit * self.hybrid_candidate_multiplier))
        vector_weight = self.hybrid_vector_weight
        lexical_weight = 1.0 - vector_weight
        candidates: Dict[str, Dict[str, Any]] = {}

        for profile_index, profile in enumerate(self.get_query_indexes()):
            if (
                profile_index > 0
                and deadline is not None
                and time.perf_counter() >= deadline
            ):
                break
            try:
                vector_started = time.perf_counter()
                points = self._qdrant.query_points(
                    collection_name=profile.collection_name,
                    query=vector,
                    limit=candidate_limit,
                    with_payload=True,
                ).points
                latency_tracer.emit(
                    "rag.vector_search",
                    duration_ms=(time.perf_counter() - vector_started) * 1000.0,
                    critical_path=True,
                    index_slug=profile.slug,
                    candidate_limit=candidate_limit,
                )

            except Exception as exc:
                logger.warning("Search failed for knowledge index '%s': %s", profile.slug, exc)
                continue

            for point in points:
                payload = dict(point.payload or {})
                key = self._candidate_key(point, profile)
                entry = candidates.setdefault(
                    key,
                    {
                        "point": point,
                        "profile": profile,
                        "vector_score": 0.0,
                        "lexical_score": 0.0,
                    },
                )
                entry["vector_score"] = max(entry["vector_score"], float(point.score or 0.0))
                entry["lexical_score"] = max(
                    entry["lexical_score"],
                    _lexical_similarity(query, self._lexical_text(payload)),
                )
                entry["priority_score"] = max(
                    float(entry.get("priority_score") or 0.0),
                    _person_profile_priority(payload, priority_names),
                )

            # Voice requests prefer useful partial recall over waiting for every
            # selected index. Query indexes are ordered with the active index
            # first, so return its candidates within the caller's deadline.
            if deadline is not None and candidates:
                break

            # A full lexical collection scan is useful for offline/admin search,
            # but cannot be interrupted mid-scroll. For latency-budgeted voice
            # search, retain lexical scoring on vector candidates and return
            # those candidates rather than risking loss of the whole response.
            if (
                deadline is None
                and self.hybrid_lexical_scan
                and self.hybrid_lexical_scan_limit > 0
            ):
                for point, lexical_score in self._lexical_scan_points(
                    profile,
                    query,
                    self.hybrid_lexical_scan_limit,
                ):
                    key = self._candidate_key(point, profile)
                    entry = candidates.setdefault(
                        key,
                        {
                            "point": point,
                            "profile": profile,
                            "vector_score": 0.0,
                            "lexical_score": 0.0,
                        },
                    )
                    entry["lexical_score"] = max(entry["lexical_score"], lexical_score)
                    entry["priority_score"] = max(
                        float(entry.get("priority_score") or 0.0),
                        _person_profile_priority(
                            dict(getattr(point, "payload", None) or {}),
                            priority_names,
                        ),
                    )

        ranked = sorted(
            candidates.values(),
            key=lambda entry: (
                vector_weight * float(entry["vector_score"])
                + lexical_weight * float(entry["lexical_score"])
                + float(entry.get("priority_score") or 0.0),
                float(entry["vector_score"]),
            ),
            reverse=True,
        )
        if ranked:
            top_score = (
                vector_weight * float(ranked[0]["vector_score"])
                + lexical_weight * float(ranked[0]["lexical_score"])
                + float(ranked[0].get("priority_score") or 0.0)
            )
            if top_score < self.hybrid_min_final_score:
                logger.info(
                    "RAG below_threshold score=%.4f threshold=%.4f query=%r",
                    top_score,
                    self.hybrid_min_final_score,
                    query,
                )
        ranked = [
            entry
            for entry in ranked
            if (
                vector_weight * float(entry["vector_score"])
                + lexical_weight * float(entry["lexical_score"])
                + float(entry.get("priority_score") or 0.0)
            )
            >= self.hybrid_min_final_score
        ]

        for rank, entry in enumerate(ranked[:limit], start=1):
            payload = dict(entry["point"].payload or {})
            payload["retrieval_rank"] = rank
            payload["vector_score"] = float(entry["vector_score"])
            payload["lexical_score"] = float(entry["lexical_score"])
            payload["priority_score"] = float(entry.get("priority_score") or 0.0)
            payload["hybrid_score"] = (
                vector_weight * float(entry["vector_score"])
                + lexical_weight * float(entry["lexical_score"])
                + payload["priority_score"]
            )
            entry["point"] = self._point_with_payload(entry["point"], payload)

        return [(entry["point"], entry["profile"]) for entry in ranked[:limit]]


    def search_knowledge(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        points = self._search_points(query, limit)
        results: List[Dict[str, Any]] = []

        for point, profile in points:
            payload = dict(point.payload or {})
            text = self._payload_text(payload)
            results.append(
                {
                    "score": float(payload.get("hybrid_score") or point.score or 0.0),
                    "vector_score": float(payload.get("vector_score") or point.score or 0.0),
                    "lexical_score": float(payload.get("lexical_score") or 0.0),
                    "index_slug": profile.slug,
                    "index_title": profile.title,
                    "collection_name": profile.collection_name,
                    "document_id": payload.get("document_id"),
                    "chunk_id": payload.get("chunk_id"),
                    "filename": payload.get("filename"),
                    "page_number": payload.get("page_number"),
                    "chunk_index": payload.get("chunk_index"),
                    "text": text,
                    "start_pos": payload.get("start_pos"),
                    "end_pos": payload.get("end_pos"),
                }
            )

        return results

    def search(
        self,
        *,
        query: str,
        top_k: int,
        include_scores: bool,
        budget_ms: int | None = None,
    ) -> SearchResponse:
        started = time.perf_counter()
        deadline = (
            started + budget_ms / 1000.0
            if budget_ms is not None
            else None
        )
        search_query = query
        prep_started = time.perf_counter()
        entity_matches: List[Dict[str, Any]] = []
        search_query, entity_matches = _augment_query_with_entities(
            search_query,
            getattr(self, "entity_dictionary", []),
        )
        if entity_matches:
            logger.info(
                "RAG entity expansion: matches=%s query=%r expanded=%r",
                [
                    {
                        "canonical": item.get("canonical"),
                        "matched_term": item.get("matched_term"),
                        "score": round(float(item.get("score") or 0.0), 3),
                    }
                    for item in entity_matches
                ],
                query,
                search_query,
            )
            search_query = _compact_query_with_entity_matches(query, entity_matches)
        fuzzy_alias = _fuzzy_person_alias(query, self._canonical_person_names)
        if fuzzy_alias is not None:
            heard_name, canonical_name, score = fuzzy_alias
            search_query = search_query.replace(heard_name, canonical_name, 1)
            logger.info(
                "RAG fuzzy person alias: heard=%r canonical=%r score=%.3f",
                heard_name,
                canonical_name,
                score,
            )
        latency_tracer.emit(
            "rag.query_prepare",
            duration_ms=(time.perf_counter() - prep_started) * 1000.0,
            critical_path=True,
            entity_matches=len(entity_matches),
            fuzzy_alias=bool(fuzzy_alias),
        )
        search_started = time.perf_counter()
        person_query = bool(
            _person_name_candidates(search_query)
            or any(item.get("type") == "person" for item in entity_matches)
            or fuzzy_alias is not None
        )
        priority_names: List[str] = []
        seen_priority_names: Set[str] = set()
        for name in [
            *[
                str(item.get("canonical") or "")
                for item in entity_matches
                if item.get("type") == "person"
            ],
            *( [fuzzy_alias[1]] if fuzzy_alias is not None else [] ),
            *_person_name_candidates(search_query),
        ]:
            clean_name = name.strip()
            key = _normalize_search_text(clean_name)
            if clean_name and key and key not in seen_priority_names:
                priority_names.append(clean_name)
                seen_priority_names.add(key)
        effective_top_k = top_k
        if person_query:
            effective_top_k = max(
                top_k,
                getattr(self, "person_query_min_top_k", 4),
            )
        lexical_fast_path_min_score = max(
            getattr(self, "lexical_fast_path_min_score", 0.68),
            0.80,
        )
        if not entity_matches and fuzzy_alias is None:
            lexical_fast_path_min_score = max(lexical_fast_path_min_score, 0.88)
        points = self._search_points(
            search_query,
            effective_top_k,
            deadline=deadline,
            lexical_fast_path_min_score=lexical_fast_path_min_score,
            priority_names=priority_names,
        )
        latency_tracer.emit(
            "rag.search_points",
            duration_ms=(time.perf_counter() - search_started) * 1000.0,
            critical_path=True,
            top_k=effective_top_k,
            requested_top_k=top_k,
            hits=len(points),
            budget_ms=budget_ms,
        )

        context_started = time.perf_counter()
        hits: List[SearchHit] = []
        context_parts: List[str] = []
        if fuzzy_alias is not None:
            heard_name, canonical_name, score = fuzzy_alias
            fuzzy_instruction = (
                "FUZZY NAME MATCH: Korisnik je rekao "
                f"'{heard_name}'. Baza sadrži veoma sličan kanonski naziv "
                f"'{canonical_name}' (score={score:.2f}). Odgovori za "
                f"'{canonical_name}' i kratko naznači pretpostavku."
            )
            context_parts.append(fuzzy_instruction)
        if entity_matches:
            entity_instruction = (
                "ENTITY MATCHES: "
                + "; ".join(
                    f"{item.get('matched_term')} -> {item.get('canonical')}"
                    for item in entity_matches
                )
                + ". Koristi ove kanonske entitete samo ako retrieved kontekst sadrži odgovor."
            )
            context_parts.append(entity_instruction)

        context_chars = sum(len(part) for part in context_parts)
        for idx, (point, profile) in enumerate(points, start=1):
            payload = dict(point.payload or {})
            payload["index_slug"] = profile.slug
            payload["index_title"] = profile.title
            payload["collection_name"] = profile.collection_name
            text = self._payload_text(payload)
            source = payload.get("filename") or payload.get("document_id")

            block = ["-----"]
            metadata_line = " | ".join(
                [
                    f"Index: {profile.title}",
                    *[
                        str(payload[field])
                        for field in self.metadata_fields
                        if payload.get(field) is not None
                    ],
                ]
            )
            if metadata_line:
                block.append(metadata_line)
            if text:
                block.append(text)
            context_block = "\n".join(block)
            remaining = self.context_max_chars - context_chars
            if remaining > 0:
                if len(context_block) > remaining:
                    clipped = context_block[:remaining]
                    boundary = clipped.rfind(" ")
                    if boundary >= max(80, remaining // 2):
                        clipped = clipped[:boundary]
                    context_block = clipped.rstrip() + "…"
                context_parts.append(context_block)
                context_chars += len(context_block)

            hits.append(
                SearchHit(
                    rank=idx,
                    score=(
                        float(payload.get("hybrid_score") or point.score or 0.0)
                        if include_scores
                        else None
                    ),
                    source=str(source) if source else None,
                    text=text,
                    payload=payload,
                )
            )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        latency_tracer.emit(
            "rag.context_assembly",
            duration_ms=(time.perf_counter() - context_started) * 1000.0,
            critical_path=True,
            context_chars=len("\n".join(context_parts)),
            hits=len(hits),
        )
        latency_tracer.emit(
            "rag.total",
            duration_ms=elapsed_ms,
            critical_path=True,
            top_k=top_k,
            hits=len(hits),
            budget_ms=budget_ms,
        )
        return SearchResponse(
            query=query,
            top_k=top_k,
            elapsed_ms=elapsed_ms,
            context="\n".join(context_parts),
            hits=hits,
        )

    def create_index(self, payload: CreateIndexRequest) -> IndexProfile:
        if payload.slug == MAIN_INDEX_SLUG:
            raise HTTPException(status_code=409, detail="The main knowledge index is protected and cannot be created or changed")

        if any(item.slug == payload.slug for item in self._all_indexes(force_refresh=True)):
            raise HTTPException(status_code=409, detail=f"Index '{payload.slug}' already exists")

        collection_name = (payload.collection_name or (
            "robot_knowledge" if payload.slug == "main" else f"robot_knowledge_{payload.slug}"
        )).strip()
        if any(item.collection_name == collection_name for item in self._all_indexes(force_refresh=True)):
            raise HTTPException(
                status_code=409,
                detail=f"Collection '{collection_name}' is already available as a knowledge index",
            )

        storage_path = None
        if payload.kind == "managed":
            default_storage = self.data_root / "knowledge" / payload.slug
            storage_path = self._normalize_storage_path(payload.storage_path or str(default_storage))

        profile = IndexProfile(
            slug=payload.slug,
            title=payload.title,
            description=payload.description,
            kind=payload.kind,
            collection_name=collection_name,
            storage_path=storage_path,
            immutable=payload.slug in self.immutable_index_slugs,
        )

        if profile.kind == "managed":
            Path(profile.storage_path).mkdir(parents=True, exist_ok=True)
            self._ensure_collection(profile)

        self.state.indexes.append(profile)
        self._discovered_indexes_cache = []
        self._discovered_indexes_cache_ts = 0.0
        self._save_state()
        return profile

    def delete_index(self, slug: str) -> Dict[str, Any]:
        profile_index = next((idx for idx, item in enumerate(self.state.indexes) if item.slug == slug), None)
        if profile_index is None:
            discovered_profile = next((item for item in self._discover_indexes(force_refresh=True) if item.slug == slug), None)
            if discovered_profile:
                if discovered_profile.immutable or discovered_profile.slug == MAIN_INDEX_SLUG:
                    raise HTTPException(status_code=409, detail=f"Knowledge index '{slug}' is immutable and cannot be deleted")

                deleted_collection = False
                try:
                    self._qdrant.delete_collection(discovered_profile.collection_name)
                    deleted_collection = True
                except Exception as exc:
                    logger.warning("Failed deleting Qdrant collection %s: %s", discovered_profile.collection_name, exc)
                self._invalidate_lexical_cache(discovered_profile.collection_name)

                self.state.query_slugs = [query_slug for query_slug in self.state.query_slugs if query_slug != slug]
                self._discovered_indexes_cache = []
                self._discovered_indexes_cache_ts = 0.0
                self._normalize_query_slugs()
                self._save_state()
                return {
                    "success": True,
                    "deleted": discovered_profile.model_dump(),
                    "active": self.get_active_index().model_dump(),
                    "deleted_collection": deleted_collection,
                    "deleted_storage": False,
                }

            raise HTTPException(status_code=404, detail=f"Unknown configured knowledge index: {slug}")

        if len(self.state.indexes) <= 1:
            raise HTTPException(status_code=409, detail="Cannot delete the only configured knowledge index")

        profile = self.state.indexes[profile_index]
        if profile.immutable:
            raise HTTPException(status_code=409, detail=f"Knowledge index '{slug}' is immutable and cannot be deleted")

        deleted_collection = False
        deleted_storage = False

        try:
            self._qdrant.delete_collection(profile.collection_name)
            deleted_collection = True
        except Exception as exc:
            logger.warning("Failed deleting Qdrant collection %s: %s", profile.collection_name, exc)
        self._invalidate_lexical_cache(profile.collection_name)

        if profile.kind == "managed" and profile.storage_path:
            storage_path = Path(profile.storage_path).resolve()
            try:
                if storage_path.exists():
                    shutil.rmtree(storage_path)
                    deleted_storage = True
            except Exception as exc:
                logger.warning("Failed deleting storage path %s: %s", storage_path, exc)

        del self.state.indexes[profile_index]
        if self.state.active_slug == slug:
            self.state.active_slug = self.state.indexes[0].slug
            active = self.activate_index(self.state.active_slug, persist=False)
        else:
            active = self.get_active_index()

        self._discovered_indexes_cache = []
        self._discovered_indexes_cache_ts = 0.0
        self._normalize_query_slugs()
        self._save_state()
        return {
            "success": True,
            "deleted": profile.model_dump(),
            "active": active.model_dump(),
            "deleted_collection": deleted_collection,
            "deleted_storage": deleted_storage,
        }

    def activate_index(self, slug: str, *, persist: bool = True) -> IndexProfile:
        profile = self._get_index_by_slug(slug, force_refresh=True)

        if profile.kind == "managed":
            Path(profile.storage_path).mkdir(parents=True, exist_ok=True)
        self._ensure_collection(profile)

        self.state.active_slug = profile.slug
        if persist:
            self._save_state()
            self._refresh_canonical_person_names(self.get_query_indexes())
        return profile
    
app = FastAPI(title="Local External RAG Service", version="2.0.0")
_svc: Optional[LocalRAGService] = None


def _get_service() -> LocalRAGService:
    if _svc is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return _svc


@app.on_event("startup")
def _startup() -> None:
    global _svc
    _svc = LocalRAGService()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    svc = _get_service()
    active = svc.get_active_index()
    return HealthResponse(
        status="ok",
        active_index=active,
        model=svc.model_name,
        device=svc.device,
        collection=active.collection_name,
        storage_path=active.storage_path,
        index_kind=active.kind,
    )


@app.get("/indexes", response_model=IndexState)
def get_indexes() -> IndexState:
    return _get_service().list_indexes()

@app.get("/indexes/active", response_model=IndexProfile)
def get_active_index() -> IndexProfile:
    return _get_service().get_active_index()

@app.post("/indexes/create", response_model=IndexProfile)
def create_index(payload: CreateIndexRequest) -> IndexProfile:
    try:
        return _get_service().create_index(payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Create index failed")
        raise HTTPException(status_code=500, detail=f"create_index_failed: {exc}") from exc

@app.post("/indexes/activate", response_model=IndexProfile)
def activate_index(payload: ActivateIndexRequest) -> IndexProfile:
    try:
        return _get_service().activate_index(payload.slug)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Activate index failed")
        raise HTTPException(status_code=500, detail=f"activate_index_failed: {exc}") from exc

@app.post("/indexes/query", response_model=IndexState)
def update_query_indexes(payload: QueryIndexesRequest) -> IndexState:
    try:
        return _get_service().update_query_indexes(payload.slugs)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Update query indexes failed")
        raise HTTPException(status_code=500, detail=f"update_query_indexes_failed: {exc}") from exc

@app.delete("/indexes/{slug}")
def delete_index(slug: str) -> Dict[str, Any]:
    try:
        return _get_service().delete_index(slug)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Delete index failed")
        raise HTTPException(status_code=500, detail=f"delete_index_failed: {exc}") from exc

@app.get("/knowledge", response_model=KnowledgeState)
def get_knowledge() -> KnowledgeState:
    return _get_service().list_documents()


@app.post("/knowledge/upload", response_model=DocumentInfo)
async def upload_knowledge(file: UploadFile = File(...)) -> DocumentInfo:
    try:
        return await _get_service().upload_document(file)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Upload failed")
        raise HTTPException(status_code=500, detail=f"upload_failed: {exc}") from exc


@app.delete("/knowledge/{doc_id}")
def delete_knowledge(doc_id: str) -> Dict[str, Any]:
    try:
        return _get_service().delete_document(doc_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Delete failed")
        raise HTTPException(status_code=500, detail=f"delete_failed: {exc}") from exc


@app.get("/knowledge/{doc_id}/file")
def get_knowledge_file(doc_id: str):
    """Return the raw PDF file for a document id if available."""
    try:
        svc = _get_service()
        pdf_path = Path(svc._pdf_path(doc_id))
        meta_path = Path(svc._metadata_path(doc_id))

        if not pdf_path.exists():
            raise HTTPException(status_code=404, detail="Document file not found")

        filename = None
        try:
            if meta_path.exists():
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                filename = str(raw.get("filename") or "").strip() or None
        except Exception:
            filename = None

        if filename:
            return FileResponse(
                pdf_path,
                media_type="application/pdf",
                filename=filename,
                content_disposition_type="inline",
            )
        return FileResponse(pdf_path, media_type="application/pdf")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to serve document file %s: %s", doc_id, exc)
        raise HTTPException(status_code=500, detail=f"failed_serving_file: {exc}") from exc



@app.post("/knowledge/index")
def reindex_knowledge() -> Dict[str, Any]:
    try:
        return _get_service().reindex_all_documents()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Re-index failed")
        raise HTTPException(status_code=500, detail=f"reindex_failed: {exc}") from exc



@app.get("/knowledge/search")
def search_knowledge(
    query: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=50),
) -> Dict[str, Any]:
    try:
        return {"results": _get_service().search_knowledge(query=query.strip(), limit=limit)}
    except Exception as exc:
        logger.exception("Knowledge search failed")
        raise HTTPException(status_code=500, detail=f"knowledge_search_failed: {exc}") from exc


@app.post("/v1/search", response_model=SearchResponse)
def search(payload: SearchRequest) -> SearchResponse:
    svc = _get_service()
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query must not be empty")

    top_k = payload.top_k or svc.default_top_k

    try:
        return svc.search(
            query=query,
            top_k=top_k,
            include_scores=payload.include_scores,
            budget_ms=payload.budget_ms,
        )
    except Exception as exc:
        logger.exception("Agent search failed")
        raise HTTPException(status_code=500, detail=f"search_failed: {exc}") from exc
