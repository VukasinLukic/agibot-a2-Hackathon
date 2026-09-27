#!/usr/bin/env bash
# A2/PC2 entrypoint. Never kills an existing supervisor or changes ARM/AIMA.
set -euo pipefail
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SESSION="robot_supervisor"
PYTHON="$WORKDIR/.venv/bin/python"
CHECKER="$WORKDIR/scripts/pc2_deploy.py"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing supervisor interpreter: $PYTHON. Restore the PC2 environment with the mentor." >&2
  exit 1
fi
case "${1:-}" in
  --check) exec "$PYTHON" "$CHECKER" check --root "$WORKDIR" ;;
  --inside-tmux)
    # Read .env in the actual child, not in the pre-existing tmux server.
    cd "$WORKDIR"
    # Preserve venv PATH for service executables as well as the Python interpreter.
    source "$WORKDIR/.venv/bin/activate"
    exec "$PYTHON" "$CHECKER" run --root "$WORKDIR"
    ;;
  "") ;;
  *) echo "Usage: bash run_robot_supervisor_v2.sh [--check]" >&2; exit 2 ;;
esac
command -v tmux >/dev/null || { echo "tmux is required." >&2; exit 1; }
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "NOT STARTED: tmux session '$SESSION' already exists. No process was replaced." >&2
  tmux list-panes -t "$SESSION" -F 'pid=#{pane_pid} cwd=#{pane_current_path} cmd=#{pane_current_command}' >&2
  echo "Inspect it with the mentor; attach explicitly: tmux attach -t $SESSION" >&2
  exit 1
fi
"$PYTHON" "$CHECKER" check --root "$WORKDIR" --require-free-port
LOG_DIR="$WORKDIR/robot_supervisor_v2/logs"
mkdir -p "$LOG_DIR"
printf -v START_CMD 'exec bash %q --inside-tmux >>%q 2>&1' \
  "$WORKDIR/run_robot_supervisor_v2.sh" "$LOG_DIR/robot_supervisor_boot.log"
# Preserve the mentor's interactive ROS shell initialization used by the old launcher.
tmux new-session -d -s "$SESSION" -c "$WORKDIR" /usr/bin/env bash -lc "exec bash"
if [[ -n "${ROS_SELECTION-1}" ]]; then
  tmux send-keys -t "$SESSION:0.0" "${ROS_SELECTION-1}" C-m
fi
tmux send-keys -t "$SESSION:0.0" "$START_CMD" C-m
echo "Start requested from: $WORKDIR"
echo "Log: $LOG_DIR/robot_supervisor_boot.log"
echo "This is not a health confirmation. Check /api/health and the log. No auto-attach."
