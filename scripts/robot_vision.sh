#!/usr/bin/env bash
# Ball vision on the A2 chest fisheye: thin wrappers around table_tennis.vision.
#
# Runs on the robot (PC2, ssh agi@192.168.2.50). It never moves the robot and
# never touches the Supervisor: it reads the camera, writes files under
# table_tennis/var/vision and posts only vision commands (camera.ready.set,
# point.propose) with TT_VISION_TOKEN. Every command is printed before it runs.
#
#   scripts/robot_vision.sh check                     read-only: env, camera, weights, backend
#   scripts/robot_vision.sh grab                      one fisheye frame -> kadar.png
#   scripts/robot_vision.sh calibrate-points "x,y x,y x,y x,y x,y x,y"
#   scripts/robot_vision.sh cal-id                    calibration_id and size from table.json
#   scripts/robot_vision.sh record [seconds]          dry-run + raw TTCLIP (default 120 s)
#   scripts/robot_vision.sh fix-clip <file.ttclip>    header frame count after a hard stop
#   scripts/robot_vision.sh dry-run                   logs what it would send, posts nothing
#   scripts/robot_vision.sh run                       real proposals into the match
#   scripts/robot_vision.sh pydeps <wheels-dir>       unpack pydantic wheels into var/pydeps
#
# Environment (all optional except the token for dry-run/run/record):
#   TT_VISION_TOKEN   vision token; if unset, read from $REPO/.env (never printed)
#   TT_API_URL        default http://127.0.0.1:8070 (Supervisor)
#   MATCH_ID          default latest
#   DEVICE            default CHEST_LEFT_FISHEYE (raw fisheye only; /h264 is refused)
#   BALLNET           default table_tennis/var/vision/ballnet.onnx
#   CALIBRATION       default table_tennis/var/vision/table.json
#   FRAME             default table_tennis/var/vision/kadar.png (grab)
#   VISION_CONFIG     optional vision yaml for --config
#   RECORD_DIR        default table_tennis/var/vision/clips
#   TT_VISION_CPUS    optional taskset core list, for example 6-7
#   PY                default /usr/bin/python3 (ROS Humble rclpy is Python 3.10)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIT="$REPO/table_tennis/var/vision"
TT_API_URL="${TT_API_URL:-http://127.0.0.1:8070}"
MATCH_ID="${MATCH_ID:-latest}"
DEVICE="${DEVICE:-CHEST_LEFT_FISHEYE}"
BALLNET="${BALLNET:-$KIT/ballnet.onnx}"
CALIBRATION="${CALIBRATION:-$KIT/table.json}"
FRAME="${FRAME:-$KIT/kadar.png}"
RECORD_DIR="${RECORD_DIR:-$KIT/clips}"
PYDEPS="$REPO/table_tennis/var/pydeps"
if [[ -z "${PY:-}" ]]; then
  if [[ -x /usr/bin/python3 ]]; then PY=/usr/bin/python3; else PY=python3; fi
fi

say() { printf '\n== %s\n' "$*"; }
warn() { printf 'UPOZORENJE: %s\n' "$*" >&2; }
die() { printf 'GRESKA: %s\n' "$*" >&2; exit 1; }

# Print the exact command, then run it from the repo root.
run_cmd() {
  printf '+ %s\n' "$(printf '%q ' "$@")"
  (cd "$REPO" && "$@")
}

ros_env() {
  # The login menu (ROS_SELECTION=1 in run_robot_supervisor_v2.sh) normally sources ROS.
  if ! command -v ros2 >/dev/null 2>&1 && [[ -f /opt/ros/humble/setup.bash ]]; then
    set +u
    # shellcheck disable=SC1091
    source /opt/ros/humble/setup.bash
    set -u
  fi
  # Same defaults as Ros2VideoCapture: an explicit choice is kept.
  export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-232}"
  export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
  local profile=/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml
  if [[ -z "${FASTRTPS_DEFAULT_PROFILES_FILE:-}" && -f "$profile" ]]; then
    export FASTRTPS_DEFAULT_PROFILES_FILE="$profile"
  fi
}

py_env() {
  ros_env
  local path="$REPO"
  [[ -d "$REPO/jetson_deps" ]] && path="$REPO/jetson_deps:$path"
  [[ -d "$PYDEPS" ]] && path="$PYDEPS:$path"
  export PYTHONPATH="$path${PYTHONPATH:+:$PYTHONPATH}"
  export PYTHONUNBUFFERED=1
}

# Token from the environment, else the last TT_VISION_TOKEN line of $REPO/.env.
find_token() {
  if [[ -n "${TT_VISION_TOKEN:-}" ]]; then
    return 0
  fi
  [[ -f "$REPO/.env" ]] || return 1
  local line
  line="$(grep -E '^[[:space:]]*TT_VISION_TOKEN[[:space:]]*=' "$REPO/.env" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1
  line="$(printf '%s' "${line#*=}" | sed -E 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  line="${line#\"}"
  line="${line%\"}"
  line="${line#\'}"
  line="${line%\'}"
  [[ -n "$line" ]] || return 1
  export TT_VISION_TOKEN="$line"
  echo "TT_VISION_TOKEN procitan iz $REPO/.env (${#TT_VISION_TOKEN} znakova)"
}

load_token() {
  find_token || die "TT_VISION_TOKEN nije postavljen i nema ga u $REPO/.env. Ako .env nema tokene, Supervisor prima podrazumevani: export TT_VISION_TOKEN=vision_secret"
}

need_file() {
  [[ -f "$1" ]] || die "$2: nema fajla $1"
}

# Common live arguments. The token stays in the environment, never on the command line.
live_args() {
  LIVE=("$PY" -m table_tennis.vision.live
    --device "$DEVICE"
    --base-url "$TT_API_URL"
    --match-id "$MATCH_ID"
    --calibration "$CALIBRATION"
    --ballnet "$BALLNET")
  if [[ -n "${VISION_CONFIG:-}" ]]; then
    LIVE+=(--config "$VISION_CONFIG")
  fi
  if [[ -n "${TT_VISION_CPUS:-}" ]]; then
    command -v taskset >/dev/null 2>&1 || die "taskset nije instaliran"
    LIVE=(taskset -c "$TT_VISION_CPUS" "${LIVE[@]}")
  fi
}

cmd_check() {
  py_env
  say "robot i repo"
  hostname -I || true
  echo "repo: $REPO"
  (cd "$REPO" && git log --oneline -1 2>/dev/null) || true
  echo "jezgra: $(nproc)"
  df -h "$KIT" 2>/dev/null || df -h "$REPO"
  [[ -d /agibot/data ]] && df -h /agibot/data

  say "python ($PY)"
  "$PY" --version
  (cd "$REPO" && "$PY" - <<'EOF') || warn "fali paket (vidi iznad); pydantic: scripts/robot_vision.sh pydeps <wheels>"
import importlib, sys
missing = 0
for name in ("rclpy", "sensor_msgs.msg", "numpy", "cv2", "pydantic"):
    try:
        module = importlib.import_module(name)
        print(f"ok   {name} {getattr(module, '__version__', '')}")
    except Exception as error:
        missing += 1
        print(f"FALI {name}: {error}")
if missing == 0:
    import pydantic
    if not pydantic.VERSION.startswith("2"):
        print(f"FALI pydantic 2 (ima {pydantic.VERSION})")
        missing += 1
sys.exit(1 if missing else 0)
EOF

  say "kod na robotu ima potrebne opcije"
  local help
  help="$(cd "$REPO" && "$PY" -m table_tennis.vision.live --help 2>&1 || true)"
  for flag in --device --grab --record --dry-run --ballnet --match-id; do
    if grep -q -- "$flag" <<<"$help"; then echo "ok   live $flag"; else warn "live nema $flag (stari kod na robotu?)"; fi
  done
  if [[ -f "$REPO/table_tennis/vision/calibrate.py" ]]; then echo "ok   calibrate.py"; else warn "nema table_tennis/vision/calibrate.py"; fi
  if grep -q "def read_if_new" "$REPO/robot_services/vision/detection/ros2_capture.py" 2>/dev/null; then
    echo "ok   ros2_capture.read_if_new"
  else
    warn "ros2_capture nema read_if_new: capture poredi bajtove celog kadra (sporije)"
  fi

  say "ROS topic ($DEVICE), samo citanje"
  local topic=/aima/hal/fish_eye_camera/chest_left/color
  [[ "$DEVICE" == CHEST_RIGHT_FISHEYE ]] && topic=/aima/hal/fish_eye_camera/chest_right/color
  echo "ROS_DOMAIN_ID=$ROS_DOMAIN_ID ROS_LOCALHOST_ONLY=$ROS_LOCALHOST_ONLY"
  if command -v ros2 >/dev/null 2>&1; then
    printf '+ ros2 topic list | grep fish_eye\n'
    timeout --foreground 15 ros2 topic list | grep fish_eye || warn "nema fish_eye topica"
    printf '+ ros2 topic hz %s (8 s)\n' "$topic"
    timeout --foreground --signal=INT 8 ros2 topic hz "$topic" | tail -n 3 || true
    printf '+ ros2 topic echo --once %s --no-arr\n' "$topic"
    timeout --foreground 10 ros2 topic echo --once "$topic" --no-arr | grep -E 'height|width|encoding|step' || warn "nijedna poruka za 10 s"
  else
    warn "ros2 nije u PATH-u (izaberi ROS opciju 1 pri ssh prijavi)"
  fi

  say "kadrovi kroz isti capture kao live (90 kadrova)"
  (cd "$REPO" && timeout 40 "$PY" - "$DEVICE" <<'EOF') || warn "capture nije dao kadrove"
import sys, time
from table_tennis.vision.a2 import A2FisheyeCapture
with A2FisheyeCapture(sys.argv[1]) as capture:
    started = None
    count = 0
    for frame in capture:
        if started is None:
            started = time.monotonic()
            print(f"kadar {frame.width}x{frame.height} topic {capture.topic}")
        count += 1
        if count >= 90:
            break
    if started is not None and count > 1:
        print(f"fps kamere kroz capture: {(count - 1) / (time.monotonic() - started):.1f}")
    print(f"camera_missing={capture.camera_missing} duplicates={capture.stats.duplicate_frames}")
EOF

  say "BallNet tezine"
  local weights
  declare -A seen=()
  for weights in "$BALLNET" "$KIT"/ballnet*.onnx "$KIT"/ballnet*.npz; do
    [[ -f "$weights" && -z "${seen[$weights]:-}" ]] || continue
    seen[$weights]=1
    md5sum "$weights"
    (cd "$REPO" && "$PY" - "$weights" <<'EOF') || warn "ne ucitava se: $weights"
import sys, time
import numpy as np
from table_tennis.vision.ballnet import load_ballnet
net = load_ballnet(sys.argv[1])
patches = np.zeros((40, 32, 32, 4), dtype=np.float32)
net.probs(patches)
started = time.perf_counter()
for _ in range(10):
    net.probs(patches)
print(f"ok   {type(net).__name__}: {(time.perf_counter() - started) * 100:.1f} ms za 40 kandidata")
EOF
  done
  [[ -f "$BALLNET" ]] || warn "BALLNET ne postoji: $BALLNET"

  say "kalibracija"
  if [[ -f "$CALIBRATION" ]]; then cmd_cal_id; else echo "jos nema $CALIBRATION"; fi

  say "backend $TT_API_URL"
  curl -s -m 3 -o /dev/null -w 'GET /api/health -> %{http_code}\n' "$TT_API_URL/api/health" || warn "backend ne odgovara"
  if find_token; then
    curl -s -m 3 -H "Authorization: Bearer $TT_VISION_TOKEN" -o /dev/null \
      -w 'GET /api/table-tennis/matches (vision token) -> %{http_code}\n' "$TT_API_URL/api/table-tennis/matches" || true
    echo "(200 = token i feature rade, 401 = token nije prepoznat, 404 = feature nije ukljucen u Supervisoru)"
  else
    echo "TT_VISION_TOKEN nije postavljen, preskacem proveru tokena"
  fi
}

cmd_grab() {
  py_env
  mkdir -p "$(dirname "$FRAME")"
  run_cmd "$PY" -m table_tennis.vision.calibrate --device "$DEVICE" --save-frame "$FRAME"
  echo "Na laptopu: scp agi@192.168.2.50:$FRAME ."
}

cmd_calibrate_points() {
  [[ $# -eq 1 ]] || die 'treba jedan argument sa 6 tacaka: "x,y x,y x,y x,y x,y x,y"'
  py_env
  mkdir -p "$(dirname "$CALIBRATION")"
  run_cmd "$PY" -m table_tennis.vision.calibrate --device "$DEVICE" --points "$1" --out "$CALIBRATION"
  echo "Pregled: ${CALIBRATION%.json}.png (scp na laptop i proveri A/B i mrezu)"
}

cmd_cal_id() {
  need_file "$CALIBRATION" "kalibracija"
  "$PY" - "$CALIBRATION" <<'EOF'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"calibration_id: {data.get('calibration_id')}")
print(f"kadar: {data.get('width')}x{data.get('height')}  uglovi {data.get('corners_px')}  mreza {data.get('net_px')}")
EOF
}

cmd_record() {
  local seconds="${1:-120}"
  [[ "$seconds" =~ ^[0-9]+$ ]] || die "sekunde moraju biti broj"
  need_file "$CALIBRATION" "record traži kalibraciju (live snima samo uz meč)"
  need_file "$BALLNET" "record"
  load_token
  py_env
  mkdir -p "$RECORD_DIR"
  local out need free
  out="$RECORD_DIR/fisheye-$(date +%Y%m%d-%H%M%S).ttclip"
  # Raw BGR: width * height * 3 bytes per frame, about 30 frames per second.
  need="$("$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["width"]*d["height"]*3*30*int(sys.argv[2])//1024)' "$CALIBRATION" "$seconds")"
  free="$(df -Pk "$RECORD_DIR" | awk 'NR==2 {print $4}')"
  echo "snimak ~$((need / 1024)) MB, slobodno $((free / 1024)) MB na $RECORD_DIR"
  (( free > need + 2 * 1024 * 1024 )) || die "nema dovoljno mesta; smanji sekunde ili postavi RECORD_DIR"
  live_args
  # SIGINT lets live close the clip and patch the frame count in the header.
  run_cmd timeout --foreground --signal=INT --kill-after=20 "$seconds" "${LIVE[@]}" --dry-run --record "$out" || true
  [[ -f "$out" ]] || die "snimak nije napravljen"
  cmd_fix_clip "$out"
  ls -lh "$out"
  echo "Na laptopu: scp agi@192.168.2.50:$out ."
}

cmd_fix_clip() {
  [[ $# -eq 1 ]] || die "treba putanja do .ttclip"
  need_file "$1" "fix-clip"
  "$PY" - "$1" <<'EOF'
import os, struct, sys
path = sys.argv[1]
head = struct.Struct("<8sIIIQ")
with open(path, "r+b") as handle:
    magic, width, height, count, period = head.unpack(handle.read(head.size))
    if magic != b"TTCLIP01":
        sys.exit(f"not a TTCLIP: {path}")
    frames = (os.path.getsize(path) - head.size) // (width * height * 3)
    print(f"{path}: {width}x{height}, header {count} frames, file {frames} frames, {1e9 / period:.1f} fps")
    if count != frames:
        handle.seek(0)
        handle.write(head.pack(magic, width, height, frames, period))
        print(f"header set to {frames} frames")
EOF
}

cmd_dry_run() {
  need_file "$CALIBRATION" "dry-run"
  need_file "$BALLNET" "dry-run"
  load_token
  py_env
  live_args
  run_cmd "${LIVE[@]}" --dry-run
}

cmd_run() {
  need_file "$CALIBRATION" "run"
  need_file "$BALLNET" "run"
  load_token
  py_env
  live_args
  echo "Zaustavljanje: Ctrl+C (salje camera.ready.set false). Ne gasiti sa kill -9 ni tmux kill-session."
  run_cmd "${LIVE[@]}"
}

cmd_pydeps() {
  [[ $# -eq 1 && -d "$1" ]] || die "treba folder sa .whl fajlovima"
  mkdir -p "$PYDEPS"
  local wheel
  for wheel in "$1"/*.whl; do
    run_cmd "$PY" -m zipfile -e "$wheel" "$PYDEPS"
  done
  echo "Raspakovano u $PYDEPS (samo za ovu skriptu; brisanje: rm -rf $PYDEPS)"
}

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

main() {
  local command="${1:-}"
  [[ $# -gt 0 ]] && shift
  case "$command" in
    check) cmd_check ;;
    grab) cmd_grab ;;
    calibrate-points) cmd_calibrate_points "$@" ;;
    cal-id) cmd_cal_id ;;
    record) cmd_record "$@" ;;
    fix-clip) cmd_fix_clip "$@" ;;
    dry-run) cmd_dry_run ;;
    run) cmd_run ;;
    pydeps) cmd_pydeps "$@" ;;
    ""|-h|--help|help) usage ;;
    *) usage; die "nepoznata komanda: $command" ;;
  esac
}

main "$@"
