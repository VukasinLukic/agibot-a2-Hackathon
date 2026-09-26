#!/usr/bin/env bash
set -euo pipefail

# Prefer a stable /dev/v4l/by-id path instead of /dev/video6.
# Override with:
#   CAMERA_SRC=/dev/v4l/by-id/your-camera-id ./camera_share.sh
CAMERA_SRC="${CAMERA_SRC:-/dev/video6}"

# Virtual camera created by v4l2loopback.
CAMERA_OUT="${CAMERA_OUT:-/dev/video10}"

# Keep resolution modest, but produce enough FPS for vision consumers.
WIDTH="${WIDTH:-960}"
HEIGHT="${HEIGHT:-540}"
FPS="${FPS:-30}"

# Prefer mjpeg if your camera supports it; it is often cheaper over USB than raw YUYV.
INPUT_FORMAT="${INPUT_FORMAT:-mjpeg}"

echo "Starting camera share:"
echo "  input:  ${CAMERA_SRC}"
echo "  output: ${CAMERA_OUT}"
echo "  mode:   ${WIDTH}x${HEIGHT}@${FPS}, input_format=${INPUT_FORMAT}"

# Wait for the physical camera and virtual camera to exist.
# This helps after boot or after the systemd service restarts.
while [[ ! -e "$CAMERA_SRC" ]]; do
  echo "Waiting for camera source: $CAMERA_SRC"
  sleep 2
done

while [[ ! -e "$CAMERA_OUT" ]]; do
  echo "Waiting for loopback output: $CAMERA_OUT"
  sleep 2
done

exec ffmpeg \
  -hide_banner \
  -loglevel warning \
  -f v4l2 \
  -thread_queue_size 2 \
  -input_format "$INPUT_FORMAT" \
  -video_size "${WIDTH}x${HEIGHT}" \
  -framerate "$FPS" \
  -i "$CAMERA_SRC" \
  -vf "fps=${FPS}" \
  -f v4l2 \
  -pix_fmt yuyv422 \
  "$CAMERA_OUT"
