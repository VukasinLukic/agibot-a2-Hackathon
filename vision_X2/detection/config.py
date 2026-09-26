import os

try:
    from .defaults import DEFAULT_CAMERA_FOURCC, DEFAULT_CAMERA_FPS, DEFAULT_CARD_CAPTURE_FPS, DEFAULT_DETECTION_FPS
except ImportError:
    from defaults import DEFAULT_CAMERA_FOURCC, DEFAULT_CAMERA_FPS, DEFAULT_CARD_CAPTURE_FPS, DEFAULT_DETECTION_FPS


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
CONF_THRESHOLD = _env_float("VISION_CONF_THRESHOLD", 0.80)

CAMERA_ID = _env_camera_id("VISION_CAMERA_ID", 6)
CAMERA_SOURCE_TYPE = os.getenv("VISION_CAMERA_SOURCE_TYPE", "opencv").strip().lower() or "opencv"
ROS_TOPIC = os.getenv("VISION_ROS_TOPIC") or None
ROS_DOMAIN_ID = os.getenv("VISION_ROS_DOMAIN_ID", os.getenv("ROS_DOMAIN_ID", "232"))
ROS_LOCALHOST_ONLY = os.getenv("VISION_ROS_LOCALHOST_ONLY", os.getenv("ROS_LOCALHOST_ONLY", "0"))
ROS_FASTDDS_PROFILE = os.getenv(
    "VISION_ROS_FASTDDS_PROFILE",
    os.getenv(
        "FASTRTPS_DEFAULT_PROFILES_FILE",
        "/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml",
    ),
)
CAMERA_WIDTH = _env_int("VISION_CAMERA_WIDTH", None)
CAMERA_HEIGHT = _env_int("VISION_CAMERA_HEIGHT", None)
CAMERA_FPS = _env_float("VISION_CAMERA_FPS", DEFAULT_CAMERA_FPS)
CAMERA_FOURCC = os.getenv("VISION_CAMERA_FOURCC", DEFAULT_CAMERA_FOURCC).strip().upper() or None
CAMERA_BUFFER_SIZE = _env_int("VISION_CAMERA_BUFFER_SIZE", None)
CAMERA_OPEN_ATTEMPTS = max(1, _env_int("VISION_CAMERA_OPEN_ATTEMPTS", 3))
CAMERA_OPEN_RETRY_DELAY_SECONDS = max(
    0.1,
    _env_float("VISION_CAMERA_OPEN_RETRY_DELAY_SECONDS", 1.0),
)
CAMERA_READ_TIMEOUT_SECONDS = max(
    1.0,
    _env_float("VISION_CAMERA_READ_TIMEOUT_SECONDS", 6.0),
)

# Cap detector work. Set VISION_DETECTION_FPS=0 to run unthrottled.
DETECTION_FPS = _env_float("VISION_DETECTION_FPS", DEFAULT_DETECTION_FPS)
CARD_CAPTURE_FPS = _env_float("VISION_CARD_CAPTURE_FPS", DEFAULT_CARD_CAPTURE_FPS)
CARD_CAPTURE_REACQUIRE_TIMEOUT_S = _env_float("VISION_CARD_CAPTURE_REACQUIRE_TIMEOUT_S", 10.0)
IDLE_FPS = _env_float("VISION_IDLE_FPS", 2.0)
STATUS_POLL_INTERVAL_SECONDS = _env_float("VISION_STATUS_POLL_INTERVAL_SECONDS", 0.5)
STATUS_TIMEOUT_SECONDS = _env_float("VISION_STATUS_TIMEOUT_SECONDS", 0.25)
SUPERVISOR_URL = os.getenv("VISION_SUPERVISOR_URL", "http://localhost:8080").rstrip("/")
STATUS_URL = os.getenv("VISION_STATUS_URL", f"{SUPERVISOR_URL}/api/vision").strip()
TARGET_PUBLISH_HZ = max(0.2, _env_float("VISION_TARGET_PUBLISH_HZ", 2.0))
TARGET_MIN_DELTA = max(0.0, _env_float("VISION_TARGET_MIN_DELTA", 0.02))
TARGET_POST_TIMEOUT_SECONDS = max(
    0.05,
    _env_float("VISION_TARGET_POST_TIMEOUT_SECONDS", 0.2),
)
# FACE_CAPTURE_RESULT_URL defined locally in main.py alongside the other supervisor URLs, not here --> DELETED TO TRY FACE RECOGNITION
STATS_INTERVAL_SECONDS = _env_float("VISION_STATS_INTERVAL_SECONDS", 5.0)
OPENCV_NUM_THREADS = _env_int("VISION_OPENCV_NUM_THREADS", 1)
TORCH_NUM_THREADS = _env_int("VISION_TORCH_NUM_THREADS", 1)

# distance proxy
CLOSE_HEIGHT_RATIO = _env_float("VISION_CLOSE_HEIGHT_RATIO", 0.50)

# face confirmation before creating a new lock
FACE_MIN_SIZE = 70
FACE_MIN_WIDTH = 40
FACE_MIN_HEIGHT = 40
FACE_CONF_THRESHOLD = 0.90
FACE_CROP_HEIGHT_RATIO = 0.60

# face identity (recognition + enrollment)
# VISION_IDENTITY_INTERVAL_FRAMES = _env_int("VISION_IDENTITY_INTERVAL_FRAMES", 10)  # --> DELETED TO TRY FACE RECOGNITION
VISION_FACE_MATCH_THRESHOLD = _env_float("VISION_FACE_MATCH_THRESHOLD", 0.62)
VISION_FACE_DEDUPE_THRESHOLD = _env_float("VISION_FACE_DEDUPE_THRESHOLD", 0.75)
VISION_IDENTITY_RETRY_SECONDS = max(
    1.0,
    _env_float("VISION_IDENTITY_RETRY_SECONDS", 5.0),
)
VISION_FACE_STORE_PATH = os.getenv(
    "VISION_FACE_STORE_PATH",
    "robot_supervisor_v2/data/faces.db",
)
VISION_FACE_CAPTURE_STABILITY_FRAMES = _env_int("VISION_FACE_CAPTURE_STABILITY_FRAMES", 5)
VISION_FACE_CAPTURE_MIN_BLUR = _env_float("VISION_FACE_CAPTURE_MIN_BLUR", 60.0)

# lock behavior - after that number of frames without same track ID, the lock is removed and a new lock can be created again
MAX_LOST_FRAMES = 15
LOCK_STABILITY_FRAMES = _env_int("VISION_LOCK_STABILITY_FRAMES", 10) # how many frames the candidate needs to be to be considred stable for locki
LOCK_STABILITY_TOLERANCE = _env_float("VISION_LOCK_STABILITY_TOLERANCE", 0.005)
LOCK_X_STABILITY_TOLERANCE = _env_float("VISION_LOCK_X_STABILITY_TOLERANCE", 0.002)

# debug/testing switches
DEBUG_OVERLAY = True # draw detection boxes and status text on the output video
BYPASS_FACE_CONFIRMATION = True # skip MTCN confimration and lock any close person
BYPASS_LOCKING = False # do not create locks, just run detection and face confirmation
