import cv2
import base64
import logging
import os
import sys
import time
from pathlib import Path

# Ensure repository root is on sys.path when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
import requests

from livekit_shared.video_devices import resolve_camera_device

from config import (
    MODEL_NAME,
    YOLO_IMAGE_SIZE,
    PERSON_CLASS_ID,
    CONF_THRESHOLD,
    CAMERA_ID,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
    CAMERA_FOURCC,
    CAMERA_BUFFER_SIZE,
    DETECTION_FPS,
    CARD_CAPTURE_FPS,
    CARD_CAPTURE_REACQUIRE_TIMEOUT_S,
    IDLE_FPS,
    STATUS_POLL_INTERVAL_SECONDS,
    STATUS_TIMEOUT_SECONDS,
    STATUS_URL,
    STATS_INTERVAL_SECONDS,
    OPENCV_NUM_THREADS,
    TORCH_NUM_THREADS,
    CLOSE_HEIGHT_RATIO,
    VISION_PRESENCE_LOG_INTERVAL_S,
    FACE_CONF_THRESHOLD,
    FACE_CROP_HEIGHT_RATIO,
    FACE_MIN_HEIGHT,
    FACE_MIN_SIZE,
    FACE_MIN_WIDTH,
    VISION_FACE_MATCH_THRESHOLD,
    VISION_FACE_DEDUPE_THRESHOLD,
    VISION_FACE_STORE_PATH,
    VISION_IDENTITY_RETRY_SECONDS,
    MAX_LOST_FRAMES,
    LOCK_STABILITY_FRAMES,
    LOCK_STABILITY_TOLERANCE,
    LOCK_X_STABILITY_TOLERANCE,
    BYPASS_FACE_CONFIRMATION,
    BYPASS_LOCKING,
    ROS_STARTUP_TIMEOUT_S,
)


from detector import PersonDetector
from card_capture import CardCaptureProcessor
from person_manager import add_person_geometry, mark_close_persons, select_closest_person
from lock_manager import LockManager
from ros2_capture import is_ros2_camera_id, Ros2VideoCapture

# The face-recognition stack (facenet-pytorch) is OPTIONAL. Person detection and
# conversation dispatch are the critical path and must keep working without it -
# a missing or mismatched facenet/torchvision install must not take the whole
# detector down. If these imports fail we log loudly once and run with face
# recognition permanently disabled for the life of the process.
FACE_STACK_AVAILABLE = True
FACE_STACK_IMPORT_ERROR = None
try:
    from face_detector import FaceDetector
    from face_embedder import FaceEmbedder
    from face_store import FaceStore
    from face_capture import FaceCaptureProcessor
except Exception as _face_import_exc:  # ImportError, or RuntimeError from a torch/torchvision mismatch
    FACE_STACK_AVAILABLE = False
    FACE_STACK_IMPORT_ERROR = _face_import_exc
    FaceDetector = None
    FaceEmbedder = None
    FaceStore = None
    FaceCaptureProcessor = None

SUPERVISOR_URL = os.getenv("VISION_SUPERVISOR_URL", "http://localhost:8080").rstrip("/")
PERSON_DETECTED_URL = os.getenv(
    "VISION_PERSON_DETECTED_URL",
    f"{SUPERVISOR_URL}/api/vision/person_detected",
)
PERSON_LEFT_URL = os.getenv(
    "VISION_PERSON_LEFT_URL",
    f"{SUPERVISOR_URL}/api/vision/person_left",
)
CARD_CAPTURE_RESULT_URL = os.getenv(
    "VISION_CARD_CAPTURE_RESULT_URL",
    f"{SUPERVISOR_URL}/api/vision/card-capture/result",
)
FACE_CAPTURE_RESULT_URL = os.getenv(
    "VISION_FACE_CAPTURE_RESULT_URL",
    f"{SUPERVISOR_URL}/api/vision/face/result",
)
FACE_FORGET_RESULT_URL = os.getenv(
    "VISION_FACE_FORGET_RESULT_URL",
    f"{SUPERVISOR_URL}/api/vision/face/forget/result",
)
CARD_CAPTURE_REACQUIRE_TIMEOUT_REASON = "card_capture_reacquire_timeout"


def _fourcc_to_string(value):
    code = int(value or 0)
    if code <= 0:
        return "auto"
    return "".join(chr((code >> 8 * index) & 255) for index in range(4))


def _card_capture_is_active(request_state):
    if not request_state.get("active") or not request_state.get("request_id"):
        return False

    expires_at = request_state.get("expires_at")
    if expires_at is None:
        return True

    try:
        return time.time() < float(expires_at)
    except (TypeError, ValueError):
        return True


def _inactive_card_capture_state():
    return {
        "active": False,
        "request_id": None,
        "state": "idle",
    }


def _card_capture_terminal_needs_reacquire(request_state):
    return str(request_state.get("state") or "").lower() in {"captured", "expired"}


def _face_capture_is_active(request_state):
    return _card_capture_is_active(request_state)


def _inactive_face_capture_state():
    return {
        "active": False,
        "request_id": None,
        "distance": None,
        "state": "idle",
    }


def _set_publisher_locked_state(processor, locked):
    publisher = getattr(processor, "publisher", None)
    if hasattr(publisher, "last_locked"):
        publisher.last_locked = bool(locked)


class CardCaptureReacquireHold:
    def __init__(self, timeout_s, *, log=None):
        self.timeout_s = max(float(timeout_s or 0.0), 0.0)
        self.log = log or logging.getLogger(__name__)
        self.request_id = None
        self.started_at = None
        self.reacquired = False
        self.left_published = False
        self.track_id = None

    @property
    def active(self):
        return self.request_id is not None

    @property
    def armed(self):
        return self.started_at is not None

    def observe(self, request_id):
        if not request_id:
            self.clear("missing_request")
            return
        if self.request_id == request_id:
            return
        self.request_id = request_id
        self.started_at = None
        self.reacquired = False
        self.left_published = False
        self.track_id = None
        self.log.info(
            "Card capture reacquire observation started request_id=%s",
            request_id,
        )

    def arm(self, request_id, now):
        self.observe(request_id)
        if not self.active or self.reacquired or self.left_published:
            return
        self.started_at = now
        self.log.info(
            "Card capture reacquire timeout armed request_id=%s timeout_s=%.2f",
            request_id,
            self.timeout_s,
        )

    def clear(self, reason):
        if self.active:
            self.log.info(
                "Card capture reacquire window cleared request_id=%s reason=%s reacquired=%s left_published=%s",
                self.request_id,
                reason,
                self.reacquired,
                self.left_published,
            )
        self.request_id = None
        self.started_at = None
        self.reacquired = False
        self.left_published = False
        self.track_id = None

    def note_visible_lock(self, locked_track_id):
        if not self.active or self.reacquired or locked_track_id is None:
            return False
        self.reacquired = True
        self.track_id = locked_track_id
        self.log.info(
            "Card capture reacquired visible person request_id=%s track_id=%s",
            self.request_id,
            locked_track_id,
        )
        return True

    def should_publish_timeout_left(self, now):
        if (
            not self.active
            or self.reacquired
            or self.left_published
            or self.started_at is None
        ):
            return False
        return now - self.started_at >= self.timeout_s

    def mark_left_published(self):
        self.left_published = True


class VisionEventPublisher:

    def __init__(self):
        self.last_locked = False
        self.last_identity_key = None
        self.timeout = 1.0
        self.log = logging.getLogger(__name__)

    def reset(self):
        self.last_locked = False
        self.last_identity_key = None

    @staticmethod
    def _identity_key(identity):
        if not isinstance(identity, dict):
            return None
        return (
            identity.get("status"),
            identity.get("face_id"),
            identity.get("name"),
        )

    def publish_if_changed(self, locked_track_id, timings=None, *, reason=None, force=False, identity=None):

        is_locked = locked_track_id is not None
        state_changed = is_locked != self.last_locked
        identity_key = self._identity_key(identity) if is_locked else None
        identity_changed = is_locked and identity_key != self.last_identity_key
        if is_locked == self.last_locked and not identity_changed and not force:
            return None

        self.last_locked = is_locked
        self.last_identity_key = identity_key
        url = PERSON_DETECTED_URL if is_locked else PERSON_LEFT_URL

        payload = {
            "locked": is_locked,
            "track_id": locked_track_id,
            "timestamp": time.time(),
        }
        if timings:
            payload["timings"] = timings
        if reason:
            payload["reason"] = reason
        if is_locked and identity:
            payload["identity"] = identity

        try:
            post_started_at = time.perf_counter()
            print(f"Publishing event: {payload} to {url}")
            response = requests.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            post_elapsed_s = time.perf_counter() - post_started_at
            self.log.info(
                "VISION POST state=%s track_id=%s post_s=%.4f",
                "locked" if is_locked else "unlocked",
                locked_track_id,
                post_elapsed_s,
            )
        except requests.RequestException as exc:
            self.log.warning("Failed POST to %s: %s", url, exc)

        if identity_changed and is_locked and not state_changed:
            return "identity_updated"
        return "locked" if is_locked else "unlocked"


class CardCaptureResultPublisher:
    def __init__(self):
        self.timeout = 1.0
        self.log = logging.getLogger(__name__)
        self._posted_request_ids = set()

    def reset(self):
        self._posted_request_ids.clear()

    def publish_once(self, result):
        request_id = result.get("request_id")
        if not request_id:
            return False
        if request_id in self._posted_request_ids:
            return False

        try:
            post_started_at = time.perf_counter()
            response = requests.post(CARD_CAPTURE_RESULT_URL, json=result, timeout=self.timeout)
            response.raise_for_status()
            self._posted_request_ids.add(request_id)
            post_elapsed_s = time.perf_counter() - post_started_at
            self.log.info(
                "CARD CAPTURE POST request_id=%s status=%s post_s=%.4f",
                request_id,
                result.get("status"),
                post_elapsed_s,
            )
            return True
        except requests.RequestException as exc:
            self.log.warning("Failed POST to %s: %s", CARD_CAPTURE_RESULT_URL, exc)
            return False


class FaceCaptureResultPublisher:
    def __init__(self):
        self.timeout = 1.0
        self.log = logging.getLogger(__name__)
        self._posted_request_ids = set()

    def reset(self):
        self._posted_request_ids.clear()

    def publish_once(self, result):
        request_id = result.get("request_id")
        if not request_id:
            return False
        if request_id in self._posted_request_ids:
            return False

        try:
            post_started_at = time.perf_counter()
            response = requests.post(FACE_CAPTURE_RESULT_URL, json=result, timeout=self.timeout)
            response.raise_for_status()
            self._posted_request_ids.add(request_id)
            post_elapsed_s = time.perf_counter() - post_started_at
            self.log.info(
                "FACE CAPTURE POST request_id=%s status=%s post_s=%.4f",
                request_id,
                result.get("status"),
                post_elapsed_s,
            )
            return True
        except requests.RequestException as exc:
            self.log.warning("Failed POST to %s: %s", FACE_CAPTURE_RESULT_URL, exc)
            return False


def _publish_face_forget_result(request_id, status, log):
    try:
        response = requests.post(
            FACE_FORGET_RESULT_URL,
            json={"request_id": request_id, "status": status, "timestamp": time.time()},
            timeout=1.0,
        )
        response.raise_for_status()
        log.info("FACE FORGET POST request_id=%s status=%s", request_id, status)
    except requests.RequestException as exc:
        log.warning("Failed POST to %s: %s", FACE_FORGET_RESULT_URL, exc)





#   __  __
#  |  \/  |   ___    ___   __      __
#  | |\/| |  / _ \  / _ \  \ \ /\ / /
#  | |  | | |  __/ | (_) |  \ V  V /
#  |_|  |_|  \___|  \___/    \_/\_/





class VisionRuntimeState:
    def __init__(
        self,
        status_url,
        poll_interval_seconds=0.5,
        timeout_seconds=0.25,
        default_active=True,
    ):
        self.status_url = status_url
        self.poll_interval_seconds = max(float(poll_interval_seconds or 0.0), 0.0)
        self.timeout_seconds = max(float(timeout_seconds or 0.0), 0.01)
        self.active = bool(default_active)
        self.dispatch_active = bool(default_active)
        self.dispatch_paused = False
        self.card_capture = _inactive_card_capture_state()
        self.face_capture = _inactive_face_capture_state()
        self.face_forget = {"request_id": None, "face_id": None, "name": None, "state": "idle"}
        self.face_recognition_enabled = False
        self._next_poll_at = 0.0
        self._last_error_log_at = 0.0
        self.log = logging.getLogger(__name__)

    def refresh_if_due(self, now=None):
        now = time.monotonic() if now is None else now
        if now < self._next_poll_at:
            return {
                "dispatch_active": self.dispatch_active,
                "dispatch_paused": self.dispatch_paused,
                "card_capture": self.card_capture,
                "face_capture": self.face_capture,
                "face_forget": self.face_forget,
                "face_recognition_enabled": self.face_recognition_enabled,
                "recognition_active": self.active and self.face_recognition_enabled,
            }

        self._next_poll_at = now + self.poll_interval_seconds
        try:
            response = requests.get(self.status_url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                if "active" in payload:
                    self.active = bool(payload["active"])
                    self.dispatch_active = self.active

                features = payload.get("features")
                if isinstance(features, dict):
                    dispatch = features.get("dispatch")
                    if isinstance(dispatch, dict):
                        dispatch_base_active = self.dispatch_active
                        if "enabled" in dispatch:
                            dispatch_base_active = bool(dispatch["enabled"])
                        if "active" in dispatch:
                            dispatch_base_active = bool(dispatch["active"]) if "enabled" not in dispatch else dispatch_base_active
                        self.dispatch_paused = bool(dispatch.get("paused", False))
                        self.dispatch_active = dispatch_base_active

                    card_capture = features.get("card_capture")
                    if isinstance(card_capture, dict):
                        self.card_capture = {
                            "active": bool(card_capture.get("active")),
                            "request_id": card_capture.get("request_id"),
                            "state": card_capture.get("state", "idle"),
                            "target_type": card_capture.get("target_type"),
                            "source": card_capture.get("source"),
                            "requested_at": card_capture.get("requested_at"),
                            "expires_at": card_capture.get("expires_at"),
                        }
                    else:
                        self.card_capture = _inactive_card_capture_state()

                    face_capture = features.get("face_capture")
                    if isinstance(face_capture, dict):
                        self.face_capture = {
                            "active": bool(face_capture.get("active")),
                            "request_id": face_capture.get("request_id"),
                            "distance": face_capture.get("distance"),
                            "name": face_capture.get("name"),
                            "state": face_capture.get("state", "idle"),
                            "requested_at": face_capture.get("requested_at"),
                            "expires_at": face_capture.get("expires_at"),
                        }
                    else:
                        self.face_capture = _inactive_face_capture_state()

                    face_forget = features.get("face_forget")
                    if isinstance(face_forget, dict):
                        self.face_forget = {
                            "request_id": face_forget.get("request_id"),
                            "face_id": face_forget.get("face_id"),
                            "name": face_forget.get("name"),
                            "state": face_forget.get("state", "idle"),
                        }
                    else:
                        self.face_forget = {"request_id": None, "face_id": None, "name": None, "state": "idle"}

                    face_recognition = features.get("face_recognition")
                    if isinstance(face_recognition, dict):
                        self.face_recognition_enabled = bool(face_recognition.get("enabled", False))
                    else:
                        self.face_recognition_enabled = False
                else:
                    self.card_capture = _inactive_card_capture_state()
                    self.face_capture = _inactive_face_capture_state()
                    self.face_forget = {"request_id": None, "face_id": None, "name": None, "state": "idle"}
                    self.face_recognition_enabled = False
        except requests.RequestException as exc:
            if now - self._last_error_log_at >= 5.0:
                self.log.warning(
                    "Failed to poll vision runtime state from %s: %s; keeping active=%s",
                    self.status_url,
                    exc,
                    self.active,
                )
                self._last_error_log_at = now
        except ValueError as exc:
            if now - self._last_error_log_at >= 5.0:
                self.log.warning(
                    "Invalid vision runtime state response from %s: %s; keeping active=%s",
                    self.status_url,
                    exc,
                    self.active,
                )
                self._last_error_log_at = now

        return {
            "dispatch_active": self.dispatch_active,
            "dispatch_paused": self.dispatch_paused,
            "card_capture": self.card_capture,
            "face_capture": self.face_capture,
            "face_forget": self.face_forget,
            "face_recognition_enabled": self.face_recognition_enabled,
            "recognition_active": self.active and self.face_recognition_enabled,
        }


class PersonPresenceLogger:

    # Each status line reports the two gates that decide whether a detected
    # person becomes a *locked* person (which is what actually starts a
    # conversation), because a person can be detected continuously and still never
    # trigger anything:
    #   1. distance gate  --> height_ratio must reach CLOSE_HEIGHT_RATIO
    #   2. stability gate --> the candidate must hold still for
    #                       LOCK_STABILITY_FRAMES consecutive frames, within
    #                       LOCK_STABILITY_TOLERANCE (height) and
    #                       LOCK_X_STABILITY_TOLERANCE (horizontal)


    def __init__(self, interval_s=None):
        self.interval_s = (
            VISION_PRESENCE_LOG_INTERVAL_S if interval_s is None else max(float(interval_s), 0.0)
        )
        self.log = logging.getLogger(__name__)
        self._present = False
        self._present_since = None
        self._next_status_log_at = 0.0
        self._last_locked_track_id = None

    def reset(self):
        self._present = False
        self._present_since = None
        self._next_status_log_at = 0.0
        self._last_locked_track_id = None

    @staticmethod
    def _stamp():
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    def update(self, persons, candidate, locked_track_id, stable_count):
        now = time.monotonic()
        count = len(persons)
        best = max(persons, key=lambda p: p.get("height_ratio", 0.0), default=None)

        if count and not self._present:
            self._present = True
            self._present_since = now
            self._next_status_log_at = 0.0  # force an immediate status line
            self.log.info(
                "PERSON DETECTED at %s | persons=%s%s",
                self._stamp(),
                count,
                self._describe_best(best),
            )

        elif not count and self._present:
            visible_s = now - self._present_since if self._present_since else 0.0
            self._present = False
            self._present_since = None
            self.log.info(
                "NO PERSON at %s | frame empty after %.1fs of presence",
                self._stamp(),
                visible_s,
            )

        if self._present and now >= self._next_status_log_at:
            self._next_status_log_at = now + self.interval_s
            self.log.info(
                "PERSON PRESENT | persons=%s%s | %s | locked=%s",
                count,
                self._describe_best(best),
                self._describe_lock_progress(best, candidate, stable_count),
                locked_track_id if locked_track_id is not None else "no",
            )

        if locked_track_id != self._last_locked_track_id:
            if locked_track_id is not None:
                self.log.info(
                    "LOCK ACQUIRED at %s track_id=%s -> posting person_detected "
                    "(this is what starts a conversation)",
                    self._stamp(),
                    locked_track_id,
                )
            else:
                self.log.info(
                    "LOCK RELEASED at %s (previous track_id=%s) -> posting person_left",
                    self._stamp(),
                    self._last_locked_track_id,
                )
            self._last_locked_track_id = locked_track_id

    @staticmethod
    def _describe_best(best):
        if best is None:
            return ""
        return (
            f" best_track={best.get('track_id')}"
            f" conf={best.get('confidence', 0.0):.2f}"
            f" height_ratio={best.get('height_ratio', 0.0):.3f}"
            f" center_x={best.get('center_x_ratio', 0.0):.3f}"
        )

    @staticmethod
    def _describe_lock_progress(best, candidate, stable_count):
        if best is None:
            return "no candidate"
        height_ratio = best.get("height_ratio", 0.0)
        if candidate is None:
            # Distance gate: select_closest_person() only returns people whose
            # bounding box is tall enough to count as "close".
            return (
                f"NOT a candidate: too far/small "
                f"(height_ratio {height_ratio:.3f} < CLOSE_HEIGHT_RATIO {CLOSE_HEIGHT_RATIO:.2f})"
            )
        return (
            f"candidate track={candidate.get('track_id')} "
            f"holding_still {stable_count}/{LOCK_STABILITY_FRAMES} frames "
            f"(needs |dheight|<={LOCK_STABILITY_TOLERANCE:g}, |dx|<={LOCK_X_STABILITY_TOLERANCE:g})"
        )


class PersonLockProcessor:

     def __init__(self, publisher=None):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.publisher = publisher or VisionEventPublisher()
        self.detection_counter = None
        self.last_locked_visible = False
        self.presence_logger = PersonPresenceLogger()

        self.detector = PersonDetector(
            model_name=MODEL_NAME,
            person_class_id=PERSON_CLASS_ID,
            conf_threshold=CONF_THRESHOLD,
            device=self.device,
            image_size=YOLO_IMAGE_SIZE,
        )
        if FACE_STACK_AVAILABLE:
            self.face_detector = FaceDetector(
                device=self.device,
                min_face_size=FACE_MIN_SIZE,
                min_face_width=FACE_MIN_WIDTH,
                min_face_height=FACE_MIN_HEIGHT,
                min_face_confidence=FACE_CONF_THRESHOLD,
                crop_height_ratio=FACE_CROP_HEIGHT_RATIO,
            )
            self.face_embedder = FaceEmbedder(device=self.device)
            self.face_store = FaceStore(VISION_FACE_STORE_PATH)
        else:
            self.face_detector = None
            self.face_embedder = None
            self.face_store = None
        self._identity_track_id = None
        self._resolved_identity = None
        self._next_identity_attempt_at = 0.0
        self.lock_manager = LockManager(
            max_lost_frames=MAX_LOST_FRAMES,
            stability_frames=LOCK_STABILITY_FRAMES,
            stability_tolerance=LOCK_STABILITY_TOLERANCE,
            x_stability_tolerance=LOCK_X_STABILITY_TOLERANCE,
        )

     def reset(self, *, reset_publisher=True):
        self.detection_counter = None
        self.last_locked_visible = False
        self.presence_logger.reset()
        self.lock_manager.reset()
        self._identity_track_id = None
        self._resolved_identity = None
        self._next_identity_attempt_at = 0.0
        if reset_publisher:
            self.publisher.reset()

     def process_frame(self, frame, suppress_unlock_event=False, resolve_identity=True):
        frame_started_at = time.perf_counter()
        previous_locked_track_id = self.lock_manager.locked_track_id

        detect_started_at = time.perf_counter()
        persons = self.detector.detect_and_track(frame)
        # person detected, previous counter is none / frame = 0, and no persons locekd 
        if len(persons) > 0 and self.detection_counter is None and previous_locked_track_id is None:
            self.detection_counter = time.perf_counter()
        detect_elapsed_s = time.perf_counter() - detect_started_at
        persons = [add_person_geometry(person, frame.shape) for person in persons]
        persons = mark_close_persons(persons, close_height_ratio=CLOSE_HEIGHT_RATIO)

        candidate = select_closest_person(persons)

        face_started_at = time.perf_counter()
        if candidate is not None:
            if BYPASS_FACE_CONFIRMATION:
                candidate["face_confirmed"] = True
            else: 
                candidate["face_confirmed"] = True
                # candidate["face_confirmed"] = self.face_detector.confirm_person_face(frame, candidate)
            
            if not candidate["face_confirmed"]:
                candidate = None
        face_elapsed_s = time.perf_counter() - face_started_at

        detection_total_elapsed_s = 0
        identity = None
        if BYPASS_LOCKING:
            locked_track_id = None
        else:
            locked_track_id = self.lock_manager.update(persons, candidate_person=candidate)
            if (
                locked_track_id is not None
                and previous_locked_track_id is None
                and self.detection_counter is not None
            ):
                detection_total_elapsed_s = time.perf_counter() - self.detection_counter
                self.detection_counter = None
            elif locked_track_id is None and not persons:
                self.detection_counter = None

            if locked_track_id is None:
                self._identity_track_id = None
                self._resolved_identity = None
                self._next_identity_attempt_at = 0.0
            elif self._identity_track_id != locked_track_id:
                self._identity_track_id = locked_track_id
                self._resolved_identity = None
                self._next_identity_attempt_at = 0.0

            if resolve_identity and locked_track_id is not None:
                locked_person = next(
                    (
                        person
                        for person in persons
                        if person.get("track_id") == locked_track_id
                    ),
                    candidate,
                )
                identity_known = (
                    isinstance(self._resolved_identity, dict)
                    and self._resolved_identity.get("status") == "known"
                )
                now = time.monotonic()
                if (
                    not identity_known
                    and locked_person is not None
                    and now >= self._next_identity_attempt_at
                ):
                    self._resolved_identity = self._resolve_identity(frame, locked_person)
                    self._next_identity_attempt_at = now + VISION_IDENTITY_RETRY_SECONDS
                    logging.info(
                        "Face identity attempt track_id=%s status=%s retry_s=%.1f",
                        locked_track_id,
                        self._resolved_identity.get("status")
                        if isinstance(self._resolved_identity, dict)
                        else "unknown",
                        VISION_IDENTITY_RETRY_SECONDS,
                    )
                identity = self._resolved_identity

        # Report what we saw and, when nothing locks, which gate blocked it.
        self.presence_logger.update(
            persons,
            candidate,
            locked_track_id,
            self.lock_manager.candidate_stable_count,
        )

        active_ids = {person["track_id"] for person in persons}
        self.last_locked_visible = locked_track_id is not None and locked_track_id in active_ids

        total_elapsed_s = time.perf_counter() - frame_started_at
        event = None
        should_publish = True
        if suppress_unlock_event:
            should_publish = False
        if should_publish:
            event = self.publisher.publish_if_changed(
                locked_track_id,
                timings={
                    "detect_s": round(detect_elapsed_s, 4),
                    "face_s": round(face_elapsed_s, 4),
                    "total_s": round(total_elapsed_s, 4),
                    "detection_counter_s": round(detection_total_elapsed_s, 4),
                },
                identity=identity,
            )
        return locked_track_id, event

     def _resolve_identity(self, frame, candidate):
        if not FACE_STACK_AVAILABLE:
            return {"status": "unknown"}
        try:
            faces = self.face_detector.detect_person_faces(frame, candidate)
            if not faces:
                return {"status": "unknown"}

            best_face = max(faces, key=lambda face: face["width"] * face["height"])
            embedding = self.face_embedder.embed(frame, best_face["bbox"])
            if embedding is None:
                return {"status": "unknown"}

            match = self.face_store.match(embedding, VISION_FACE_MATCH_THRESHOLD)
            if match is None:
                return {"status": "unknown"}
            return {
                "status": "known",
                "name": match["name"],
                "display_name": match.get("display_name") or match["name"],
                "canonical_name": match.get("canonical_name") or match["name"],
                "aliases": match.get("aliases") or [],
                "face_id": match["face_id"],
            }
        except Exception:
            logging.exception("Face identity resolution failed")
            return {"status": "unknown"}

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if OPENCV_NUM_THREADS and OPENCV_NUM_THREADS > 0:
        cv2.setNumThreads(OPENCV_NUM_THREADS)
    if TORCH_NUM_THREADS and TORCH_NUM_THREADS > 0:
        torch.set_num_threads(TORCH_NUM_THREADS)

    if not FACE_STACK_AVAILABLE:
        logging.error(
            "Face recognition DISABLED: could not import the facenet-pytorch stack (%s: %s). "
            "Person detection and conversation dispatch will run normally. To enable it, make sure "
            "the interpreter in the vision-controller's python_bin has facenet-pytorch AND a "
            "matching torch/torchvision pair - note that a 'jetson_deps' entry on pythonpath "
            "shadows the venv's torch and can break that pairing.",
            type(FACE_STACK_IMPORT_ERROR).__name__,
            FACE_STACK_IMPORT_ERROR,
        )

    processor = PersonLockProcessor()
    card_processor = CardCaptureProcessor()
    card_result_publisher = CardCaptureResultPublisher()
    face_processor = (
        FaceCaptureProcessor(processor.face_detector, processor.face_embedder)
        if FACE_STACK_AVAILABLE
        else None
    )
    face_result_publisher = FaceCaptureResultPublisher()
    enrollment_state = {
        "name": None, "embedding_close": None, "embedding_far": None,
        "image_close": None, "image_far": None,
    }

    use_ros2 = is_ros2_camera_id(CAMERA_ID)
    if use_ros2:
        camera_source = CAMERA_ID
        cap = Ros2VideoCapture(str(CAMERA_ID), startup_timeout=ROS_STARTUP_TIMEOUT_S)
    else:
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") and hasattr(cv2, "CAP_V4L2") else 0
        camera_source = (
            resolve_camera_device(CAMERA_ID)
            if isinstance(CAMERA_ID, str)
            else CAMERA_ID
        )
        cap = cv2.VideoCapture(camera_source, backend) if backend else cv2.VideoCapture(camera_source)

        if CAMERA_FOURCC:
            if len(CAMERA_FOURCC) != 4:
                logging.warning("Ignoring invalid VISION_CAMERA_FOURCC=%r; expected four characters", CAMERA_FOURCC)
            else:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*CAMERA_FOURCC))
        if CAMERA_WIDTH:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        if CAMERA_HEIGHT:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        if CAMERA_FPS:
            cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
        if CAMERA_BUFFER_SIZE is not None and hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            cap.set(cv2.CAP_PROP_BUFFERSIZE, CAMERA_BUFFER_SIZE)

    if not cap.isOpened():
        raise RuntimeError(f"Unable to open camera {CAMERA_ID}")

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
    actual_fps = cap.get(cv2.CAP_PROP_FPS) or None
    actual_fourcc = _fourcc_to_string(cap.get(cv2.CAP_PROP_FOURCC))
    logging.info(
        "Vision detector ready: device=%s model=%s imgsz=%s camera=%s actual=%sx%s @ %s fps fourcc=%s detection_fps=%s card_capture_fps=%s idle_fps=%s status_url=%s poll_s=%s buffer_size=%s",
        processor.device,
        MODEL_NAME,
        YOLO_IMAGE_SIZE,
        camera_source,
        actual_width or "auto",
        actual_height or "auto",
        f"{actual_fps:.2f}" if actual_fps else "auto",
        actual_fourcc,
        DETECTION_FPS or "unlimited",
        CARD_CAPTURE_FPS or "unlimited",
        IDLE_FPS,
        STATUS_URL,
        STATUS_POLL_INTERVAL_SECONDS,
        CAMERA_BUFFER_SIZE if CAMERA_BUFFER_SIZE is not None else "default",
    )

    frame_interval = 1.0 / DETECTION_FPS if DETECTION_FPS and DETECTION_FPS > 0 else 0.0
    card_frame_interval = 1.0 / CARD_CAPTURE_FPS if CARD_CAPTURE_FPS and CARD_CAPTURE_FPS > 0 else 0.0
    idle_frame_interval = 1.0 / IDLE_FPS if IDLE_FPS and IDLE_FPS > 0 else 0.0
    runtime_state = VisionRuntimeState(
        status_url=STATUS_URL,
        poll_interval_seconds=STATUS_POLL_INTERVAL_SECONDS,
        timeout_seconds=STATUS_TIMEOUT_SECONDS,
        default_active=True,
    )
    tracking_active = runtime_state.dispatch_active
    card_capture_request = runtime_state.card_capture
    card_capture_active = _card_capture_is_active(card_capture_request)
    face_capture_request = runtime_state.face_capture
    face_capture_active = _face_capture_is_active(face_capture_request)
    reacquire_hold = CardCaptureReacquireHold(
        CARD_CAPTURE_REACQUIRE_TIMEOUT_S,
        log=logging.getLogger(__name__),
    )
    if card_capture_active:
        reacquire_hold.observe(card_capture_request.get("request_id"))
    next_frame_at = time.monotonic()
    stats_started_at = next_frame_at
    processed_frames = 0
    card_capture_frames = 0
    face_capture_frames = 0
    idle_frames = 0
    processed_forget_request_id = None
    last_logged_face_reason = None

    try:
        while True:
            state_now = runtime_state.refresh_if_due()
            active_now = bool(
                state_now["dispatch_active"]
                or state_now.get("recognition_active")
            )
            dispatch_paused_now = bool(state_now.get("dispatch_paused"))
            card_capture_now = state_now["card_capture"]
            card_active_now = _card_capture_is_active(card_capture_now)
            if active_now != tracking_active:
                tracking_active = active_now
                logging.info(
                    "Vision dispatch tracker %s",
                    "active" if tracking_active else "inactive",
                )
                if not tracking_active:
                    processor.reset(reset_publisher=not dispatch_paused_now)
                    reacquire_hold.clear("vision_dispatch_tracker_inactive")
                next_frame_at = time.monotonic()

            if (
                card_active_now != card_capture_active
                or card_capture_now.get("request_id") != card_capture_request.get("request_id")
            ):
                previous_card_capture_active = card_capture_active
                card_capture_active = card_active_now
                card_capture_request = card_capture_now
                logging.info(
                    "Card capture tracker %s request_id=%s state=%s",
                    "active" if card_capture_active else "inactive",
                    card_capture_request.get("request_id"),
                    card_capture_request.get("state"),
                )
                if card_capture_active:
                    card_processor.reset(card_capture_request.get("request_id"))
                    if tracking_active:
                        processor.reset(reset_publisher=False)
                    reacquire_hold.observe(card_capture_request.get("request_id"))
                else:
                    card_processor.reset()
                    if previous_card_capture_active:
                        logging.info(
                            "Card capture ended state=%s; resuming normal dispatch evaluation",
                            card_capture_request.get("state"),
                        )
                        if _card_capture_terminal_needs_reacquire(card_capture_request):
                            if reacquire_hold.reacquired:
                                _set_publisher_locked_state(processor, True)
                                reacquire_hold.clear("card_capture_reacquired")
                            else:
                                reacquire_hold.arm(
                                    card_capture_request.get("request_id"),
                                    time.monotonic(),
                                )
                        else:
                            reacquire_hold.clear(
                                f"card_capture_{card_capture_request.get('state') or 'inactive'}"
                            )
                next_frame_at = time.monotonic()
            else:
                card_capture_request = card_capture_now

            face_capture_now = state_now["face_capture"]
            face_active_now = _face_capture_is_active(face_capture_now) and FACE_STACK_AVAILABLE
            if not FACE_STACK_AVAILABLE:
                # No face_processor exists to reset; just track the latest request
                # so a request_id change cannot fall through to it.
                face_capture_request = face_capture_now
                face_capture_active = False
            elif (
                face_active_now != face_capture_active
                or face_capture_now.get("request_id") != face_capture_request.get("request_id")
            ):
                face_capture_active = face_active_now
                face_capture_request = face_capture_now
                logging.info(
                    "Face capture tracker %s request_id=%s distance=%s state=%s",
                    "active" if face_capture_active else "inactive",
                    face_capture_request.get("request_id"),
                    face_capture_request.get("distance"),
                    face_capture_request.get("state"),
                )
                if face_capture_active:
                    face_processor.reset(
                        face_capture_request.get("request_id"),
                        face_capture_request.get("distance"),
                    )
                    last_logged_face_reason = None
                    if tracking_active:
                        processor.reset(reset_publisher=False)
                    if enrollment_state["name"] != face_capture_request.get("name"):
                        enrollment_state = {
                            "name": face_capture_request.get("name"),
                            "embedding_close": None,
                            "embedding_far": None,
                            "image_close": None,
                            "image_far": None,
                        }
                else:
                    face_processor.reset()
                next_frame_at = time.monotonic()
            else:
                face_capture_request = face_capture_now

            face_forget_now = state_now.get("face_forget") or {}
            if (
                FACE_STACK_AVAILABLE
                and face_forget_now.get("state") == "pending"
                and face_forget_now.get("request_id")
                and face_forget_now.get("request_id") != processed_forget_request_id
            ):
                forget_request_id = face_forget_now.get("request_id")
                processed_forget_request_id = forget_request_id
                try:
                    deleted = processor.face_store.delete(
                        face_id=face_forget_now.get("face_id"),
                        name=face_forget_now.get("name"),
                    )
                    forget_status = "deleted" if deleted else "not_found"
                except Exception:
                    logging.exception("Face forget failed request_id=%s", forget_request_id)
                    forget_status = "failed"
                _publish_face_forget_result(forget_request_id, forget_status, logging.getLogger(__name__))

            frame_started_at = time.monotonic()
            capture_started_at = time.monotonic()
            ok, frame = cap.read()
            capture_elapsed_s = time.monotonic() - capture_started_at
            if not ok:
                print("Could not read webcam frame")
                break

            locked_track_id = None
            event = None
            process_elapsed_s = 0.0
            card_process_elapsed_s = 0.0
            card_event = None
            face_process_elapsed_s = 0.0
            face_event = None
            if tracking_active:
                process_started_at = time.monotonic()
                suppress_dispatch_events = card_capture_active or face_capture_active or reacquire_hold.active
                locked_track_id, event = processor.process_frame(
                    frame,
                    suppress_unlock_event=suppress_dispatch_events,
                    resolve_identity=runtime_state.face_recognition_enabled and FACE_STACK_AVAILABLE,
                )
                process_elapsed_s = time.monotonic() - process_started_at
                processed_frames += 1
                if suppress_dispatch_events:
                    event = None
                    if processor.last_locked_visible:
                        reacquire_hold.note_visible_lock(locked_track_id)
                        if not card_capture_active and not reacquire_hold.left_published:
                            _set_publisher_locked_state(processor, True)
                            reacquire_hold.clear("card_capture_reacquired")
                    timeout_now = time.monotonic()
                    if reacquire_hold.should_publish_timeout_left(timeout_now):
                        _set_publisher_locked_state(processor, True)
                        event = processor.publisher.publish_if_changed(
                            None,
                            timings={
                                "detect_s": round(process_elapsed_s, 4),
                                "face_s": 0.0,
                                "total_s": round(process_elapsed_s, 4),
                                "detection_counter_s": 0,
                            },
                            reason=CARD_CAPTURE_REACQUIRE_TIMEOUT_REASON,
                            force=True,
                        )
                        reacquire_hold.mark_left_published()
                        logging.info(
                            "Card capture reacquire timed out request_id=%s timeout_s=%.2f event=%s",
                            reacquire_hold.request_id,
                            reacquire_hold.timeout_s,
                            event,
                        )
                        reacquire_hold.clear("card_capture_reacquire_timeout")

            if card_capture_active:
                card_process_started_at = time.monotonic()
                card_result = card_processor.process_frame(frame, card_capture_request)
                card_process_elapsed_s = time.monotonic() - card_process_started_at
                card_capture_frames += 1
                if card_result and card_result_publisher.publish_once(card_result):
                    card_event = card_result.get("status")
                    card_request_id = card_capture_request.get("request_id")
                    card_capture_active = False
                    card_capture_request = {
                        **_inactive_card_capture_state(),
                        "request_id": card_request_id,
                        "state": card_event or "captured",
                    }
                    runtime_state.card_capture = card_capture_request
                    card_processor.reset()
                    logging.info(
                        "Card capture result received state=%s; resuming normal dispatch evaluation",
                        card_capture_request.get("state"),
                    )
                    if _card_capture_terminal_needs_reacquire(card_capture_request):
                        if reacquire_hold.reacquired:
                            _set_publisher_locked_state(processor, True)
                            reacquire_hold.clear("card_capture_reacquired")
                        else:
                            reacquire_hold.arm(
                                card_capture_request.get("request_id"),
                                time.monotonic(),
                            )
                    else:
                        reacquire_hold.clear(
                            f"card_capture_{card_capture_request.get('state') or 'result'}"
                        )

            if face_capture_active:
                face_process_started_at = time.monotonic()
                face_result = face_processor.process_frame(frame, face_capture_request)
                face_process_elapsed_s = time.monotonic() - face_process_started_at
                face_capture_frames += 1
                current_face_reason = face_processor.last_rejection_reason
                if current_face_reason != last_logged_face_reason:
                    logging.info(
                        "Face capture progress request_id=%s distance=%s name=%s status=%s",
                        face_capture_request.get("request_id"),
                        face_capture_request.get("distance"),
                        face_capture_request.get("name"),
                        current_face_reason or "face_visible_capturing",
                    )
                    last_logged_face_reason = current_face_reason
                if face_result:
                    distance = face_result["metadata"].get("distance")
                    # Embeddings never leave this process: pop it here before the
                    # result is POSTed to the supervisor below, after using it
                    # locally to match/enroll against the on-disk face store.
                    embedding = face_result["metadata"].pop("embedding", None)
                    face_id = None
                    if embedding is not None and distance in ("close", "far"):
                        enrollment_state[f"embedding_{distance}"] = embedding
                        jpeg_base64 = face_result["metadata"].get("face_jpeg_base64")
                        enrollment_state[f"image_{distance}"] = (
                            base64.b64decode(jpeg_base64) if jpeg_base64 else None
                        )
                        if (
                            enrollment_state["name"]
                            and enrollment_state["embedding_close"] is not None
                            and enrollment_state["embedding_far"] is not None
                        ):
                            enrollment = processor.face_store.enroll_or_get(
                                enrollment_state["name"],
                                enrollment_state["embedding_close"],
                                enrollment_state["embedding_far"],
                                image_close=enrollment_state["image_close"],
                                image_far=enrollment_state["image_far"],
                                dedupe_threshold=VISION_FACE_DEDUPE_THRESHOLD,
                            )
                            face_id = enrollment["face_id"]
                            face_result["metadata"]["enrollment_created"] = enrollment["created"]
                            face_result["metadata"]["enrolled_name"] = enrollment["name"]
                            if enrollment.get("score") is not None:
                                face_result["metadata"]["dedupe_score"] = round(
                                    float(enrollment["score"]),
                                    4,
                                )
                            enrollment_state = {
                                "name": None,
                                "embedding_close": None,
                                "embedding_far": None,
                                "image_close": None,
                                "image_far": None,
                            }
                    if face_id:
                        face_result["metadata"]["face_id"] = face_id
                    if face_result_publisher.publish_once(face_result):
                        face_event = face_result.get("status")
                        face_request_id = face_capture_request.get("request_id")
                        face_capture_active = False
                        face_capture_request = {
                            **_inactive_face_capture_state(),
                            "request_id": face_request_id,
                            "state": face_event or "captured",
                        }
                        runtime_state.face_capture = face_capture_request
                        face_processor.reset()
                        logging.info(
                            "Face capture result received state=%s face_id=%s; resuming normal dispatch evaluation",
                            face_capture_request.get("state"),
                            face_id,
                        )
            if not tracking_active and not card_capture_active and not face_capture_active:
                idle_frames += 1

            now = time.monotonic()
            if STATS_INTERVAL_SECONDS and now - stats_started_at >= STATS_INTERVAL_SECONDS:
                elapsed_s = now - stats_started_at
                if tracking_active or card_capture_active or face_capture_active:
                    measured_fps = processed_frames / elapsed_s
                    logging.info(
                        "Detection loop: dispatch=%.2f fps card=%.2f fps face=%.2f fps last_frame=%.1f ms capture=%.1f ms dispatch_process=%.1f ms card_process=%.1f ms face_process=%.1f ms locked_track_id=%s event=%s card_request_id=%s card_event=%s face_request_id=%s face_event=%s reacquire_active=%s reacquired=%s",
                        measured_fps,
                        card_capture_frames / elapsed_s,
                        face_capture_frames / elapsed_s,
                        (now - frame_started_at) * 1000,
                        capture_elapsed_s * 1000,
                        process_elapsed_s * 1000,
                        card_process_elapsed_s * 1000,
                        face_process_elapsed_s * 1000,
                        locked_track_id,
                        event,
                        card_capture_request.get("request_id"),
                        card_event,
                        face_capture_request.get("request_id"),
                        face_event,
                        reacquire_hold.active,
                        reacquire_hold.reacquired,
                    )
                else:
                    measured_fps = idle_frames / elapsed_s
                    logging.info(
                        "Detection loop inactive: %.2f discard fps, last_frame=%.1f ms, capture=%.1f ms",
                        measured_fps,
                        (now - frame_started_at) * 1000,
                        capture_elapsed_s * 1000,
                    )
                stats_started_at = now
                processed_frames = 0
                card_capture_frames = 0
                face_capture_frames = 0
                idle_frames = 0

            if card_capture_active:
                active_frame_interval = card_frame_interval
            elif face_capture_active:
                active_frame_interval = card_frame_interval
            elif tracking_active:
                active_frame_interval = frame_interval
            else:
                active_frame_interval = idle_frame_interval
            if active_frame_interval:
                next_frame_at += active_frame_interval
                sleep_for = next_frame_at - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_frame_at = time.monotonic()
    finally:
        cap.release()

if __name__ == "__main__":
    main()
