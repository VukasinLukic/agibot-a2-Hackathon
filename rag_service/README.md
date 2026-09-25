# Local External RAG Service

This service runs local retrieval against Qdrant and exposes a simple HTTP API for the LiveKit agent.

## Endpoints

- `GET /health`
- `POST /v1/search`

Request:

```json
{
  "query": "Kako zamenim vozacku dozvolu?",
  "top_k": 5,
  "include_scores": false
}
```

Response contains `context` (formatted text block to inject into LLM context) and `hits`.

## Run (Non-Docker, rag_torch)

From repository root:

```bash
bash rag_service/run_rag_api.sh
```

This launcher runs:

- Python interpreter: `rag_torch/bin/python` (override with `RAG_PYTHON_BIN`)
- Server: `uvicorn rag_service.app:app`
- Default bind: `0.0.0.0:8098` (override with `RAG_API_HOST` / `RAG_API_PORT`)

## Environment Variables

- `RAG_PYTHON_BIN` (default: `./rag_torch/bin/python`)
- `RAG_API_HOST` (default: `0.0.0.0`)
- `RAG_API_PORT` (default: `8098`)
- `RAG_QDRANT_URL` (default: `http://127.0.0.1:6333`)
- `RAG_QDRANT_COLLECTION` (default: `robot_knowledge`)
- `RAG_EMBED_MODEL` (recommended local path: `./bge-m3-local`)
- `RAG_OFFLINE` (`1` disables Hugging Face network access and requires local model files)
- `RAG_DEVICE` (`cpu` or `cuda`, default: `cpu`)
- `RAG_TOP_K` (default: `5`)
- `RAG_MAX_LENGTH` (default: `256`)
- `RAG_CHUNK_CHARS` (default: `2500`)
- `RAG_OVERLAP_CHARS` (default: `300`)

For offline use, place a full `bge-m3` model snapshot in `./bge-m3-local` and start the service with `RAG_OFFLINE=1`. If the directory is missing, startup now fails fast with a clear error instead of retrying requests to Hugging Face.

## Run With Docker Without Compose

This directory now also includes standalone Docker files and launchers so you can run each service separately with plain `docker`.

Start Qdrant first:

```bash
bash rag_service/run_qdrant_docker.sh
```

Then start the RAG API:

```bash
bash rag_service/run_rag_api_docker.sh
```

Check status:

```bash
docker ps
docker logs humanoid-qdrant
docker logs humanoid-rag-api
```

Stop them separately:

```bash
docker stop humanoid-rag-api
docker stop humanoid-qdrant
```

Notes:

- `run_qdrant_docker.sh` runs `qdrant/qdrant:latest` directly and exposes `6333`.
- `run_rag_api_docker.sh` builds from `Dockerfile.rag-api` and exposes `8098`.
- The API container defaults to `RAG_QDRANT_URL=http://host.docker.internal:6333`, so it talks to the Qdrant container through the host port instead of a Compose network.

## Hall of Fame hybrid index

The `hall_of_fame` collection is deliberately separate from the managed PDF
indexes. It uses named BGE-M3 vectors (`dense`, 1024/Cosine, and `sparse`,
lexical weights), and stores each complete JSONL record as payload. Normal voice
search does dense+sparse RRF candidate fusion and cross-encoder reranking, then
returns exactly one `type=panel` record. Panel activation by string `id` is a
deterministic payload lookup.

The implementation reuses the RAG service's Qdrant environment variables,
BGE-M3 model name/device/offline configuration, tokenizer/model instance when
called by the running API, and its dense mean-pooling plus L2 normalization.
The official BGE-M3 `sparse_linear.pt` lexical head and a small cross-encoder
reranker are loaded only for Hall of Fame retrieval.

From the repository root:

```bash
.venv-310/bin/python -m rag_service.hall_of_fame_cli ingest hall_of_fame_kb.jsonl
.venv-310/bin/python -m rag_service.hall_of_fame_cli smoke --strict
.venv-310/bin/python -m rag_service.hall_of_fame_cli activate panel-01-beginnings
.venv-310/bin/python -m rag_service.hall_of_fame_cli search "What is the Tesla brand?"
```

In Supervisor's Knowledge tab, select only `Hall Of Fame` under query indexes
for the isolated canary. This does not delete or alter other Qdrant collections.
The canonical dataset currently contains 40 panel records and 7 narrative records.
