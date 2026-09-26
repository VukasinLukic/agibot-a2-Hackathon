import base64
import time

import cv2
import numpy as np

from config import (
    FACE_MIN_HEIGHT,
    FACE_MIN_WIDTH,
    VISION_FACE_CAPTURE_MIN_BLUR,
    VISION_FACE_CAPTURE_STABILITY_FRAMES,
)

STABILITY_CENTER_TOLERANCE_RATIO = 0.08
JPEG_QUALITY = 85


def _face_quality(frame, bbox):
    x1, y1, x2, y2 = bbox
    crop = frame[max(0, y1):y2, max(0, x1):x2]
    if crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    width = x2 - x1
    height = y2 - y1
    size_ok = width >= FACE_MIN_WIDTH and height >= FACE_MIN_HEIGHT
    blur_ok = blur >= VISION_FACE_CAPTURE_MIN_BLUR
    return {"blur": blur, "width": width, "height": height, "accepted": size_ok and blur_ok}


class FaceCaptureProcessor:
    #Request-gated dual-distance face capture for enrollment (mirrors CardCaptureProcessor).

    def __init__(self, face_detector, face_embedder):
        self.face_detector = face_detector
        self.face_embedder = face_embedder
        self.request_id = None
        self.distance = None
        self.stable_count = 0
        self.last_center = None
        self.last_rejection_reason = None

    def reset(self, request_id=None, distance=None):
        self.request_id = request_id
        self.distance = distance
        self.stable_count = 0
        self.last_center = None
        self.last_rejection_reason = None

    def process_frame(self, frame, request_state):
        request_id = request_state.get("request_id")
        distance = request_state.get("distance")
        if not request_id:
            self.reset()
            return None

        if request_id != self.request_id or distance != self.distance:
            self.reset(request_id, distance)

        face = self._best_face(frame)
        if face is None:
            self.stable_count = 0
            self.last_center = None
            return None

        quality = _face_quality(frame, face["bbox"])
        if quality is None or not quality["accepted"]:
            self.stable_count = 0
            self.last_center = None
            if quality is None:
                self.last_rejection_reason = "empty_crop"
            else:
                problems = []
                if quality["width"] < FACE_MIN_WIDTH or quality["height"] < FACE_MIN_HEIGHT:
                    problems.append(
                        f"too_small({quality['width']}x{quality['height']}"
                        f"<{FACE_MIN_WIDTH}x{FACE_MIN_HEIGHT})"
                    )
                if quality["blur"] < VISION_FACE_CAPTURE_MIN_BLUR:
                    problems.append(f"too_blurry(blur={quality['blur']:.0f}<{VISION_FACE_CAPTURE_MIN_BLUR:.0f})")
                self.last_rejection_reason = "low_quality:" + ",".join(problems)
            return None

        center = face["center"]
        if not self._is_stable(center, frame.shape):
            self.stable_count = 1
        else:
            self.stable_count += 1
        self.last_center = center

        if self.stable_count < VISION_FACE_CAPTURE_STABILITY_FRAMES:
            self.last_rejection_reason = f"holding_steady({self.stable_count}/{VISION_FACE_CAPTURE_STABILITY_FRAMES})"
            return None

        self.last_rejection_reason = None
        return self._build_result(request_id, distance, frame, face, quality)

    def _best_face(self, frame):
        frame_h, frame_w = frame.shape[:2]
        faces = self.face_detector.detect_faces_full_height(frame, (0, 0, frame_w, frame_h))
        if not faces:
            self.last_rejection_reason = "no_face"
            return None

        if len(faces) != 1:
            self.last_rejection_reason = "multiple_faces"
            return None

        best = faces[0]
        self.last_rejection_reason = None
        x1, y1, x2, y2 = best["bbox"]
        return {
            "bbox": best["bbox"],
            "confidence": best["confidence"],
            "center": ((x1 + x2) / 2.0, (y1 + y2) / 2.0),
        }

    def _is_stable(self, center, frame_shape):
        if self.last_center is None:
            return False
        frame_h, frame_w = frame_shape[:2]
        return (
            abs(center[0] - self.last_center[0]) <= STABILITY_CENTER_TOLERANCE_RATIO * frame_w
            and abs(center[1] - self.last_center[1]) <= STABILITY_CENTER_TOLERANCE_RATIO * frame_h
        )

    def _build_result(self, request_id, distance, frame, face, quality):
        embedding = self.face_embedder.embed(frame, face["bbox"])
        if embedding is None:
            return None

        x1, y1, x2, y2 = face["bbox"]
        crop = frame[max(0, y1):y2, max(0, x1):x2]
        ok, encoded = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])

        return {
            "request_id": request_id,
            "status": "captured",
            "timestamp": time.time(),
            "metadata": {
                "distance": distance,
                "embedding": embedding.astype(np.float32).tolist(),
                "face_jpeg_base64": base64.b64encode(encoded.tobytes()).decode("ascii") if ok else None,
                "quality": {"blur": round(quality["blur"], 2), "confidence": round(face["confidence"], 3)},
            },
        }
