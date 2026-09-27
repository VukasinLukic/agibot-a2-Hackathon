#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${RAG_API_IMAGE_NAME:-humanoid-rag-api}"
CONTAINER_NAME="${RAG_API_CONTAINER_NAME:-humanoid-rag-api}"
HOST_PORT="${RAG_API_HOST_PORT:-8098}"
CONTAINER_PORT="${RAG_API_CONTAINER_PORT:-8098}"
DATA_DIR="${RAG_DATA_HOST_DIR:-$SCRIPT_DIR/data}"
HF_CACHE_DIR="${RAG_HF_CACHE_HOST_DIR:-$DATA_DIR/hf-cache}"
MODEL_DIR="${RAG_MODEL_HOST_DIR:-$SCRIPT_DIR/../bge-m3-local}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
RAG_QDRANT_URL="${RAG_QDRANT_URL:-http://host.docker.internal:6333}"
RAG_EMBED_MODEL="${RAG_EMBED_MODEL:-/models/bge-m3-local}"
RAG_OFFLINE="${RAG_OFFLINE:-1}"
RAG_DEVICE="${RAG_DEVICE:-cpu}"
RAG_MAX_LENGTH="${RAG_MAX_LENGTH:-256}"
RAG_TOP_K="${RAG_TOP_K:-5}"
HF_HOME="${HF_HOME:-/hf-cache}"
RAG_DATA_ROOT="${RAG_DATA_ROOT:-/data}"
RAG_QDRANT_COLLECTION="${RAG_QDRANT_COLLECTION:-robot_knowledge}"  
RAG_STORAGE_PATH="${RAG_STORAGE_PATH:-/data/knowledge/main}"        
RAG_INDEX_KIND="${RAG_INDEX_KIND:-managed}"                        
RAG_ACTIVE_INDEX_SLUG="${RAG_ACTIVE_INDEX_SLUG:-main}"              
RAG_CHUNK_CHARS="${RAG_CHUNK_CHARS:-2500}"
RAG_OVERLAP_CHARS="${RAG_OVERLAP_CHARS:-300}"

mkdir -p "$DATA_DIR/knowledge" "$HF_CACHE_DIR"

docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR/Dockerfile.rag-api" "$SCRIPT_DIR"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

DOCKER_DATA_DIR="$DATA_DIR"
DOCKER_HF_CACHE_DIR="$HF_CACHE_DIR"
DOCKER_MODEL_DIR="$MODEL_DIR"

# Git Bash rewrites container-style /paths for docker.exe unless conversion is disabled.
if command -v cygpath >/dev/null 2>&1; then
  DOCKER_DATA_DIR="$(cygpath -m "$DATA_DIR")"
  DOCKER_HF_CACHE_DIR="$(cygpath -m "$HF_CACHE_DIR")"
  DOCKER_MODEL_DIR="$(cygpath -m "$MODEL_DIR")"
  export MSYS_NO_PATHCONV=1
  export MSYS2_ARG_CONV_EXCL="*"
fi

exec docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p "$HOST_PORT:$CONTAINER_PORT" \
  --add-host host.docker.internal:host-gateway \
  -e LOG_LEVEL="$LOG_LEVEL" \
  -e RAG_DATA_ROOT="$RAG_DATA_ROOT" \
  -e RAG_QDRANT_URL="$RAG_QDRANT_URL" \
  -e RAG_QDRANT_COLLECTION="$RAG_QDRANT_COLLECTION" \
  -e RAG_STORAGE_PATH="$RAG_STORAGE_PATH" \
  -e RAG_INDEX_KIND="$RAG_INDEX_KIND" \
  -e RAG_ACTIVE_INDEX_SLUG="$RAG_ACTIVE_INDEX_SLUG" \
  -e RAG_EMBED_MODEL="$RAG_EMBED_MODEL" \
  -e RAG_OFFLINE="$RAG_OFFLINE" \
  -e RAG_DEVICE="$RAG_DEVICE" \
  -e RAG_MAX_LENGTH="$RAG_MAX_LENGTH" \
  -e RAG_TOP_K="$RAG_TOP_K" \
  -e RAG_CHUNK_CHARS="$RAG_CHUNK_CHARS" \
  -e RAG_OVERLAP_CHARS="$RAG_OVERLAP_CHARS" \
  -e HF_HOME="$HF_HOME" \
  -v "$DOCKER_DATA_DIR:/data" \
  -v "$DOCKER_MODEL_DIR:/models/bge-m3-local:ro" \
  -v "$DOCKER_HF_CACHE_DIR:/hf-cache" \
  "$IMAGE_NAME"
