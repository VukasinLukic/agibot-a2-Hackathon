#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

APP_DIR="/opt/camera-share"
SERVICE_NAME="camera-share.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SCRIPT_SRC="$SCRIPT_DIR/camera_share.sh"
SERVICE_SRC="$SCRIPT_DIR/systemd_entry.txt"
APP_SCRIPT_DST="$APP_DIR/camera-share.sh"
LOOPBACK_DEVICE="/dev/video10"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

run_root() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    command_exists sudo || die "sudo is required when not running as root."
    sudo "$@"
  fi
}

[[ "$(uname -s)" == "Linux" ]] || die "This installer is Linux-only."

for required in ffmpeg modprobe systemctl; do
  command_exists "$required" || die "$required is required. Run camera_share/install_deps.sh first."
done

[[ -f "$APP_SCRIPT_SRC" ]] || die "Missing runtime script: $APP_SCRIPT_SRC"
[[ -f "$SERVICE_SRC" ]] || die "Missing systemd service file: $SERVICE_SRC"

echo "Installing camera-share to ${APP_DIR}"

run_root mkdir -p "$APP_DIR"
run_root cp "$APP_SCRIPT_SRC" "$APP_SCRIPT_DST"
run_root chmod +x "$APP_SCRIPT_DST"

echo "Configuring v4l2loopback to load at boot..."

# Load the module at boot.
echo "v4l2loopback" | run_root tee /etc/modules-load.d/v4l2loopback.conf >/dev/null

# Create one stable virtual camera: /dev/video10.
# exclusive_caps=1 improves compatibility with apps that expect capture-only devices.
echo 'options v4l2loopback devices=1 video_nr=10 card_label="shared-camera" exclusive_caps=1' \
  | run_root tee /etc/modprobe.d/v4l2loopback.conf >/dev/null

echo "Installing systemd service..."

run_root cp "$SERVICE_SRC" "/etc/systemd/system/$SERVICE_NAME"

# Default config. Edit this later if needed.
if [[ ! -f /etc/default/camera-share ]]; then
  run_root tee /etc/default/camera-share >/dev/null <<'EOF'
# Physical camera source.
# Prefer a stable path from:
#   ls -l /dev/v4l/by-id/
CAMERA_SRC=/dev/video6

# Virtual camera created by v4l2loopback.
CAMERA_OUT=/dev/video10

WIDTH=960
HEIGHT=540
FPS=30
INPUT_FORMAT=mjpeg
EOF
fi

echo "Loading v4l2loopback now..."
if ! run_root modprobe -r v4l2loopback 2>/dev/null; then
  echo "Could not unload existing v4l2loopback module; it may be in use."
fi
run_root modprobe v4l2loopback

if [[ ! -e "$LOOPBACK_DEVICE" ]]; then
  die "$LOOPBACK_DEVICE was not created. Stop processes using v4l2loopback, then run: sudo modprobe -r v4l2loopback && sudo modprobe v4l2loopback"
fi

run_root systemctl daemon-reload
run_root systemctl enable "$SERVICE_NAME"
run_root systemctl restart "$SERVICE_NAME"

echo
echo "Done."
echo "Check status with:"
echo "  systemctl status $SERVICE_NAME"
echo
echo "Check logs with:"
echo "  journalctl -u $SERVICE_NAME -f"
echo
echo "Consumers should use:"
echo "  $LOOPBACK_DEVICE"
