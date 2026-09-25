#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${QDRANT_IMAGE_NAME:-qdrant/qdrant:latest}"
CONTAINER_NAME="${QDRANT_CONTAINER_NAME:-humanoid-qdrant}"
HOST_PORT="${QDRANT_HOST_PORT:-6333}"
CONTAINER_PORT="${QDRANT_CONTAINER_PORT:-6333}"
DATA_DIR="${QDRANT_DATA_DIR:-$SCRIPT_DIR/data/qdrant}"

mkdir -p "$DATA_DIR"

if ! docker info >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Cannot access the Docker daemon.

This account likely does not have permission to use /var/run/docker.sock.
Fix one of these, then retry:
  - run this script with sudo
  - add your user to the docker group, then log out and back in:
      sudo usermod -aG docker "$USER"

If Docker is not running yet, start the Docker service first.
EOF
  exit 1
fi

if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  docker pull "$IMAGE_NAME"
fi

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

exec docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p "$HOST_PORT:$CONTAINER_PORT" \
  -v "$DATA_DIR:/qdrant/storage" \
  "$IMAGE_NAME"
