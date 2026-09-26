#!/usr/bin/env bash
set -euo pipefail

SESSION="robot_supervisor"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOG_DIR="/home/unitree"
LOG_FILE="$LOG_DIR/robot_supervisor_boot.log"
mkdir -p "$LOG_DIR"
exec >>"$LOG_FILE" 2>&1

echo "=== run_robot_supervisor.sh fired: $(date) user=$(whoami) workdir=$WORKDIR ==="

# Default to ROS option 1 unless caller explicitly sets something (including empty).
if [[ -z "${ROS_SELECTION+x}" ]]; then
  ROS_SELECTION="1"
fi

: "${ROBOT_SUPERVISOR_CONFIG:="$WORKDIR/robot_supervisor/config.example.yaml"}"
: "${ROBOT_SUPERVISOR_PORT:=8080}"

# Make cron environment deterministic
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Use bash explicitly in the tmux pane and run the command directly
RUN_CMD="source \"$WORKDIR/.venv/bin/activate\" && \
export ROBOT_SUPERVISOR_CONFIG=\"$ROBOT_SUPERVISOR_CONFIG\" && \
uvicorn robot_supervisor.app.main:app --host 0.0.0.0 --port \"$ROBOT_SUPERVISOR_PORT\""

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
