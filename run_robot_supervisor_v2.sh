#!/usr/bin/env bash
set -euo pipefail

SESSION="robot_supervisor"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOG_DIR="/agibot/data/home/agi/Desktop/CT/humanoid-platform"
LOG_FILE="$LOG_DIR/robot_supervisor_boot.log"
mkdir -p "$LOG_DIR"
exec >>"$LOG_FILE" 2>&1

echo "=== run_robot_supervisor.sh fired: $(date) user=$(whoami) workdir=$WORKDIR ==="

# Default to ROS option 1 unless caller explicitly sets something (including empty).
if [[ -z "${ROS_SELECTION+x}" ]]; then
  ROS_SELECTION="1"
fi

: "${ROBOT_SUPERVISOR_PORT:=8070}"

# Make cron environment deterministic
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Load .env file if it exists
if [[ -f "$WORKDIR/.env" ]]; then
  set -a
  # Normalize "KEY = value" lines to "KEY=value" before sourcing.
  # shellcheck disable=SC1090
  source <(sed -E 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*/\1=/' "$WORKDIR/.env")
  set +a
fi

# Use bash explicitly in the tmux pane and run the command directly
#
# This must stay on .venv (python3.12): it is the SUPERVISOR's own environment
# and needs fastapi/uvicorn/livekit plus the editable livekit-plugins-truebar
# install, which only .venv has. It does NOT need torch/facenet.
#
# The face-recognition dependencies belong to the *vision detector*, which the
# supervisor launches as a separate subprocess with its own interpreter. Change
# that one via `vision-controller.python_bin` in robot_supervisor_v2/config.yaml,
# not here.
RUN_CMD="source \"$WORKDIR/.venv/bin/activate\" && \
cd \"$WORKDIR\" && \
python robot_supervisor_v2/run_api.py --host 0.0.0.0 --port \"$ROBOT_SUPERVISOR_PORT\""

send_with_optional_ros_selection() {
  local pane="$1"
  local cmd="$2"

  if [[ -n "${ROS_SELECTION:-}" ]]; then
    tmux send-keys -t "$pane" "$ROS_SELECTION" C-m
  fi

  tmux send-keys -t "$pane" "$cmd" C-m
}

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required but not installed." >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists."
else
  echo "Creating tmux session '$SESSION'..."
  tmux new-session -d -s "$SESSION" -c "$WORKDIR" /usr/bin/env bash -lc "echo 'pane started: ' \$(date); exec bash"
  send_with_optional_ros_selection "$SESSION:0.0" "$RUN_CMD"
fi

echo "Robot supervisor started in tmux session: $SESSION"

# Only attach if we have a TTY (interactive run), never from cron
if [[ -t 1 ]]; then
  echo "Attaching..."
  exec tmux attach -t "$SESSION"
else
  echo "No TTY detected; not attaching (cron/boot mode)."
fi
