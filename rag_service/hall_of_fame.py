"""Hybrid BGE-M3 retrieval for the standalone Hall of Fame collection."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Sequence

import torch
from huggingface_hub import hf_hub_download
from qdrant_client import QdrantClient, models
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer


COLLECTION_NAME = "hall_of_fame"
PANEL_COUNT = 40
NARRATIVE_COUNT = 7
RECORD_COUNT = PANEL_COUNT + NARRATIVE_COUNT
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
DENSE_SIZE = 1024
DEFAULT_EMBED_MODEL = "BAAI/bge-m3"
DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_CERN_SPELLED_RE = re.compile(
    r"\b(?:c|see|sea)[\s.,_-]+(?:e|ee)[\s.,_-]+(?:r|are)[\s.,_-]+(?:n|en|in)\b\.?",
    re.IGNORECASE,
)
_CERN_PHONETIC_RE = re.compile(r"\b(?:cern|sern|sirn)\b", re.IGNORECASE)
_FOUNDER_INTENT_RE = re.compile(
    r"\b(?:who\s+(?:founded|started|created)|founder\s+of|the\s+founder)\b",
    re.IGNORECASE,
)
_PANEL_NUMBER_RE = re.compile(
    r"(?:\bpanel\s*(?:number\s*)?(\d{1,2})(?:st|nd|rd|th)?\b|"
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s*\.?\s*panel\b)",
    re.IGNORECASE,
)
_BEGINNINGS_PANEL_RE = re.compile(r"\b(?:the\s+)?beginnings?\b", re.IGNORECASE)
_GAMING_PANEL_RE = re.compile(
    r"\b(?:gaming|gambling|casino|slot(?:s| machines?)?|bally'?s|amatic|dafabet|genting)\b",
    re.IGNORECASE,
)
_CSI_ACHIEVEMENTS_PANEL_RE = re.compile(
    r"(?:"
    r"(?=.*\b(?:comtrade\s+)?(?:system\s+integration|csi)\b)"
    r"(?=.*\b(?:achievement|achievements|award|awards|recognition|microsoft|mssp|vajfert)\b)"
    r"|\b(?:microsoft\s+partner\s+of\s+the\s+year|mssp\s+alert|vajfert)\b"
    r")",
    re.IGNORECASE,
)

_CARDINAL_ONES = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine",
}
_ORDINAL_ONES = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth",
}
_CARDINAL_TENS = {10: "ten", 20: "twenty", 30: "thirty", 40: "forty"}
_ORDINAL_TENS = {10: "tenth", 20: "twentieth", 30: "thirtieth", 40: "fortieth"}


def embedding_text(record: dict[str, Any]) -> str:
    """Return the one and only text representation used during ingestion."""
    return "\n".join(str(record.get(key, "")) for key in ("context_prefix", "spoken", "detail"))


def normalize_retrieval_question(question: str) -> str:
    """Canonicalize common CERN spellings only in the private retrieval query."""
    normalized, spelled_count = _CERN_SPELLED_RE.subn("CERN", question)
    normalized, phonetic_count = _CERN_PHONETIC_RE.subn("CERN", normalized)
    if spelled_count or phonetic_count:
        return f"{normalized}\nCERN openlab Large Hadron Collider"
    if _FOUNDER_INTENT_RE.search(normalized):
        return f"{normalized}\nComtrade founder Veselin Jevrosimovic beginnings how it all started"
    return question


def _number_word(number: int, *, ordinal: bool) -> str:
    if number in (_ORDINAL_TENS if ordinal else _CARDINAL_TENS):
        return (_ORDINAL_TENS if ordinal else _CARDINAL_TENS)[number]
    if number < 10:
        return (_ORDINAL_ONES if ordinal else _CARDINAL_ONES)[number]
    tens = (number // 10) * 10
    ones = number % 10
    tens_word = _CARDINAL_TENS[tens]
    ones_word = (_ORDINAL_ONES if ordinal else _CARDINAL_ONES)[ones]
    return f"{tens_word} {ones_word}"


def panel_ordinal_aliases(sequence: int) -> list[str]:
    """Return stable numeric and spoken-English aliases for a wall panel."""
    if sequence < 1 or sequence > PANEL_COUNT:
        raise ValueError(f"Hall of Fame panel sequence must be between 1 and {PANEL_COUNT}")
    cardinal = _number_word(sequence, ordinal=False)
    ordinal = _number_word(sequence, ordinal=True)
    suffix = "th" if 10 <= sequence % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(sequence % 10, "th")
    return [
        f"panel {sequence}",
        f"{sequence}. panel",
        f"{sequence}{suffix} panel",
        f"panel {cardinal}",
        f"{ordinal} panel",
        f"the {ordinal} panel",
        *(
            ["last panel", "the last panel", "final panel", "the final panel"]
            if sequence == PANEL_COUNT
            else []
        ),
    ]


def panel_sequence_from_question(question: str) -> int | None:
    """Extract an explicit Hall of Fame panel number without semantic search."""
    normalized = re.sub(r"[-_]", " ", question.lower())
    if re.search(r"\b(?:the\s+)?(?:last|final)\s+panel\b", normalized):
        return PANEL_COUNT
    numeric = _PANEL_NUMBER_RE.search(normalized)
    if numeric:
        sequence = int(numeric.group(1) or numeric.group(2))
        return sequence if 1 <= sequence <= PANEL_COUNT else None
    # Long compound forms must be checked before their suffixes (for example,
    # "twenty first panel" before "first panel").
    for sequence in range(PANEL_COUNT, 0, -1):
        cardinal = re.escape(_number_word(sequence, ordinal=False))
        ordinal = re.escape(_number_word(sequence, ordinal=True))
        if re.search(
            rf"\b(?:panel\s+(?:number\s+)?{cardinal}|(?:the\s+)?{ordinal}\s+panel)\b",
            normalized,
        ):
            return sequence
    return None


def with_panel_ordinal_aliases(record: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(record)
    if enriched.get("type") != "panel":
        return enriched
    aliases = [str(alias) for alias in enriched.get("aliases", []) if str(alias).strip()]
    for alias in panel_ordinal_aliases(int(enriched["sequence"])):
        if alias not in aliases:
            aliases.append(alias)
    enriched["aliases"] = aliases
    return enriched


def retrieved_panel_context(record: dict[str, Any]) -> str:
    """Format the best Hall of Fame record for the current visitor question."""
    lines = ["[RETRIEVED PANEL FOR THIS TURN]"]
    sequence = record.get("sequence")
    if sequence is not None:
        lines.append(f"sequence: {sequence}")
    for label, key in (("title", "title"), ("narration", "spoken")):
        value = str(record.get(key, "")).strip()
        if value:
            lines.append(f"{label}: {value}")

    detail_parts = []
    for key in ("detail", "source_text"):
        value = str(record.get(key, "")).strip()
        if value and value not in detail_parts:
            detail_parts.append(value)
    if detail_parts:
        lines.append(f"detail: {' '.join(detail_parts)}")

    story = str(record.get("story", "")).strip()
    if story:
        lines.append(f"story: {story}")
    neighbors = record.get("neighbors")
    if neighbors:
        if isinstance(neighbors, (list, tuple)):
            neighbor_text = " | ".join(str(item).strip() for item in neighbors if str(item).strip())
        else:
            neighbor_text = str(neighbors).strip()
        if neighbor_text:
            lines.append(f"neighbors: {neighbor_text}")
    return "\n".join(lines)


# Backward-compatible import for callers outside this repository. New runtime
# code uses retrieved_panel_context so the panel is not treated as a lock.
active_panel_context = retrieved_panel_context


def _api_key() -> str | None:
    return os.getenv("RAG_QDRANT_API_KEY") or os.getenv("QDRANT_API_KEY") or None


def make_qdrant_client() -> QdrantClient:
    return QdrantClient(
        url=os.getenv("RAG_QDRANT_URL", "http://127.0.0.1:6333"),
        api_key=_api_key(),
        timeout=float(os.getenv("RAG_QDRANT_TIMEOUT_SECONDS", "15")),
    )


def _model_file(model_name: str, filename: str, offline: bool) -> str:
    local_path = Path(model_name).expanduser()
    if local_path.exists():
        candidate = local_path / filename
        if not candidate.is_file():
            raise FileNotFoundError(f"BGE-M3 model is missing required file: {candidate}")
        return str(candidate)
    return hf_hub_download(model_name, filename, local_files_only=offline)


class BgeM3HybridEncoder:
    """Dense mean-pooling plus the official BGE-M3 lexical-weight head."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_EMBED_MODEL,
        device: str = "cpu",
        max_length: int = 256,
        offline: bool = False,
        tokenizer: Any | None = None,
        model: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.max_length = max_length
        local_only = offline or Path(model_name).expanduser().exists()
        self.tokenizer = tokenizer or AutoTokenizer.from_pretrained(
            model_name, local_files_only=local_only
        )
        self.model = model or AutoModel.from_pretrained(
            model_name, local_files_only=local_only
        ).eval().to(device)
        hidden_size = int(self.model.config.hidden_size)
        if hidden_size != DENSE_SIZE:
            raise ValueError(f"BGE-M3 hidden size must be {DENSE_SIZE}; got {hidden_size}")

        self.sparse_linear = torch.nn.Linear(hidden_size, 1)
        sparse_path = _model_file(model_name, "sparse_linear.pt", offline)
        state = torch.load(sparse_path, map_location="cpu", weights_only=True)
        self.sparse_linear.load_state_dict(state)
        self.sparse_linear.eval().to(device)
        self._special_ids = {int(value) for value in self.tokenizer.all_special_ids}

    @torch.no_grad()
    def encode(self, texts: Sequence[str], batch_size: int = 16) -> tuple[list[list[float]], list[models.SparseVector]]:
        dense_vectors: list[list[float]] = []
        sparse_vectors: list[models.SparseVector] = []

        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(self.device) for key, value in encoded.items()}
            hidden = self.model(**encoded).last_hidden_state

            mask = encoded["attention_mask"].unsqueeze(-1).expand(hidden.size()).float()
            mean = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
            mean = torch.nn.functional.normalize(mean, p=2, dim=1)
            dense_vectors.extend(mean.detach().cpu().tolist())

            token_weights = torch.relu(self.sparse_linear(hidden)).squeeze(-1)
            for token_ids, weights, attention in zip(
                encoded["input_ids"], token_weights, encoded["attention_mask"]
            ):
                lexical: dict[int, float] = {}
                for token_id, weight, visible in zip(token_ids.tolist(), weights.tolist(), attention.tolist()):
                    token_id = int(token_id)
                    value = float(weight)
                    if not visible or token_id in self._special_ids or value <= 0.0:
                        continue
                    lexical[token_id] = max(lexical.get(token_id, 0.0), value)
                indices = sorted(lexical)
                sparse_vectors.append(
                    models.SparseVector(indices=indices, values=[lexical[index] for index in indices])
                )

        return dense_vectors, sparse_vectors

    def encode_one(self, text: str) -> tuple[list[float], models.SparseVector]:
        dense, sparse = self.encode([text])
        return dense[0], sparse[0]


class CrossEncoderReranker:
    def __init__(self, *, model_name: str, device: str, offline: bool) -> None:
        local_only = offline or Path(model_name).expanduser().exists()
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=local_only)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_name, local_files_only=local_only
        ).eval().to(device)

    @torch.no_grad()
    def scores(self, question: str, records: Sequence[dict[str, Any]]) -> list[float]:
        pairs = [[question, embedding_text(record)] for record in records]
        encoded = self.tokenizer(
            pairs,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        encoded = {key: value.to(self.device) for key, value in encoded.items()}
        logits = self.model(**encoded).logits
        if logits.shape[-1] == 1:
            return logits[:, 0].detach().cpu().tolist()
        return logits[:, -1].detach().cpu().tolist()


class HallOfFameStore:
    def __init__(
        self,
        *,
        client: QdrantClient | None = None,
        encoder: BgeM3HybridEncoder | None = None,
        reranker: Any | None = None,
        device: str | None = None,
        offline: bool | None = None,
    ) -> None:
        requested_device = device or os.getenv("RAG_DEVICE", "cpu")
        self.device = "cuda" if requested_device == "cuda" and torch.cuda.is_available() else "cpu"
        self.offline = (
            offline
            if offline is not None
            else os.getenv("RAG_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}
        )
        self.client = client or make_qdrant_client()
        self.encoder = encoder
        self.reranker = reranker

    def _encoder(self) -> BgeM3HybridEncoder:
        if self.encoder is None:
            self.encoder = BgeM3HybridEncoder(
                model_name=os.getenv("RAG_EMBED_MODEL", DEFAULT_EMBED_MODEL),
                device=self.device,
                max_length=max(64, int(os.getenv("RAG_MAX_LENGTH", "256"))),
                offline=self.offline,
            )
        return self.encoder

    def _reranker(self) -> Any:
        if self.reranker is None:
            self.reranker = CrossEncoderReranker(
                model_name=os.getenv("RAG_HOF_RERANK_MODEL", DEFAULT_RERANK_MODEL),
                device=os.getenv("RAG_HOF_RERANK_DEVICE", self.device),
                offline=self.offline,
            )
        return self.reranker

    def recreate(self) -> None:
        if self.client.collection_exists(COLLECTION_NAME):
            self.client.delete_collection(COLLECTION_NAME)
        self.client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                DENSE_VECTOR_NAME: models.VectorParams(size=DENSE_SIZE, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={SPARSE_VECTOR_NAME: models.SparseVectorParams()},
        )
        for field, schema in (
            ("id", models.PayloadSchemaType.KEYWORD),
            ("type", models.PayloadSchemaType.KEYWORD),
            ("panel_group", models.PayloadSchemaType.KEYWORD),
            ("era", models.PayloadSchemaType.KEYWORD),
            ("sequence", models.PayloadSchemaType.INTEGER),
            ("trigger_keywords", models.PayloadSchemaType.KEYWORD),
        ):
            self.client.create_payload_index(COLLECTION_NAME, field, schema, wait=True)

    def ingest(self, records: Sequence[dict[str, Any]], batch_size: int = 16) -> None:
        records = [with_panel_ordinal_aliases(record) for record in records]
        sequences = [int(record["sequence"]) for record in records]
        ids = [str(record["id"]) for record in records]
        if (
            len(records) != RECORD_COUNT
            or len(set(sequences)) != RECORD_COUNT
            or len(set(ids)) != RECORD_COUNT
        ):
            raise ValueError(
                f"Expected exactly {RECORD_COUNT} records with unique sequence and id values"
            )
        if sum(record.get("type") == "panel" for record in records) != PANEL_COUNT:
            raise ValueError(f"Expected exactly {PANEL_COUNT} panel records")
        if sum(record.get("type") == "narrative" for record in records) != NARRATIVE_COUNT:
            raise ValueError(f"Expected exactly {NARRATIVE_COUNT} narrative records")

        self.recreate()
        for start in range(0, len(records), batch_size):
            batch = list(records[start : start + batch_size])
            dense, sparse = self._encoder().encode([embedding_text(record) for record in batch])
            points = [
                models.PointStruct(
                    id=int(record["sequence"]),
                    vector={DENSE_VECTOR_NAME: dense_vector, SPARSE_VECTOR_NAME: sparse_vector},
                    payload=dict(record),
                )
                for record, dense_vector, sparse_vector in zip(batch, dense, sparse)
            ]
            self.client.upsert(COLLECTION_NAME, points=points, wait=True)
        self.verify_counts()

    def verify_counts(self) -> None:
        expected = {None: RECORD_COUNT, "panel": PANEL_COUNT, "narrative": NARRATIVE_COUNT}
        for record_type, count in expected.items():
            query_filter = None
            if record_type:
                query_filter = models.Filter(
                    must=[models.FieldCondition(key="type", match=models.MatchValue(value=record_type))]
                )
            actual = self.client.count(
                COLLECTION_NAME, count_filter=query_filter, exact=True
            ).count
            if actual != count:
                raise RuntimeError(f"Hall of Fame count mismatch for {record_type or 'all'}: {actual} != {count}")

    def activate_panel(self, panel_id: str) -> dict[str, Any] | None:
        response = self.client.query_points(
            COLLECTION_NAME,
            query_filter=models.Filter(
                must=[models.FieldCondition(key="id", match=models.MatchValue(value=panel_id))]
            ),
            limit=1,
            with_payload=True,
        )
        return dict(response.points[0].payload or {}) if response.points else None

    def voice_search(self, question: str, k: int = 3) -> Any | None:
        explicit_sequence = panel_sequence_from_question(question)
        if explicit_sequence is None and _BEGINNINGS_PANEL_RE.search(question):
            explicit_sequence = 1
        if explicit_sequence is None and _GAMING_PANEL_RE.search(question):
            explicit_sequence = 23
        if explicit_sequence is None and _CSI_ACHIEVEMENTS_PANEL_RE.search(question):
            explicit_sequence = 37
        if explicit_sequence is not None:
            response = self.client.query_points(
                COLLECTION_NAME,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="type", match=models.MatchValue(value="panel")),
                        models.FieldCondition(key="sequence", match=models.MatchValue(value=explicit_sequence)),
                    ]
                ),
                limit=1,
                with_payload=True,
            )
            return response.points[0] if response.points else None
        retrieval_question = normalize_retrieval_question(question)
        dense, sparse = self._encoder().encode_one(retrieval_question)
        panel_filter = models.Filter(
            must=[models.FieldCondition(key="type", match=models.MatchValue(value="panel"))]
        )
        response = self.client.query_points(
            COLLECTION_NAME,
            prefetch=[
                models.Prefetch(query=dense, using=DENSE_VECTOR_NAME, limit=20, filter=panel_filter),
                models.Prefetch(query=sparse, using=SPARSE_VECTOR_NAME, limit=20, filter=panel_filter),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=max(1, k),
            with_payload=True,
        )
        if not response.points:
            return None
        records = [dict(point.payload or {}) for point in response.points]
        scores = self._reranker().scores(retrieval_question, records)
        best = max(range(len(response.points)), key=lambda index: scores[index])
        return response.points[best]


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}") from exc
    return records


def activate_panel(panel_id: str) -> dict[str, Any] | None:
    return HallOfFameStore().activate_panel(panel_id)


def voice_search(question: str, k: int = 3) -> dict[str, Any] | None:
    point = HallOfFameStore().voice_search(question, k=k)
    return dict(point.payload or {}) if point is not None else None
