import os
from pathlib import Path

try:
    from .defaults import DEFAULT_CAMERA_FOURCC, DEFAULT_CAMERA_FPS, DEFAULT_CARD_CAPTURE_FPS, DEFAULT_DETECTION_FPS
except ImportError:
    from defaults import DEFAULT_CAMERA_FOURCC, DEFAULT_CAMERA_FPS, DEFAULT_CARD_CAPTURE_FPS, DEFAULT_DETECTION_FPS

# robot_services/vision/detection/config.py -> repo root (matches main.py's REPO_ROOT)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _env_float(name, default):
    value = os.getenv(name)
    return default if value in (None, "") else float(value)


def _env_int(name, default):
    value = os.getenv(name)
    return default if value in (None, "") else int(value)


def _env_camera_id(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    normalized = value.strip()
    if not normalized:
        return default
    return int(normalized) if normalized.isdigit() else normalized


MODEL_NAME = os.getenv("VISION_MODEL_NAME", "yolo26n.pt")
YOLO_IMAGE_SIZE = _env_int("VISION_YOLO_IMAGE_SIZE", 640)

PERSON_CLASS_ID = 0
CONF_THRESHOLD = 0.80

CAMERA_ID = _env_camera_id("VISION_CAMERA_ID", 6)
CAMERA_WIDTH = _env_int("VISION_CAMERA_WIDTH", None)
CAMERA_HEIGHT = _env_int("VISION_CAMERA_HEIGHT", None)
CAMERA_FPS = _env_float("VISION_CAMERA_FPS", DEFAULT_CAMERA_FPS)
CAMERA_FOURCC = os.getenv("VISION_CAMERA_FOURCC", DEFAULT_CAMERA_FOURCC).strip().upper() or None
CAMERA_BUFFER_SIZE = _env_int("VISION_CAMERA_BUFFER_SIZE", None)

# Cap detector work. Set VISION_DETECTION_FPS=0 to run unthrottled.
DETECTION_FPS = _env_float("VISION_DETECTION_FPS", DEFAULT_DETECTION_FPS)
CARD_CAPTURE_FPS = _env_float("VISION_CARD_CAPTURE_FPS", DEFAULT_CARD_CAPTURE_FPS)
CARD_CAPTURE_REACQUIRE_TIMEOUT_S = _env_float("VISION_CARD_CAPTURE_REACQUIRE_TIMEOUT_S", 10.0)
IDLE_FPS = _env_float("VISION_IDLE_FPS", 2.0)
STATUS_POLL_INTERVAL_SECONDS = _env_float("VISION_STATUS_POLL_INTERVAL_SECONDS", 0.5)
STATUS_TIMEOUT_SECONDS = _env_float("VISION_STATUS_TIMEOUT_SECONDS", 0.25)
SUPERVISOR_URL = os.getenv("VISION_SUPERVISOR_URL", "http://localhost:8080").rstrip("/")
STATUS_URL = os.getenv("VISION_STATUS_URL", f"{SUPERVISOR_URL}/api/vision").strip()
STATS_INTERVAL_SECONDS = _env_float("VISION_STATS_INTERVAL_SECONDS", 5.0)
OPENCV_NUM_THREADS = _env_int("VISION_OPENCV_NUM_THREADS", 1)
TORCH_NUM_THREADS = _env_int("VISION_TORCH_NUM_THREADS", 1)
ROS_STARTUP_TIMEOUT_S = _env_float("VISION_ROS_STARTUP_TIMEOUT_S", 10.0)

# distance proxy
CLOSE_HEIGHT_RATIO = _env_float("VISION_CLOSE_HEIGHT_RATIO", 0.50)

# How often (seconds) to repeat the "PERSON PRESENT" status line while somebody
# is in frame. Appearance/disappearance are always logged once, immediately.
VISION_PRESENCE_LOG_INTERVAL_S = max(
    0.0,
    _env_float("VISION_PRESENCE_LOG_INTERVAL_S", 1.0),
)

# face confirmation before creating a new lock
FACE_MIN_SIZE = 70
FACE_MIN_WIDTH = 40
FACE_MIN_HEIGHT = 40
FACE_CONF_THRESHOLD = 0.90
FACE_CROP_HEIGHT_RATIO = 0.60

# face identity (recognition + enrollment)
VISION_FACE_MATCH_THRESHOLD = _env_float("VISION_FACE_MATCH_THRESHOLD", 0.62)
VISION_FACE_DEDUPE_THRESHOLD = _env_float("VISION_FACE_DEDUPE_THRESHOLD", 0.75)
VISION_IDENTITY_RETRY_SECONDS = max(
    1.0,
    _env_float("VISION_IDENTITY_RETRY_SECONDS", 5.0),
)
# Resolved against REPO_ROOT (not the process's CWD) so every entry point -
# the live detector, the bulk-enroll CLI, the supervisor - reads/writes the
# same faces.db regardless of the working directory it happens to be launched
# from. Override with an absolute VISION_FACE_STORE_PATH if you need the DB
# somewhere else.
_face_store_path_env = os.getenv("VISION_FACE_STORE_PATH", "robot_supervisor_v2/data/faces.db")
VISION_FACE_STORE_PATH = str(
    Path(_face_store_path_env)
    if Path(_face_store_path_env).is_absolute()
    else (REPO_ROOT / _face_store_path_env)
)
VISION_FACE_CAPTURE_STABILITY_FRAMES = _env_int("VISION_FACE_CAPTURE_STABILITY_FRAMES", 5)
VISION_FACE_CAPTURE_MIN_BLUR = _env_float("VISION_FACE_CAPTURE_MIN_BLUR", 60.0)

# lock behavior - after that number of frames without same track ID, the lock is removed and a new lock can be created again
#
# All four are env-overridable so they can be tuned per robot/camera from
# config.yaml without editing code. The defaults below were tuned for a
# narrow-FOV camera; a wide-angle fisheye at 640x480 running 7-15 fps needs
# looser tolerances, because 0.002 of frame width is only ~1.3 px there - less
# than YOLO's own bounding-box jitter, so the stability counter never climbs.
MAX_LOST_FRAMES = _env_int("VISION_MAX_LOST_FRAMES", 15)
# How many consecutive stable frames a candidate needs before it is locked.
LOCK_STABILITY_FRAMES = _env_int("VISION_LOCK_STABILITY_FRAMES", 10)
# Max allowed frame-to-frame change in bbox height ratio (fraction of frame height).
LOCK_STABILITY_TOLERANCE = _env_float("VISION_LOCK_STABILITY_TOLERANCE", 0.005)
# Max allowed drift in horizontal centre (fraction of frame width) from where the
# candidate was first seen.
LOCK_X_STABILITY_TOLERANCE = _env_float("VISION_LOCK_X_STABILITY_TOLERANCE", 0.002)

# debug/testing switches
DEBUG_OVERLAY = True # draw detection boxes and status text on the output video
BYPASS_FACE_CONFIRMATION = True # skip MTCN confimration and lock any close person
BYPASS_LOCKING = False # do not create locks, just run detection and face confirmation
