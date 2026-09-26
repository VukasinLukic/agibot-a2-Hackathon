#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"

INSTALL_ROOT="${INSTALL_ROOT:-/agibot/data/home/agi/humanoid-livekit}"
CONFIG_DIR="${CONFIG_DIR:-/etc/humanoid-livekit}"
SERVICE_NAME="${SERVICE_NAME:-remote-audio-bridge-manager}"
SERVICE_USER="${SERVICE_USER:-agi}"
SKIP_APT="${SKIP_APT:-0}"
SKIP_PYTHON="${SKIP_PYTHON:-0}"
ENABLE_SERVICE="${ENABLE_SERVICE:-1}"
START_SERVICE="${START_SERVICE:-1}"
FORCE_CONFIG="${FORCE_CONFIG:-0}"

usage() {
  cat <<EOF
Usage: $0 [options]

Installs the PC3 remote audio bridge manager from the current repository checkout.

Options:
  --install-root PATH   Repo checkout used by systemd (default: $INSTALL_ROOT)
  --config-dir PATH     Manager config directory (default: $CONFIG_DIR)
  --service-name NAME   systemd service name without .service (default: $SERVICE_NAME)
  --service-user USER   User that runs the manager and owns audio devices (default: $SERVICE_USER)
  --skip-apt            Do not install apt packages
  --skip-python         Do not create/update the Python venv
  --no-enable           Do not enable the systemd service
  --no-start            Do not start/restart the systemd service
  --force-config        Overwrite existing manager YAML and env file
  -h, --help            Show this help

Environment variables with the same names as the uppercase options can also be used.
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

run_as_service_user() {
  if [[ "$(id -un)" == "$SERVICE_USER" ]]; then
    "$@"
  elif [[ "$(id -u)" -eq 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo -H -u "$SERVICE_USER" "$@"
    else
      runuser -u "$SERVICE_USER" -- "$@"
    fi
  else
    sudo -H -u "$SERVICE_USER" "$@"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-root)
      INSTALL_ROOT="${2:?missing value for --install-root}"
      shift 2
      ;;
    --config-dir)
      CONFIG_DIR="${2:?missing value for --config-dir}"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="${2:?missing value for --service-name}"
      shift 2
      ;;
    --service-user)
      SERVICE_USER="${2:?missing value for --service-user}"
      shift 2
      ;;
    --skip-apt)
      SKIP_APT=1
      shift
      ;;
    --skip-python)
      SKIP_PYTHON=1
      shift
      ;;
    --no-enable)
      ENABLE_SERVICE=0
      shift
      ;;
    --no-start)
      START_SERVICE=0
      shift
      ;;
    --force-config)
      FORCE_CONFIG=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

INSTALL_ROOT="$(readlink -f "$INSTALL_ROOT")"
CONFIG_DIR="$(readlink -m "$CONFIG_DIR")"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CONFIG_FILE="$CONFIG_DIR/audio_bridge_manager.yaml"
ENV_FILE="$CONFIG_DIR/audio-bridge-manager.env"

[[ -f "$SCRIPT_DIR/apt-requirements.txt" ]] || die "missing apt-requirements.txt"
[[ -f "$SCRIPT_DIR/requirements.txt" ]] || die "missing requirements.txt"
[[ -f "$SCRIPT_DIR/remote-audio-bridge-manager.service" ]] || die "missing service file"
[[ -f "$REPO_ROOT/robot_services/audio/audio_bridge_manager.py" ]] || die "cannot find audio_bridge_manager.py"
[[ -f "$REPO_ROOT/robot_services/audio/audio_bridge_manager.example.yaml" ]] || die "cannot find audio_bridge_manager.example.yaml"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER:-}" != "root" ]] && id "$SUDO_USER" >/dev/null 2>&1; then
    SERVICE_USER="$SUDO_USER"
  else
    die "service user '$SERVICE_USER' does not exist; pass --service-user"
  fi
fi

if [[ "$REPO_ROOT" != "$INSTALL_ROOT" ]]; then
  die "repo checkout is '$REPO_ROOT' but install root is '$INSTALL_ROOT'. Clone or move the repo to the install root, or pass --install-root '$REPO_ROOT'."
fi

if [[ "$SKIP_APT" -eq 0 ]]; then
  echo "Installing apt packages..."
  mapfile -t APT_PACKAGES < <(sed -E 's/#.*$//' "$SCRIPT_DIR/apt-requirements.txt" | awk 'NF')
  as_root apt-get update
  as_root apt-get install -y "${APT_PACKAGES[@]}"
fi

if [[ "$SKIP_PYTHON" -eq 0 ]]; then
  echo "Creating/updating Python venv..."
  run_as_service_user python3 -m venv "$INSTALL_ROOT/.venv"
  run_as_service_user "$INSTALL_ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
  run_as_service_user "$INSTALL_ROOT/.venv/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"
fi
run_as_service_user mkdir -p "$INSTALL_ROOT/robot_supervisor_v2/logs"

echo "Installing manager config..."
as_root install -d -m 0755 "$CONFIG_DIR"
if [[ ! -f "$CONFIG_FILE" || "$FORCE_CONFIG" -eq 1 ]]; then
  as_root install -m 0644 "$REPO_ROOT/robot_services/audio/audio_bridge_manager.example.yaml" "$CONFIG_FILE"
else
  echo "Keeping existing $CONFIG_FILE"
fi

if [[ ! -f "$ENV_FILE" || "$FORCE_CONFIG" -eq 1 ]]; then
  TOKEN_PYTHON="$INSTALL_ROOT/.venv/bin/python"
  if [[ ! -x "$TOKEN_PYTHON" ]]; then
    TOKEN_PYTHON="python3"
  fi
  TOKEN="$("$TOKEN_PYTHON" - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  TMP_ENV="$(mktemp)"
  cat > "$TMP_ENV" <<EOF
AUDIO_BRIDGE_MANAGER_TOKEN=$TOKEN
LIVEKIT_URL=ws://<pc2-ip>:7880
LIVEKIT_ROOM=g1-lab
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
EOF
  as_root install -m 0600 "$TMP_ENV" "$ENV_FILE"
  rm -f "$TMP_ENV"
else
  echo "Keeping existing $ENV_FILE"
fi

echo "Installing systemd service..."
TMP_SERVICE="$(mktemp)"
sed \
  -e "s|^User=.*|User=$SERVICE_USER|" \
  -e "s|^WorkingDirectory=.*|WorkingDirectory=$INSTALL_ROOT|" \
  -e "s|^EnvironmentFile=.*|EnvironmentFile=-$ENV_FILE|" \
  -e "s|^ExecStart=.*|ExecStart=$INSTALL_ROOT/.venv/bin/python robot_services/audio/audio_bridge_manager.py --config $CONFIG_FILE|" \
  "$SCRIPT_DIR/remote-audio-bridge-manager.service" > "$TMP_SERVICE"
as_root install -m 0644 "$TMP_SERVICE" "$SERVICE_FILE"
rm -f "$TMP_SERVICE"

as_root systemctl daemon-reload
if [[ "$ENABLE_SERVICE" -eq 1 ]]; then
  as_root systemctl enable "$SERVICE_NAME.service"
fi
if [[ "$START_SERVICE" -eq 1 ]]; then
  as_root systemctl restart "$SERVICE_NAME.service"
fi

cat <<EOF

Installed PC3 remote audio bridge manager.

Service:      $SERVICE_NAME.service
Install root: $INSTALL_ROOT
Config:       $CONFIG_FILE
Env:          $ENV_FILE

Next checks:
  sudo systemctl status $SERVICE_NAME.service --no-pager
  sudo journalctl -u $SERVICE_NAME.service -n 100 --no-pager
  python3 - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8766/health", timeout=2).read().decode())
PY
EOF
