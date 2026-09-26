"""
IGRA service — hand-gesture detection + leaderboard API.

Hackathon first test:
  stable gesture match -> announce \"RADI\" (local TTS and/or LiveKit agent).

Run standalone:
  cd eldorado && source .venv/bin/activate
  python igra_api.py --port 8105

Or via supervisor service type `igra`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Repo root for optional agent_commands import.
REPO_ROOT = ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from announce import announce_sync  # noqa: E402
from camera_source import normalize_camera_id, open_camera  # noqa: E402
from gesture_database import GestureDatabase  # noqa: E402
from gesture_utils import landmarks_to_vector  # noqa: E402
from hand_gesture_detector import HandGestureDetector  # noqa: E402
from leaderboard import LeaderboardStore  # noqa: E402

logger = logging.getLogger("igra")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

DEFAULT_PORT = int(os.getenv("IGRA_PORT", "8105"))
# A2 default: chest-right fisheye (ROS2 alias). Laptop falls back to OpenCV 0.
DEFAULT_CAMERA = os.getenv("IGRA_CAMERA", "CHEST_RIGHT_FISHEYE")
DEFAULT_DB = os.getenv("IGRA_GESTURE_DB", str(ROOT / "gestures.json"))
DEFAULT_LEADERBOARD = os.getenv("IGRA_LEADERBOARD_DB", str(ROOT / "data" / "igra_leaderboard.db"))
STABLE_FRAMES = int(os.getenv("IGRA_STABLE_FRAMES", "3"))
_MAX = os.getenv("IGRA_MAX_DISTANCE")
MAX_DISTANCE = float(_MAX) if _MAX else None  # None => GestureDatabase default
SHOW_WINDOW = os.getenv("IGRA_SHOW_WINDOW", "1").strip() not in {"0", "false", "False", "no"}
ANNOUNCE_TEXT = os.getenv("IGRA_ANNOUNCE_TEXT", "RADI")
ROBOT_ID = os.getenv("IGRA_ROBOT_ID", "agibot-a2-ultra")


class IgraState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.camera_ok = False
        self.last_error: Optional[str] = None
        self.labels: list[str] = []
        self.sample_count = 0
        self.current_label: Optional[str] = None
        self.current_distance: Optional[float] = None
        self.last_stable_label: Optional[str] = None
        self.last_stable_at: Optional[float] = None
        self.last_announce: Optional[dict[str, Any]] = None
        self.frames = 0
        self.detections = 0
        self.started_at: Optional[float] = None
        self.gesture_db_path = DEFAULT_DB
        self.show_window = SHOW_WINDOW
        self.announce_enabled = True
        self.announce_text = ANNOUNCE_TEXT
        self.camera_requested = normalize_camera_id(DEFAULT_CAMERA)
        self.camera_resolved: Optional[str] = None
        self.camera_backend: Optional[str] = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "service": "igra",
                "running": self.running,
                "camera_ok": self.camera_ok,
                "camera_requested": self.camera_requested,
                "camera_resolved": self.camera_resolved,
                "camera_backend": self.camera_backend,
                "last_error": self.last_error,
                "labels": list(self.labels),
                "sample_count": self.sample_count,
                "current_label": self.current_label,
                "current_distance": self.current_distance,
                "last_stable_label": self.last_stable_label,
                "last_stable_at": self.last_stable_at,
                "last_announce": self.last_announce,
                "frames": self.frames,
                "detections": self.detections,
                "started_at": self.started_at,
                "gesture_db_path": self.gesture_db_path,
                "show_window": self.show_window,
                "announce_enabled": self.announce_enabled,
                "announce_text": self.announce_text,
                "uptime_seconds": (time.time() - self.started_at) if self.started_at else 0.0,
            }


STATE = IgraState()
LEADERBOARD = LeaderboardStore(DEFAULT_LEADERBOARD)
_stop_event = threading.Event()
_worker: Optional[threading.Thread] = None


def _detection_loop() -> None:
    import cv2

    db = GestureDatabase().load(STATE.gesture_db_path)
    with STATE.lock:
        STATE.labels = db.labels()
        STATE.sample_count = len(db.real_samples())
        STATE.running = True
        STATE.started_at = time.time()
        STATE.last_error = None
        STATE.camera_requested = normalize_camera_id(DEFAULT_CAMERA)

    if not db.real_samples():
        msg = f"No gesture samples in {STATE.gesture_db_path}. Run collect_gestures.py first."
        logger.warning(msg)
        with STATE.lock:
            STATE.last_error = msg
            STATE.camera_ok = False

    try:
        cap, resolved, backend = open_camera(DEFAULT_CAMERA, startup_timeout=12.0)
    except Exception as exc:
        msg = f"Could not open camera '{DEFAULT_CAMERA}': {exc}"
        logger.error(msg)
        with STATE.lock:
            STATE.camera_ok = False
            STATE.last_error = msg
            STATE.running = False
        return

    detector = HandGestureDetector(max_hands=1)
    pending_label: Optional[str] = None
    pending_count = 0
    last_fired_label: Optional[str] = None

    with STATE.lock:
        STATE.camera_ok = True
        STATE.camera_resolved = resolved
        STATE.camera_backend = backend

    logger.info(
        "IGRA detection loop started (camera=%s resolved=%s backend=%s labels=%s real=%s match_corpus=%s aug=%s window=%s)",
        DEFAULT_CAMERA,
        resolved,
        backend,
        db.labels(),
        len(db.real_samples()),
        len(db.match_samples),
        db.augment_per_real,
        STATE.show_window,
    )

    try:
        while not _stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                with STATE.lock:
                    STATE.camera_ok = False
                    STATE.last_error = "Failed to read camera frame"
                time.sleep(0.05)
                continue

            # Fisheye chest cam is already world-oriented; only mirror local webcams.
            if backend == "opencv" and str(resolved) in {"0", "1"}:
                frame = cv2.flip(frame, 1)
            results = detector.find_hands(frame)
            detector.draw_landmarks(frame, results)

            display_label = "no hand"
            distance: Optional[float] = None
            stable: Optional[str] = None

            if results.multi_hand_landmarks and db.real_samples():
                vector = landmarks_to_vector(results.multi_hand_landmarks[0])
                label, distance = db.match(vector, max_distance=MAX_DISTANCE)
                if label is None:
                    pending_label = None
                    pending_count = 0
                    display_label = "?"
                else:
                    if label == pending_label:
                        pending_count += 1
                    else:
                        pending_label = label
                        pending_count = 1
                    if pending_count >= STABLE_FRAMES:
                        stable = label
                        display_label = label
                    else:
                        display_label = "..."
            else:
                pending_label = None
                pending_count = 0

            with STATE.lock:
                STATE.frames += 1
                STATE.camera_ok = True
                STATE.current_label = stable or (display_label if display_label not in {"no hand", "?", "..."} else None)
                STATE.current_distance = distance
                if stable:
                    STATE.last_stable_label = stable
                    STATE.last_stable_at = time.time()
                    STATE.detections += 1

            # Fire once per newly stable label (re-arms when hand leaves / label changes).
            if stable and stable != last_fired_label:
                last_fired_label = stable
                if STATE.announce_enabled:
                    result = announce_sync(STATE.announce_text)
                    with STATE.lock:
                        STATE.last_announce = {
                            **result,
                            "gesture": stable,
                            "distance": distance,
                            "at": time.time(),
                        }
                    logger.info("Detected gesture=%s announce=%s", stable, result)

            if display_label in {"no hand", "?"}:
                last_fired_label = None

            if STATE.show_window:
                color = (0, 255, 0) if stable else (0, 255, 255)
                if display_label == "no hand":
                    color = (0, 0, 255)
                text = display_label
                if distance is not None:
                    text = f"{display_label}  d={distance:.2f}"
                cv2.putText(
                    frame,
                    text,
                    (10, 50),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.2,
                    color,
                    3,
                    cv2.LINE_AA,
                )
                cv2.putText(
                    frame,
                    f"IGRA | db: {', '.join(db.labels()) or 'empty'} | q: quit window only",
                    (10, frame.shape[0] - 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (200, 200, 200),
                    1,
                    cv2.LINE_AA,
                )
                cv2.imshow("IGRA Detect", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    # Close preview window but keep API alive headless.
                    with STATE.lock:
                        STATE.show_window = False
                    cv2.destroyAllWindows()
            else:
                time.sleep(0.01)
    except Exception as exc:
        logger.exception("IGRA detection loop crashed")
        with STATE.lock:
            STATE.last_error = str(exc)
    finally:
        detector.close()
        cap.release()
        try:
            import cv2

            cv2.destroyAllWindows()
        except Exception:
            pass
        with STATE.lock:
            STATE.running = False
            STATE.camera_ok = False
        logger.info("IGRA detection loop stopped")


def start_worker() -> None:
    global _worker
    if _worker and _worker.is_alive():
        return
    _stop_event.clear()
    _worker = threading.Thread(target=_detection_loop, name="igra-detect", daemon=True)
    _worker.start()


def stop_worker() -> None:
    _stop_event.set()
    if _worker and _worker.is_alive():
        _worker.join(timeout=3.0)


app = FastAPI(title="IGRA", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MatchRecordRequest(BaseModel):
    player_name: str = Field(..., min_length=1)
    winner: str = Field(..., description="human | robot | draw")
    human_choice: Optional[str] = None
    robot_choice: Optional[str] = None
    comm_mode: Optional[str] = None
    posture_mode: Optional[str] = None
    robot_id: Optional[str] = None


class AnnounceRequest(BaseModel):
    text: str = "RADI"


@app.on_event("startup")
def _on_startup() -> None:
    auto = os.getenv("IGRA_AUTO_START_DETECT", "1").strip() not in {"0", "false", "False"}
    if auto:
        start_worker()


@app.on_event("shutdown")
def _on_shutdown() -> None:
    stop_worker()


@app.get("/health")
def health() -> dict[str, Any]:
    snap = STATE.snapshot()
    return {
        "status": "ok" if snap["running"] or snap["last_error"] is None else "degraded",
        "camera_ok": snap["camera_ok"],
        "running": snap["running"],
    }


@app.get("/status")
def status() -> dict[str, Any]:
    return STATE.snapshot()


@app.get("/leaderboard")
def leaderboard() -> dict[str, Any]:
    return LEADERBOARD.snapshot()


@app.post("/leaderboard/match")
def record_match(body: MatchRecordRequest) -> dict[str, Any]:
    winner = body.winner.strip().lower()
    if winner not in {"human", "robot", "draw"}:
        raise HTTPException(status_code=400, detail="winner must be human|robot|draw")
    return LEADERBOARD.record_match(
        player_name=body.player_name,
        robot_id=body.robot_id or ROBOT_ID,
        winner=winner,
        human_choice=body.human_choice,
        robot_choice=body.robot_choice,
        comm_mode=body.comm_mode,
        posture_mode=body.posture_mode,
    )


@app.post("/detect/start")
def detect_start() -> dict[str, Any]:
    start_worker()
    return {"status": "starting", **STATE.snapshot()}


@app.post("/detect/stop")
def detect_stop() -> dict[str, Any]:
    stop_worker()
    return {"status": "stopped", **STATE.snapshot()}


@app.post("/announce")
def announce_now(body: AnnounceRequest) -> dict[str, Any]:
    result = announce_sync(body.text or "RADI", cooldown_s=0.0)
    with STATE.lock:
        STATE.last_announce = {**result, "at": time.time(), "manual": True}
    return result


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "igra",
        "docs": "/docs",
        "health": "/health",
        "status": "/status",
        "leaderboard": "/leaderboard",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="IGRA gesture + leaderboard service")
    parser.add_argument("--host", default=os.getenv("IGRA_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    logger.info("Starting IGRA on %s:%s", args.host, args.port)
    uvicorn.run(
        "igra_api:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
