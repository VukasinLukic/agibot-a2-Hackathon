#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAG_PYTHON_BIN="${RAG_PYTHON_BIN:-$ROOT_DIR/rag_torch/bin/python}"
RAG_API_HOST="${RAG_API_HOST:-0.0.0.0}"
RAG_API_PORT="${RAG_API_PORT:-8098}"

if [[ ! -x "$RAG_PYTHON_BIN" ]]; then
  echo "RAG python interpreter not found or not executable: $RAG_PYTHON_BIN" >&2
  exit 1
fi

# Ensure repository root is import root for `rag_service.app`.
cd "$ROOT_DIR"

exec "$RAG_PYTHON_BIN" -m uvicorn rag_service.app:app \
  --host "$RAG_API_HOST" \
  --port "$RAG_API_PORT"
