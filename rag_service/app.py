#!/usr/bin/env python3
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Set

import fitz
import torch
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient, models
from transformers import AutoModel, AutoTokenizer
from pydantic import BaseModel, Field, model_validator

from rag_service.hall_of_fame import (
    COLLECTION_NAME as HALL_OF_FAME_COLLECTION,
    DENSE_VECTOR_NAME as HALL_OF_FAME_DENSE_VECTOR,
    BgeM3HybridEncoder,
    HallOfFameStore,
    retrieved_panel_context,
)


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("rag-service")

MAIN_INDEX_SLUG = "main"


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
        if _env_bool("RAG_STRICT_DEVICE", False):
            raise RuntimeError("RAG_DEVICE=cuda requested but CUDA is unavailable")
        logger.warning("RAG_DEVICE=cuda requested but CUDA unavailable; using CPU.")
    elif requested != "cpu":
        raise ValueError(f"Unsupported RAG_DEVICE={requested!r}; expected 'cpu' or 'cuda'")
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
        # Namespace for a shared Qdrant: only collections with this prefix are
        # created, discovered, queried or deleted by this service instance.
        self.collection_prefix = _env_str("RAG_COLLECTION_PREFIX", "").strip()
        default_data_root = Path(__file__).resolve().parent / "data"
        self.data_root = Path(_env_str("RAG_DATA_ROOT", str(default_data_root))).resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_root / "index_state.json"
        
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
        self.immutable_index_slugs = _env_csv("RAG_IMMUTABLE_INDEX_SLUGS", MAIN_INDEX_SLUG)
        self.immutable_index_slugs.add(MAIN_INDEX_SLUG)
        self.offline_mode = _env_bool("RAG_OFFLINE", False)
        self.local_model_only = self.offline_mode or Path(self.model_name).expanduser().exists()

        self.chunk_chars = max(200, _env_int("RAG_CHUNK_CHARS", 2500))
        self.overlap_chars = max(0, _env_int("RAG_OVERLAP_CHARS", 300))
        if self.overlap_chars >= self.chunk_chars:
            raise ValueError("RAG_OVERLAP_CHARS must be smaller than RAG_CHUNK_CHARS")



        self.metadata_fields = ["filename", "page_number", "chunk_index", "document_id"]

        if self.offline_mode:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        qdrant_api_key = os.getenv("RAG_QDRANT_API_KEY") or os.getenv("QDRANT_API_KEY") or None
        self._qdrant = QdrantClient(
            url=self.qdrant_url,
            api_key=qdrant_api_key,
            timeout=self.timeout_s,
        )
        self._discovered_indexes_cache: List[IndexProfile] = []
        self._discovered_indexes_cache_ts = 0.0
        model_load_kwargs = {"local_files_only": self.local_model_only}
        try:
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
        self._hall_of_fame_store: Optional[HallOfFameStore] = None




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

        logger.info(
            "RAG service ready: qdrant=%s active_slug=%s collection=%s model=%s device=%s storage=%s chunk=%s overlap=%s",
            self.qdrant_url,
            active.slug,
            active.collection_name,
            self.model_name,
            self.device,
            active.storage_path,
            self.chunk_chars,
            self.overlap_chars,
        )

    def _normalize_storage_path(self, raw_path: str) -> str:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (self.data_root / path).resolve()
        return str(path)
    
    def _default_collection_name(self, slug: str) -> str:
        base = "robot_knowledge" if slug == MAIN_INDEX_SLUG else f"robot_knowledge_{slug}"
        return f"{self.collection_prefix}{base}"

    def _collection_allowed(self, collection_name: str) -> bool:
        return not self.collection_prefix or collection_name.startswith(self.collection_prefix)

    def _require_allowed_collection(self, collection_name: str) -> None:
        if not self._collection_allowed(collection_name):
            raise HTTPException(
                status_code=400,
                detail=f"Collection '{collection_name}' must start with prefix '{self.collection_prefix}'",
            )

    def _bootstrap_state(self) -> IndexState:
        slug = _env_str("RAG_ACTIVE_INDEX_SLUG", "main")
        kind = _env_str("RAG_INDEX_KIND", "managed").strip().lower()
        if kind not in {"managed", "external"}:
            kind = "managed"

        collection_name = _env_str("RAG_QDRANT_COLLECTION", self._default_collection_name(slug))
        if not self._collection_allowed(collection_name):
            raise ValueError(
                f"RAG_QDRANT_COLLECTION '{collection_name}' must start with RAG_COLLECTION_PREFIX "
                f"'{self.collection_prefix}'"
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
                foreign = [item.slug for item in state.indexes if not self._collection_allowed(item.collection_name)]
                if foreign:
                    logger.warning(
                        "Ignoring indexes outside RAG_COLLECTION_PREFIX '%s': %s",
                        self.collection_prefix,
                        ", ".join(foreign),
                    )
                    state.indexes = [item for item in state.indexes if self._collection_allowed(item.collection_name)]
                if not state.indexes:
                    raise ValueError("No indexes in state file")
                if not state.active_slug:
                    state.active_slug = state.indexes[0].slug
                for item in state.indexes:
                    item.immutable = item.slug in self.immutable_index_slugs
                self.state = state
                self._normalize_query_slugs()
                self._save_state()
                return state
            except Exception as exc:
                logger.warning("Invalid index state file %s: %s", self.state_path, exc)

        self.state = self._bootstrap_state()
        self._save_state()
        return self.state

    def _normalize_query_slugs(self) -> None:
        available_slugs = {item.slug for item in self._all_indexes(force_refresh=True)}
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
            and self._discovered_indexes_cache
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
                if collection_name in configured_collections or not self._collection_allowed(collection_name):
                    continue
                discovered.append(self._make_discovered_index(collection_name, used_slugs))
        except Exception as exc:
            logger.warning("Failed to discover Qdrant collections: %s", exc)
            if self._discovered_indexes_cache:
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
            self._get_index_by_slug(slug, force_refresh=True)
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
        return self.list_indexes()

    @staticmethod
    def _collection_vector_size(collection_info: Any, vector_name: Optional[str] = None) -> Optional[int]:
        params = getattr(getattr(collection_info, "config", None), "params", None)
        vectors = getattr(params, "vectors", None)
        if isinstance(vectors, dict):
            vector = vectors.get(vector_name) if vector_name else None
            size = getattr(vector, "size", None)
            return int(size) if size is not None else None
        size = getattr(vectors, "size", None)
        return int(size) if size is not None else None

    def _validate_collection_vector_size(self, profile: IndexProfile, collection_info: Any) -> None:
        params = getattr(getattr(collection_info, "config", None), "params", None)
        vectors = getattr(params, "vectors", None)
        named_vectors = isinstance(vectors, dict)
        if named_vectors and profile.collection_name != HALL_OF_FAME_COLLECTION:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Qdrant collection '{profile.collection_name}' has an unsupported named-vector "
                    "configuration"
                ),
            )
        vector_name = HALL_OF_FAME_DENSE_VECTOR if named_vectors else None
        vector_size = self._collection_vector_size(collection_info, vector_name)
        if vector_size is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Qdrant collection '{profile.collection_name}' has an unsupported vector "
                    f"configuration; expected vectors with size={self.embedding_dim}"
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

    def get_document_file(self, doc_id: str) -> tuple[Path, str]:
        document = next((item for item in self._load_documents() if item.id == doc_id), None)
        if document is None:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        path = self._pdf_path(doc_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"PDF file for {doc_id} not found")
        return path, document.filename
    
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

    def _search_points(self, query: str, limit: int) -> List[tuple[Any, IndexProfile]]:
        vector: Optional[List[float]] = None
        results: List[tuple[Any, IndexProfile]] = []

        for profile in self.get_query_indexes():
            try:
                if profile.collection_name == HALL_OF_FAME_COLLECTION:
                    point = self._get_hall_of_fame_store().voice_search(
                        query,
                        k=max(3, limit),
                    )
                    if point is not None:
                        results.append((point, profile))
                    continue

                if vector is None:
                    vector = self._embed_texts([query])[0]
                query_points = getattr(self._qdrant, "query_points", None)
                if callable(query_points):
                    response = query_points(
                        collection_name=profile.collection_name,
                        query=vector,
                        limit=limit,
                        with_payload=True,
                    )
                    points = response.points
                else:
                    # qdrant-client < 1.16 compatibility.
                    points = self._qdrant.search(
                        collection_name=profile.collection_name,
                        query_vector=vector,
                        limit=limit,
                        with_payload=True,
                    )
            except Exception as exc:
                logger.warning("Search failed for knowledge index '%s': %s", profile.slug, exc)
                continue

            results.extend((point, profile) for point in points)

        results.sort(key=lambda item: float(item[0].score or 0.0), reverse=True)
        return results[:limit]

    def _get_hall_of_fame_store(self) -> HallOfFameStore:
        if self._hall_of_fame_store is None:
            encoder = BgeM3HybridEncoder(
                model_name=self.model_name,
                device=self.device,
                max_length=self.max_length,
                offline=self.offline_mode,
                tokenizer=self._tokenizer,
                model=self._model,
            )
            self._hall_of_fame_store = HallOfFameStore(
                client=self._qdrant,
                encoder=encoder,
                device=self.device,
                offline=self.offline_mode,
            )
        return self._hall_of_fame_store

    @staticmethod
    def _payload_text(payload: Dict[str, Any]) -> str:
        if payload.get("id") and payload.get("type") in {"panel", "narrative"}:
            parts = [
                str(payload.get(key, "")).strip()
                for key in ("spoken", "detail")
                if str(payload.get(key, "")).strip()
            ]
            return "\n".join(parts)
        for key in ("text", "chunk_content", "content", "chunk_text"):
            value = payload.get(key)
            if value is not None:
                text = " ".join(str(value).split())
                if text:
                    return text
        return ""

        
    
    def search_knowledge(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        points = self._search_points(query, limit)
        results: List[Dict[str, Any]] = []

        for point, profile in points:
            payload = dict(point.payload or {})
            text = self._payload_text(payload)
            results.append(
                {
                    "score": float(point.score) if point.score is not None else 0.0,
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

    def search(self, *, query: str, top_k: int, include_scores: bool) -> SearchResponse:
        started = time.perf_counter()
        points = self._search_points(query, top_k)

        hits: List[SearchHit] = []
        context_parts: List[str] = []

        for idx, (point, profile) in enumerate(points, start=1):
            payload = dict(point.payload or {})
            payload["index_slug"] = profile.slug
            payload["index_title"] = profile.title
            payload["collection_name"] = profile.collection_name
            text = self._payload_text(payload)
            source = payload.get("filename") or payload.get("document_id")

            if profile.collection_name == HALL_OF_FAME_COLLECTION:
                context_parts.append(retrieved_panel_context(payload))
            else:
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
                context_parts.append("\n".join(block))

            hits.append(
                SearchHit(
                    rank=idx,
                    score=float(point.score) if include_scores and point.score is not None else None,
                    source=str(source) if source else None,
                    text=text,
                    payload=payload,
                )
            )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
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

        collection_name = (payload.collection_name or self._default_collection_name(payload.slug)).strip()
        self._require_allowed_collection(collection_name)
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


@app.get("/knowledge/{doc_id}/file")
def get_knowledge_file(doc_id: str):
    path, filename = _get_service().get_document_file(doc_id)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="inline",
    )


@app.delete("/knowledge/{doc_id}")
def delete_knowledge(doc_id: str) -> Dict[str, Any]:
    try:
        return _get_service().delete_document(doc_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Delete failed")
        raise HTTPException(status_code=500, detail=f"delete_failed: {exc}") from exc



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
        return svc.search(query=query, top_k=top_k, include_scores=payload.include_scores)
    except Exception as exc:
        logger.exception("Agent search failed")
        raise HTTPException(status_code=500, detail=f"search_failed: {exc}") from exc
