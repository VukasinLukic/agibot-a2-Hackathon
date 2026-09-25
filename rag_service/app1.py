#!/usr/bin/env python3
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from transformers import AutoModel, AutoTokenizer
import torch

from datetime import datetime
import json
import uuid
import fitz
from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue, VectorParams, Distance


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("rag-service")


def _has_model_weights(model_path: Path) -> bool:
    weight_filenames = (
        "pytorch_model.bin",
        "model.safetensors",
        "tf_model.h5",
        "model.ckpt.index",
        "flax_model.msgpack",
        # sharded checkpoints
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
    return "cpu"


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


class HealthResponse(BaseModel):
    status: str
    collection: str
    model: str
    device: str

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
        default_model_path = repo_root / "bge-m3-local"
        default_hf_model = "BAAI/bge-m3"

        self.qdrant_url = _env_str("RAG_QDRANT_URL", "http://127.0.0.1:6333")
        # self.collection = _env_str("RAG_QDRANT_COLLECTION", "kc-crm-chunks")
        self.collection = _env_str("RAG_QDRANT_COLLECTION", "robot_knowledge")


        # storage and local rag settings 
        self.storage_path = Path(_env_str("RAG_STORAGE_PATH", "/data/knowledge"))
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.chunk_size = max(200, _env_int("RAG_CHUNK_SIZE", 1500))
        self.chunk_overlap = max(0, _env_int("RAG_CHUNK_OVERLAP", 200))
        self.embedding_dim = int(self._model.config.hidden_size)


        configured_model = os.getenv("RAG_EMBED_MODEL")
        if configured_model and configured_model.strip():
            self.model_name = configured_model.strip()
        elif _has_model_weights(default_model_path):
            self.model_name = str(default_model_path)
        else:
            self.model_name = default_hf_model
            logger.warning(
                "Local model directory %s has no model weights; falling back to %s.",
                default_model_path,
                default_hf_model,
            )
        self.device = _resolve_device(_env_str("RAG_DEVICE", "cpu"))
        self.max_length = max(64, _env_int("RAG_MAX_LENGTH", 256))
        self.default_top_k = max(1, _env_int("RAG_TOP_K", 5))
        self.timeout_s = max(0.1, _env_float("RAG_QDRANT_TIMEOUT_SECONDS", 4.0))
        self.content_field = _env_str("RAG_CONTENT_FIELD", "chunk_content")
        self.metadata_fields = [
            field.strip()
            for field in _env_str(
                "RAG_METADATA_FIELDS",
                "source,url,filename,content_id,page_number,chunk_index",
            ).split(",")
            if field.strip()
        ]

        self._qdrant = QdrantClient(url=self.qdrant_url, timeout=self.timeout_s)
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModel.from_pretrained(self.model_name).eval().to(self.device)
        self._validate_collection()

        logger.info(
            "RAG service ready: qdrant=%s collection=%s model=%s device=%s top_k=%s",
            self.qdrant_url,
            self.collection,
            self.model_name,
            self.device,
            self.default_top_k,
        )

    """
    def _validate_collection(self) -> None:
        try:
            self._qdrant.get_collection(self.collection)
        except Exception as exc:
            raise RuntimeError(
                f"Qdrant collection not found or unavailable: {self.collection}"
            ) from exc
    """

    ## ensure collection exists 
    def _ensure_collection(self) -> None:
        try:
            self._qdrant.get_collection(self.collection)
        except Exception:
            self._qdrant.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=self.embedding_dim,
                    distance=Distance.COSINE,
                ),
            )


    @torch.no_grad()
    def _embed(self, text: str) -> List[float]:
        enc = self._tokenizer(
            [text],
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        enc = {k: v.to(self.device) for k, v in enc.items()}
        out = self._model(**enc)
        last = out.last_hidden_state

        mask = enc["attention_mask"].unsqueeze(-1).expand(last.size()).float()
        summed = (last * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1e-9)
        mean = summed / counts
        mean = torch.nn.functional.normalize(mean, p=2, dim=1)
        return mean[0].detach().cpu().tolist()

    def search(
        self,
        *,
        query: str,
        top_k: int,
        include_scores: bool,
    ) -> SearchResponse:
        started = time.perf_counter()

        vector = self._embed(query)
        points = self._qdrant.search(
            collection_name=self.collection,
            query_vector=vector,
            limit=top_k,
            with_payload=True,
        )

        hits: List[SearchHit] = []
        context_parts: List[str] = []
        for idx, point in enumerate(points, start=1):
            payload = point.payload or {}
            text = payload.get(self.content_field) or payload.get("content") or ""
            text = " ".join(str(text).split())
            source = (
                payload.get("url")
                or payload.get("source")
                or payload.get("filename")
                or payload.get("content_id")
            )

            block: List[str] = ["-----"]
            metadata_line = " | ".join(
                str(payload[field]) for field in self.metadata_fields if payload.get(field)
            )
            if metadata_line:
                block.append(metadata_line)
            if text:
                block.append(text)
            context_parts.append("\n".join(block))

            hits.append(
                SearchHit(
                    rank=idx,
                    score=float(point.score) if include_scores else None,
                    source=str(source) if source else None,
                    text=text,
                    payload=dict(payload),
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


app = FastAPI(title="Local External RAG Service", version="1.0.0")
_svc: Optional[LocalRAGService] = None


@app.on_event("startup")
def _startup() -> None:
    global _svc
    _svc = LocalRAGService()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    if _svc is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return HealthResponse(
        status="ok",
        collection=_svc.collection,
        model=_svc.model_name,
        device=_svc.device,
    )


@app.post("/v1/search", response_model=SearchResponse)
def search(payload: SearchRequest) -> SearchResponse:
    if _svc is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query must not be empty")
    top_k = payload.top_k or _svc.default_top_k
    try:
        return _svc.search(query=query, top_k=top_k, include_scores=payload.include_scores)
    except Exception as exc:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=f"search_failed: {exc}") from exc
