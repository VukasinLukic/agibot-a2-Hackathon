import cv2
import logging
import os
import time
import torch

import requests

from config import (
    MODEL_NAME,
    PERSON_CLASS_ID,
    CONF_THRESHOLD,
    CAMERA_ID,
    CLOSE_HEIGHT_RATIO,
    FACE_CONF_THRESHOLD,
    FACE_CROP_HEIGHT_RATIO,
    FACE_MIN_HEIGHT,
    FACE_MIN_SIZE,
    FACE_MIN_WIDTH,
    MAX_LOST_FRAMES,
    LOCK_STABILITY_FRAMES,
    LOCK_STABILITY_TOLERANCE,
    DEBUG_OVERLAY,
    BYPASS_FACE_CONFIRMATION,
    BYPASS_LOCKING,
)

from detector import PersonDetector
from face_detector import FaceDetector
from person_manager import (
    add_person_geometry,
    mark_close_persons,
    select_closest_person,
)
from lock_manager import LockManager
from utils import draw_debug_overlay, draw_person, draw_status

PERSON_DETECTED_URL = "http://localhost:8080/api/vision/person_detected"
PERSON_LEFT_URL = "http://localhost:8080/api/vision/person_left"

class FramePublisher:
    def __init__(self):
        self.log = logging.getLogger(__name__)
        self.url = os.getenv("GPU_DETECTOR_FRAME_URL", "http://127.0.0.1:8765/frame")
        self.timeout_seconds = float(os.getenv("GPU_DETECTOR_FRAME_TIMEOUT", "0.5"))
        self.jpeg_quality = int(os.getenv("GPU_DETECTOR_FRAME_QUALITY", "70"))

    def publish(self, frame):
        try:
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if not ok:
                return
            requests.post(
                self.url,
                files={"file": ("frame.jpg", buf.tobytes(), "image/jpeg")},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            self.log.warning("Failed to publish frame: %s", exc)

class LockStatePublisher:
    # post to gpu_detector_server only when lock state changes
    def __init__(self):
        self.log = logging.getLogger(__name__)
        self.url = os.getenv("GPU_DETECTOR_LOCK_STATE_URL", "http://127.0.0.1:8765/lock-state")
        self.timeout_seconds = float(os.getenv("GPU_DETECTOR_LOCK_STATE_TIMEOUT", "1"))
        self.last_locked_track_id = None

    def publish_if_changed(self, locked_track_id, state):
        if locked_track_id == self.last_locked_track_id:
            return None

        previous_track_id = self.last_locked_track_id
        self.last_locked_track_id = locked_track_id

        if locked_track_id is None:
            event = "unlocked"
        elif previous_track_id is None:
            event = "locked"
        else:
            event = "lock_changed"

        payload = {
            "event": event,
            "locked": locked_track_id is not None,
            "locked_track_id": locked_track_id,
            "previous_locked_track_id": previous_track_id,
            "timestamp": time.time(),
            "raw_candidate_id": state["raw_candidate_id"],
            "confirmed_candidate_id": state["confirmed_candidate_id"],
            "persons": state["persons"],
        }

        try:
            response = requests.post(self.url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.log.warning("Failed to publish lock state to %s: %s", self.url, exc)

        return event

class PersonLockProcessor:
    def __init__(self):
        self.log = logging.getLogger(__name__)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.lock_state_publisher = LockStatePublisher()

        self.detector = PersonDetector(
            model_name=MODEL_NAME,
            person_class_id=PERSON_CLASS_ID,
            conf_threshold=CONF_THRESHOLD,
            device=self.device,
        )
        self.face_detector = FaceDetector(
            device=self.device,
            min_face_size=FACE_MIN_SIZE,
            min_face_width=FACE_MIN_WIDTH,
            min_face_height=FACE_MIN_HEIGHT,
            min_face_confidence=FACE_CONF_THRESHOLD,
            crop_height_ratio=FACE_CROP_HEIGHT_RATIO,
        )

        self.lock_manager = LockManager(
            max_lost_frames=MAX_LOST_FRAMES,
            stability_frames=LOCK_STABILITY_FRAMES,
            stability_tolerance=LOCK_STABILITY_TOLERANCE,
        )
        self.frame_publisher = FramePublisher()

    def process_frame_with_state(self, frame):
        # YOLO detects and tracks people, it returns a list of dicts with track_id, confidence and bbox
        persons = self.detector.detect_and_track(frame)

        #  add information used for proximity estimation and locking, returns bbox, center coordinates, height ratio and area ratio
        persons = [
            add_person_geometry(person, frame.shape)
            for person in persons
        ]

        #  Mark which YOLO persons are close enough to consider for locking
        persons = mark_close_persons(
            persons,
            close_height_ratio=CLOSE_HEIGHT_RATIO,
        )

        # pick the closest close person. No face confirmation has happened yet
        candidate = select_closest_person(persons)
        raw_candidate = candidate
        if candidate is not None:
            cx1, cy1, cx2, cy2 = candidate["bbox"]
            self.log.info(f"[person] track={candidate['track_id']} w={cx2-cx1} h={cy2-cy1} height_ratio={candidate['height_ratio']:.3f} (min={CLOSE_HEIGHT_RATIO}) | stability={self.lock_manager.candidate_stable_count}/{LOCK_STABILITY_FRAMES}")

        # confirm the candidate with MTCNN. If no face is found, do not create a new lock
        if candidate is not None:
            if BYPASS_FACE_CONFIRMATION:
                candidate["face_confirmed"] = True
            else:
                candidate["face_confirmed"] = self.face_detector.confirm_person_face(
                    frame,
                    candidate,
                )
            if not candidate["face_confirmed"]:
                candidate = None

        # LockManager owns the final track lock. Existing locks continue by YOLO track ID.
        if BYPASS_LOCKING:
            locked_track_id = None
        else:
            locked_track_id = self.lock_manager.update(
                persons,
                candidate_person=candidate,
            )

        for person in persons:
            locked = self.lock_manager.is_locked(person)
            draw_person(frame, person, locked=locked)

        draw_status(frame, locked_track_id)

        if DEBUG_OVERLAY:
            close_count = sum(1 for person in persons if person["is_close"])
            raw_candidate_id = None if raw_candidate is None else raw_candidate["track_id"]
            confirmed_candidate_id = None if candidate is None else candidate["track_id"]
            draw_debug_overlay(
                frame,
                [
                    f"device: {self.device}",
                    f"persons: {len(persons)}  close: {close_count}",
                    f"raw candidate: {raw_candidate_id}",
                    f"face-confirmed candidate: {confirmed_candidate_id}",
                    f"stability: {self.lock_manager.candidate_stable_count}/{LOCK_STABILITY_FRAMES}",
                    f"face gate bypass: {BYPASS_FACE_CONFIRMATION}",
                    f"locking bypass: {BYPASS_LOCKING}",
                ],
            )

        state = {
            "locked_track_id": locked_track_id,
            "persons": persons,
            "raw_candidate_id": None if raw_candidate is None else raw_candidate["track_id"],
            "confirmed_candidate_id": None if candidate is None else candidate["track_id"],
        }
        state["lock_event"] = self.lock_state_publisher.publish_if_changed(
            locked_track_id,
            state,
        )

        self.frame_publisher.publish(frame)
        return frame, state


def main():
    logging.basicConfig(
        filename="detection.log",
        filemode="w",
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    processor = PersonLockProcessor()
    cap = cv2.VideoCapture(CAMERA_ID)

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Could not read webcam frame")
            break

        frame, _state = processor.process_frame_with_state(frame)
        # print out device 
        print(f"Device: {processor.device}")
        print(f"Locked track ID: {_state['locked_track_id']} | Lock event: {_state['lock_event']} | Raw candidate: {_state['raw_candidate_id']} | Confirmed candidate: {_state['confirmed_candidate_id']}")
        # cv2.imshow("Person lock system", frame)

        # if cv2.waitKey(1) & 0xFF == ord("q"):
        #     break

    cap.release()
    # cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
