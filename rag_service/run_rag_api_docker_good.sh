#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${RAG_API_IMAGE_NAME:-humanoid-rag-api}"
CONTAINER_NAME="${RAG_API_CONTAINER_NAME:-humanoid-rag-api}"
HOST_PORT="${RAG_API_HOST_PORT:-8098}"
CONTAINER_PORT="${RAG_API_CONTAINER_PORT:-8098}"
KNOWLEDGE_DIR="${RAG_STORAGE_HOST_DIR:-$SCRIPT_DIR/data/knowledge}"
HF_CACHE_DIR="${RAG_HF_CACHE_HOST_DIR:-$SCRIPT_DIR/data/hf-cache}"
MODEL_DIR="${RAG_MODEL_HOST_DIR:-$SCRIPT_DIR/../bge-m3-local}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
RAG_QDRANT_URL="${RAG_QDRANT_URL:-http://host.docker.internal:6333}"
RAG_QDRANT_COLLECTION="${RAG_QDRANT_COLLECTION:-robot_knowledge}"
RAG_STORAGE_PATH="${RAG_STORAGE_PATH:-/data/knowledge}"
RAG_EMBED_MODEL="${RAG_EMBED_MODEL:-/models/bge-m3-local}"
RAG_OFFLINE="${RAG_OFFLINE:-1}"
RAG_DEVICE="${RAG_DEVICE:-cpu}"
RAG_MAX_LENGTH="${RAG_MAX_LENGTH:-256}"
RAG_TOP_K="${RAG_TOP_K:-5}"
RAG_CHUNK_CHARS="${RAG_CHUNK_CHARS:-2500}"
RAG_OVERLAP_CHARS="${RAG_OVERLAP_CHARS:-300}"
HF_HOME="${HF_HOME:-/hf-cache}"

mkdir -p "$KNOWLEDGE_DIR" "$HF_CACHE_DIR"

docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR/Dockerfile.rag-api" "$SCRIPT_DIR"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

exec docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p "$HOST_PORT:$CONTAINER_PORT" \
  --add-host host.docker.internal:host-gateway \
  -e LOG_LEVEL="$LOG_LEVEL" \
  -e RAG_QDRANT_URL="$RAG_QDRANT_URL" \
  -e RAG_QDRANT_COLLECTION="$RAG_QDRANT_COLLECTION" \
  -e RAG_STORAGE_PATH="$RAG_STORAGE_PATH" \
  -e RAG_EMBED_MODEL="$RAG_EMBED_MODEL" \
  -e RAG_OFFLINE="$RAG_OFFLINE" \
  -e RAG_DEVICE="$RAG_DEVICE" \
  -e RAG_MAX_LENGTH="$RAG_MAX_LENGTH" \
  -e RAG_TOP_K="$RAG_TOP_K" \
  -e RAG_CHUNK_CHARS="$RAG_CHUNK_CHARS" \
  -e RAG_OVERLAP_CHARS="$RAG_OVERLAP_CHARS" \
  -e HF_HOME="$HF_HOME" \
  -v "$KNOWLEDGE_DIR:/data/knowledge" \
  -v "$MODEL_DIR:/models/bge-m3-local:ro" \
  -v "$HF_CACHE_DIR:/hf-cache" \
  "$IMAGE_NAME"
