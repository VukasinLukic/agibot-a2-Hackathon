#!/usr/bin/env bash
set -euo pipefail

SESSION="livekit_stack"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Default to ROS option 1 unless caller explicitly sets something (including empty).
if [[ -z "${ROS_SELECTION+x}" ]]; then
  ROS_SELECTION="1"
fi

# Commands
CMD1='livekit-server --dev'
CMD2='source .venv/bin/activate && python livekit-client/agent_basic.py start'
CMD3='source .venv/bin/activate && python examples/livekit_audio_patching/robot_audio_bridge.py --iface eth0'

send_with_optional_ros_selection() {
  local pane="$1"
  local cmd="$2"

  # Some machines prompt for a ROS environment (1/2) before accepting commands.
  if [[ -n "$ROS_SELECTION" ]]; then
    tmux send-keys -t "$pane" "$ROS_SELECTION" C-m
  fi

  tmux send-keys -t "$pane" "$cmd" C-m
}

# If session already exists, just attach
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists. Attaching..."
  exec tmux attach -t "$SESSION"
fi

# Create session and lay out panes
tmux new-session -d -s "$SESSION" -c "$WORKDIR"

# Split into 3 panes: left (cmd1), top-right (cmd2), bottom-right (cmd3)
tmux split-window -h  -t "$SESSION" -c "$WORKDIR"
tmux split-window -v  -t "$SESSION:0.1" -c "$WORKDIR"

# Optional: nicer layout
tmux select-layout -t "$SESSION" tiled >/dev/null 2>&1 || true

# Send commands
send_with_optional_ros_selection "$SESSION:0.0" "$CMD1"
sleep "1"
send_with_optional_ros_selection "$SESSION:0.1" "$CMD2"
sleep "10"
send_with_optional_ros_selection "$SESSION:0.2" "$CMD3"

echo "Started in tmux session: $SESSION"
echo "Attach with: tmux attach -t $SESSION"
echo "Detach with: Ctrl-b then d"
exec tmux attach -t "$SESSION"
