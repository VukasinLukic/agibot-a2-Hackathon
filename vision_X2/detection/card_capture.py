import base64
import os
import time

import cv2
import numpy as np


def _env_float(name, default):
    value = os.getenv(name)
    return default if value in (None, "") else float(value)


def _env_int(name, default):
    value = os.getenv(name)
    return default if value in (None, "") else int(value)


TARGET_ASPECT_RATIO = _env_float("VISION_CARD_CAPTURE_TARGET_ASPECT_RATIO", 1.586)
MIN_ASPECT_RATIO = _env_float("VISION_CARD_CAPTURE_MIN_ASPECT_RATIO", 1.35)
MAX_ASPECT_RATIO = _env_float("VISION_CARD_CAPTURE_MAX_ASPECT_RATIO", 1.85)
MIN_AREA_RATIO = _env_float("VISION_CARD_CAPTURE_MIN_AREA_RATIO", 0.08)
MIN_BLUR = _env_float("VISION_CARD_CAPTURE_MIN_BLUR", 80.0)
MIN_BRIGHTNESS = _env_float("VISION_CARD_CAPTURE_MIN_BRIGHTNESS", 45.0)
MAX_BRIGHTNESS = _env_float("VISION_CARD_CAPTURE_MAX_BRIGHTNESS", 220.0)
MAX_GLARE_RATIO = _env_float("VISION_CARD_CAPTURE_MAX_GLARE_RATIO", 0.18)
STABILITY_FRAMES = _env_int("VISION_CARD_CAPTURE_STABILITY_FRAMES", 3)
STABILITY_CENTER_TOLERANCE = _env_float("VISION_CARD_CAPTURE_STABILITY_CENTER_TOLERANCE", 0.08)
STABILITY_AREA_TOLERANCE = _env_float("VISION_CARD_CAPTURE_STABILITY_AREA_TOLERANCE", 0.20)
JPEG_QUALITY = _env_int("VISION_CARD_CAPTURE_JPEG_QUALITY", 90)
OUTPUT_WIDTH = _env_int("VISION_CARD_CAPTURE_OUTPUT_WIDTH", 856)
OUTPUT_HEIGHT = _env_int("VISION_CARD_CAPTURE_OUTPUT_HEIGHT", 540)
GUIDE_WIDTH_RATIO = _env_float("VISION_CARD_CAPTURE_GUIDE_WIDTH_RATIO", 0.72)
GUIDE_HEIGHT_RATIO = _env_float("VISION_CARD_CAPTURE_GUIDE_HEIGHT_RATIO", 0.78)
PORTRAIT_MIN_FACE_AREA_RATIO = _env_float("VISION_CARD_CAPTURE_PORTRAIT_MIN_FACE_AREA_RATIO", 0.003)
PORTRAIT_MAX_FACE_AREA_RATIO = _env_float("VISION_CARD_CAPTURE_PORTRAIT_MAX_FACE_AREA_RATIO", 0.08)
PORTRAIT_MIN_ASPECT_RATIO = _env_float("VISION_CARD_CAPTURE_PORTRAIT_MIN_ASPECT_RATIO", 1.15)
PORTRAIT_MAX_ASPECT_RATIO = _env_float("VISION_CARD_CAPTURE_PORTRAIT_MAX_ASPECT_RATIO", 2.40)
PORTRAIT_MIN_AREA_RATIO = _env_float("VISION_CARD_CAPTURE_PORTRAIT_MIN_AREA_RATIO", 0.08)
PORTRAIT_MIN_FRAME_AREA_RATIO = _env_float("VISION_CARD_CAPTURE_PORTRAIT_MIN_FRAME_AREA_RATIO", 0.12)
PORTRAIT_MIN_LINE_COVERAGE = _env_float("VISION_CARD_CAPTURE_PORTRAIT_MIN_LINE_COVERAGE", 0.25)
PORTRAIT_MIN_LINE_LENGTH_RATIO = _env_float("VISION_CARD_CAPTURE_PORTRAIT_MIN_LINE_LENGTH_RATIO", 0.14)
PORTRAIT_MAX_LINE_ANGLE_DEG = _env_float("VISION_CARD_CAPTURE_PORTRAIT_MAX_LINE_ANGLE_DEG", 15.0)

_FACE_CASCADE = None


class CardCaptureProcessor:
    """OpenCV portrait-anchor ID/card capture processor."""

    def __init__(self):
        self.request_id = None
        self.started_at = None
        self.frame_count = 0
        self.stable_count = 0
        self.last_center = None
        self.last_area_ratio = None

    def reset(self, request_id=None):
        self.request_id = request_id
        self.started_at = time.time() if request_id else None
        self.frame_count = 0
        self.stable_count = 0
        self.last_center = None
        self.last_area_ratio = None

    def process_frame(self, frame, request_state):
        request_id = request_state.get("request_id")
        if not request_id:
            self.reset()
            return None

        if request_id != self.request_id:
            self.reset(request_id)

        self.frame_count += 1
        candidate = self._detect_best_candidate(frame)
        if candidate is None:
            self.stable_count = 0
            self.last_center = None
            self.last_area_ratio = None
            return None

        if not self._is_stable(candidate):
            self.stable_count = 1
        else:
            self.stable_count += 1

        self.last_center = candidate["center"]
        self.last_area_ratio = candidate["quality"]["area_ratio"]
        candidate["quality"]["stable_frames"] = self.stable_count

        if self.stable_count < STABILITY_FRAMES:
            return None

        return self._build_result(request_id, request_state, candidate)

    def _detect_best_candidate(self, frame):
        if frame is None or not isinstance(frame, np.ndarray) or frame.ndim != 3:
            return None

        return self._detect_portrait_edge_candidate(frame)

    def _detect_portrait_edge_candidate(self, frame):
        inspected = self.inspect_portrait_edges(frame, max_candidates=8)
        for candidate in inspected.get("candidates", []):
            if not candidate.get("accepted"):
                continue

            points = candidate["points"].astype(np.float32)
            warped = _warp_card(frame, points)
            if warped is None:
                continue

            frame_area = float(frame.shape[0] * frame.shape[1]) or 1.0
            contour_area = float(cv2.contourArea(points))
            area_ratio = contour_area / frame_area
            if area_ratio < PORTRAIT_MIN_FRAME_AREA_RATIO:
                continue
            quality = _quality_metrics(
                frame_shape=frame.shape,
                points=points,
                contour_area=contour_area,
                area_ratio=area_ratio,
                aspect_ratio=candidate["aspect_ratio"],
                warped=warped,
            )
            quality.update(
                {
                    "line_coverage": candidate["line_coverage"],
                    "horizontal_coverage": candidate["horizontal_coverage"],
                    "vertical_coverage": candidate["vertical_coverage"],
                    "portrait_area_ratio": candidate["portrait_area_ratio"],
                    "portrait_face_count": inspected.get("face_count", 0),
                    "portrait_line_count": inspected.get("line_count", 0),
                    "portrait_score": candidate["score"],
                    "portrait_roi_area_ratio": candidate["area_ratio"],
                    "portrait_frame_area_ratio": area_ratio,
                    "portrait_min_frame_area_ratio": PORTRAIT_MIN_FRAME_AREA_RATIO,
                }
            )
            quality_failures = _quality_failures(quality)
            if quality_failures:
                continue

            return {
                "points": points,
                "center": tuple(np.mean(points, axis=0)),
                "frame_shape": frame.shape,
                "warped": warped,
                "quality": quality,
                "source": "portrait_edges",
            }
        return None

    def debug_frame(self, frame):
        """Return diagnostics for local tuning without changing capture behavior."""
        if frame is None or not isinstance(frame, np.ndarray) or frame.ndim != 3:
            return {
                "line_count": 0,
                "accepted": [],
                "rejected": [],
                "reason": "invalid_frame",
            }

        inspected = self.inspect_portrait_edges(frame)
        candidates = inspected.get("candidates", [])
        return {
            "mode": "portrait_edges",
            "line_count": inspected.get("line_count", 0),
            "accepted": [candidate for candidate in candidates if candidate.get("accepted")],
            "rejected": [candidate for candidate in candidates if not candidate.get("accepted")],
            "reason": inspected.get("reason"),
            "portrait": inspected.get("portrait"),
            "face_count": inspected.get("face_count", 0),
            "line_count": inspected.get("line_count", 0),
        }

    def inspect_portrait_edges(self, frame, max_candidates=8):
        """Return diagnostics for portrait-anchor plus line-edge card detection."""
        if frame is None or not isinstance(frame, np.ndarray) or frame.ndim != 3:
            return {
                "mode": "portrait_debug",
                "face_count": 0,
                "portrait": None,
                "line_count": 0,
                "candidates": [],
                "reason": "invalid_frame",
            }

        frame_height, frame_width = frame.shape[:2]
        roi_points = _guide_points(frame_width, frame_height)
        x1, y1, x2, y2 = _axis_aligned_bounds(roi_points, frame_width, frame_height)
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return {
                "mode": "portrait_debug",
                "face_count": 0,
                "portrait": None,
                "line_count": 0,
                "candidates": [],
                "reason": "empty_roi",
                "roi_points": roi_points,
                "roi_rect": (x1, y1, x2, y2),
            }

        offset = np.array([x1, y1], dtype=np.float32)
        faces = _inspect_portrait_faces(roi, offset)
        accepted_faces = [face for face in faces if face["accepted"]]
        portrait = max(
            accepted_faces,
            key=lambda face: (face["score"], face["area_ratio"]),
            default=None,
        )
        lines = _portrait_edge_lines(roi, offset)
        candidates = []
        if portrait is not None:
            candidates = _portrait_edge_candidates(roi.shape, portrait, lines, offset)
            candidates = candidates[:max_candidates]

        return {
            "mode": "portrait_debug",
            "face_count": len(faces),
            "faces": faces,
            "portrait": portrait,
            "line_count": len(lines),
            "lines": lines,
            "candidates": candidates,
            "roi": roi,
            "roi_points": roi_points,
            "roi_rect": (x1, y1, x2, y2),
            "reason": "ready" if portrait is not None else "no_portrait_anchor",
        }

    def _is_stable(self, candidate):
        if self.last_center is None or self.last_area_ratio is None:
            return False

        center = candidate["center"]
        area_ratio = candidate["quality"]["area_ratio"]
        frame_height, frame_width = candidate["frame_shape"][:2]
        center_is_stable = (
            abs(center[0] - self.last_center[0]) <= STABILITY_CENTER_TOLERANCE * max(float(frame_width), 1.0)
            and abs(center[1] - self.last_center[1]) <= STABILITY_CENTER_TOLERANCE * max(float(frame_height), 1.0)
        )

        area_base = max(abs(self.last_area_ratio), 0.0001)
        area_is_stable = abs(area_ratio - self.last_area_ratio) / area_base <= STABILITY_AREA_TOLERANCE
        return center_is_stable and area_is_stable

    def _build_result(self, request_id, request_state, candidate):
        crop = _normalize_output_crop(candidate["warped"])
        ok, encoded = cv2.imencode(
            ".jpg",
            crop,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(np.clip(JPEG_QUALITY, 1, 100))],
        )
        if not ok:
            return None

        now = time.time()
        quality = dict(candidate["quality"])
        quality["stable_frames"] = self.stable_count
        return {
            "request_id": request_id,
            "status": "captured",
            "timestamp": now,
            "metadata": {
                "processor": "portrait_edges",
                "image_mime_type": "image/jpeg",
                "image_jpeg_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                "crop_width": int(crop.shape[1]),
                "crop_height": int(crop.shape[0]),
                "request_id": request_id,
                "target_type": request_state.get("target_type"),
                "source": request_state.get("source"),
                "started_at": self.started_at,
                "completed_at": now,
                "frame_count": self.frame_count,
                "quality": _json_safe_quality(quality),
            },
        }


def _order_points(points):
    rect = np.zeros((4, 2), dtype=np.float32)
    point_sum = points.sum(axis=1)
    point_diff = np.diff(points, axis=1).reshape(4)
    rect[0] = points[np.argmin(point_sum)]
    rect[2] = points[np.argmax(point_sum)]
    rect[1] = points[np.argmin(point_diff)]
    rect[3] = points[np.argmax(point_diff)]
    return rect


def _guide_points(frame_width, frame_height):
    max_width = float(frame_width) * np.clip(GUIDE_WIDTH_RATIO, 0.1, 1.0)
    max_height = float(frame_height) * np.clip(GUIDE_HEIGHT_RATIO, 0.1, 1.0)
    width = min(max_width, max_height * TARGET_ASPECT_RATIO)
    height = width / TARGET_ASPECT_RATIO
    if height > max_height:
        height = max_height
        width = height * TARGET_ASPECT_RATIO

    center_x = float(frame_width) / 2.0
    center_y = float(frame_height) / 2.0
    x1 = center_x - width / 2.0
    y1 = center_y - height / 2.0
    x2 = center_x + width / 2.0
    y2 = center_y + height / 2.0
    return np.array(
        [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        dtype=np.float32,
    )


def _axis_aligned_bounds(points, frame_width, frame_height):
    x1 = int(max(0, np.floor(np.min(points[:, 0]))))
    y1 = int(max(0, np.floor(np.min(points[:, 1]))))
    x2 = int(min(frame_width, np.ceil(np.max(points[:, 0]))))
    y2 = int(min(frame_height, np.ceil(np.max(points[:, 1]))))
    return x1, y1, x2, y2


def _inspect_portrait_faces(roi, offset):
    roi_height, roi_width = roi.shape[:2]
    roi_area = float(roi_width * roi_height) or 1.0
    boxes = _detect_face_boxes(roi)
    faces = []
    for x, y, width, height in boxes:
        area_ratio = float(width * height) / roi_area
        center = np.array([x + width / 2.0, y + height / 2.0], dtype=np.float32)
        roi_center = np.array([roi_width / 2.0, roi_height / 2.0], dtype=np.float32)
        center_distance = float(np.linalg.norm(center - roi_center))
        max_center_distance = float(np.linalg.norm(roi_center)) or 1.0
        center_score = max(0.0, 1.0 - center_distance / max_center_distance)
        failures = []
        if area_ratio < PORTRAIT_MIN_FACE_AREA_RATIO:
            failures.append(f"face_area={area_ratio:.3f}<{PORTRAIT_MIN_FACE_AREA_RATIO:.3f}")
        if area_ratio > PORTRAIT_MAX_FACE_AREA_RATIO:
            failures.append(f"face_area={area_ratio:.3f}>{PORTRAIT_MAX_FACE_AREA_RATIO:.3f}")

        bbox = (int(x), int(y), int(width), int(height))
        frame_bbox = (
            int(x + offset[0]),
            int(y + offset[1]),
            int(width),
            int(height),
        )
        faces.append(
            {
                "accepted": not failures,
                "reason": "accepted" if not failures else ",".join(failures),
                "bbox": bbox,
                "frame_bbox": frame_bbox,
                "area_ratio": float(area_ratio),
                "center_score": float(center_score),
                "score": float(0.65 * center_score + 0.35 * min(area_ratio / 0.03, 1.0)),
            }
        )
    faces.sort(key=lambda face: (face["accepted"], face["score"]), reverse=True)
    return faces


def _detect_face_boxes(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    classifier = _face_cascade()
    if classifier is None:
        return []

    min_side = max(24, int(min(gray.shape[:2]) * 0.06))
    faces = classifier.detectMultiScale(
        gray,
        scaleFactor=1.08,
        minNeighbors=3,
        minSize=(min_side, min_side),
    )
    return [(int(x), int(y), int(width), int(height)) for x, y, width, height in faces]


def _portrait_edge_lines(roi, offset):
    roi_height, roi_width = roi.shape[:2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 45, 140)
    min_line_length = int(max(30.0, min(roi_width, roi_height) * PORTRAIT_MIN_LINE_LENGTH_RATIO))
    raw_lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=45,
        minLineLength=min_line_length,
        maxLineGap=12,
    )
    if raw_lines is None:
        return []

    lines = []
    max_angle = float(PORTRAIT_MAX_LINE_ANGLE_DEG)
    for raw_line in raw_lines[:, 0, :]:
        x1, y1, x2, y2 = [float(value) for value in raw_line]
        dx = x2 - x1
        dy = y2 - y1
        length = float(np.hypot(dx, dy))
        if length <= 0:
            continue
        angle = float(np.degrees(np.arctan2(dy, dx)))
        abs_angle = abs(angle)
        orientation = "other"
        if abs_angle <= max_angle or abs_angle >= 180.0 - max_angle:
            orientation = "horizontal"
        elif abs(abs_angle - 90.0) <= max_angle:
            orientation = "vertical"

        if orientation == "other":
            continue
        line = {
            "orientation": orientation,
            "local": (x1, y1, x2, y2),
            "frame": (x1 + float(offset[0]), y1 + float(offset[1]), x2 + float(offset[0]), y2 + float(offset[1])),
            "length": length,
            "angle": angle,
            "x": float((x1 + x2) / 2.0),
            "y": float((y1 + y2) / 2.0),
        }
        lines.append(line)

    lines.sort(key=lambda line: line["length"], reverse=True)
    return lines[:40]


def _portrait_edge_candidates(roi_shape, portrait, lines, offset):
    roi_height, roi_width = roi_shape[:2]
    portrait_x, portrait_y, portrait_width, portrait_height = portrait["bbox"]
    portrait_center_x = portrait_x + portrait_width / 2.0
    portrait_center_y = portrait_y + portrait_height / 2.0
    horizontal = [line for line in lines if line["orientation"] == "horizontal"]
    vertical = [line for line in lines if line["orientation"] == "vertical"]
    candidates = []

    for top in horizontal:
        if top["y"] >= portrait_center_y:
            continue
        for bottom in horizontal:
            if bottom["y"] <= portrait_center_y:
                continue
            candidate_height = bottom["y"] - top["y"]
            if candidate_height <= portrait_height:
                continue
            for left in vertical:
                if left["x"] >= portrait_center_x:
                    continue
                for right in vertical:
                    if right["x"] <= portrait_center_x:
                        continue
                    candidate_width = right["x"] - left["x"]
                    if candidate_width <= portrait_width:
                        continue
                    candidate = _score_portrait_edge_candidate(
                        roi_width,
                        roi_height,
                        portrait,
                        top,
                        bottom,
                        left,
                        right,
                        candidate_width,
                        candidate_height,
                        offset,
                    )
                    candidates.append(candidate)

    candidates.sort(key=lambda candidate: (candidate["accepted"], candidate["score"]), reverse=True)
    return candidates


def _score_portrait_edge_candidate(
    roi_width,
    roi_height,
    portrait,
    top,
    bottom,
    left,
    right,
    candidate_width,
    candidate_height,
    offset,
):
    x1 = float(left["x"])
    x2 = float(right["x"])
    y1 = float(top["y"])
    y2 = float(bottom["y"])
    area_ratio = float((candidate_width * candidate_height) / max(float(roi_width * roi_height), 1.0))
    aspect_ratio = float(candidate_width / max(candidate_height, 1.0))
    horizontal_coverage = min((top["length"] + bottom["length"]) / max(2.0 * candidate_width, 1.0), 1.0)
    vertical_coverage = min((left["length"] + right["length"]) / max(2.0 * candidate_height, 1.0), 1.0)
    line_coverage = min(horizontal_coverage, vertical_coverage)

    candidate_center = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0], dtype=np.float32)
    roi_center = np.array([roi_width / 2.0, roi_height / 2.0], dtype=np.float32)
    center_distance = float(np.linalg.norm(candidate_center - roi_center))
    max_center_distance = float(np.linalg.norm(roi_center)) or 1.0
    center_score = max(0.0, 1.0 - center_distance / max_center_distance)
    aspect_score = max(0.0, 1.0 - abs(aspect_ratio - TARGET_ASPECT_RATIO) / TARGET_ASPECT_RATIO)
    area_score = min(area_ratio / 0.35, 1.0)
    score = 0.30 * aspect_score + 0.25 * line_coverage + 0.25 * area_score + 0.20 * center_score

    failures = []
    if area_ratio < PORTRAIT_MIN_AREA_RATIO:
        failures.append(f"area={area_ratio:.3f}<{PORTRAIT_MIN_AREA_RATIO:.3f}")
    if aspect_ratio < PORTRAIT_MIN_ASPECT_RATIO or aspect_ratio > PORTRAIT_MAX_ASPECT_RATIO:
        failures.append(
            f"aspect={aspect_ratio:.2f}!={PORTRAIT_MIN_ASPECT_RATIO:.2f}-{PORTRAIT_MAX_ASPECT_RATIO:.2f}"
        )
    if line_coverage < PORTRAIT_MIN_LINE_COVERAGE:
        failures.append(f"line_coverage={line_coverage:.2f}<{PORTRAIT_MIN_LINE_COVERAGE:.2f}")

    points = np.array(
        [
            [x1 + offset[0], y1 + offset[1]],
            [x2 + offset[0], y1 + offset[1]],
            [x2 + offset[0], y2 + offset[1]],
            [x1 + offset[0], y2 + offset[1]],
        ],
        dtype=np.float32,
    )
    return {
        "accepted": not failures,
        "reason": "accepted" if not failures else ",".join(failures),
        "points": points,
        "score": float(score),
        "area_ratio": float(area_ratio),
        "aspect_ratio": float(aspect_ratio),
        "center_score": float(center_score),
        "line_coverage": float(line_coverage),
        "horizontal_coverage": float(horizontal_coverage),
        "vertical_coverage": float(vertical_coverage),
        "portrait_area_ratio": float(portrait["area_ratio"]),
        "portrait_bbox": portrait["frame_bbox"],
    }


def _warp_card(frame, points):
    top_width = np.linalg.norm(points[1] - points[0])
    bottom_width = np.linalg.norm(points[2] - points[3])
    left_height = np.linalg.norm(points[3] - points[0])
    right_height = np.linalg.norm(points[2] - points[1])
    width = int(max(top_width, bottom_width))
    height = int(max(left_height, right_height))
    if width <= 0 or height <= 0:
        return None

    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(points.astype(np.float32), destination)
    warped = cv2.warpPerspective(frame, transform, (width, height))
    return _rotate_landscape(warped)


def _rotate_landscape(image):
    height, width = image.shape[:2]
    if height > width:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    return image


def _normalize_output_crop(image):
    image = _rotate_landscape(image)
    return cv2.resize(image, (OUTPUT_WIDTH, OUTPUT_HEIGHT), interpolation=cv2.INTER_AREA)


def _quality_metrics(frame_shape, points, contour_area, area_ratio, aspect_ratio, warped):
    gray_crop = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray_crop, cv2.CV_64F).var())
    brightness = float(np.mean(gray_crop))
    glare_ratio = float(np.mean(gray_crop >= 245))

    frame_height, frame_width = frame_shape[:2]
    frame_center = np.array([frame_width / 2.0, frame_height / 2.0], dtype=np.float32)
    card_center = np.mean(points, axis=0)
    center_distance = float(np.linalg.norm(card_center - frame_center))
    max_center_distance = float(np.linalg.norm(frame_center)) or 1.0
    center_score = max(0.0, 1.0 - (center_distance / max_center_distance))

    rect = cv2.minAreaRect(points.astype(np.float32))
    rect_width, rect_height = rect[1]
    rect_area = max(float(rect_width * rect_height), 1.0)
    rectangularity = min(float(contour_area) / rect_area, 1.0)

    aspect_score = max(0.0, 1.0 - (abs(aspect_ratio - TARGET_ASPECT_RATIO) / TARGET_ASPECT_RATIO))
    area_score = min(area_ratio / 0.30, 1.0)
    blur_score = min(blur / (MIN_BLUR * 2.0), 1.0) if MIN_BLUR > 0 else 1.0
    brightness_score = 1.0 if MIN_BRIGHTNESS <= brightness <= MAX_BRIGHTNESS else 0.0
    glare_score = max(0.0, 1.0 - (glare_ratio / max(MAX_GLARE_RATIO, 0.001)))

    score = (
        0.25 * area_score
        + 0.20 * aspect_score
        + 0.20 * rectangularity
        + 0.15 * center_score
        + 0.10 * blur_score
        + 0.05 * brightness_score
        + 0.05 * glare_score
    )

    return {
        "score": score,
        "blur": blur,
        "brightness": brightness,
        "glare_ratio": glare_ratio,
        "area_ratio": float(area_ratio),
        "aspect_ratio": float(aspect_ratio),
        "rectangularity": float(rectangularity),
        "center_score": float(center_score),
        "stable_frames": 0,
    }


def _face_cascade():
    global _FACE_CASCADE
    if _FACE_CASCADE is not None:
        return _FACE_CASCADE if not _FACE_CASCADE.empty() else None

    cv2_data = getattr(cv2, "data", None)
    haarcascades = getattr(cv2_data, "haarcascades", "")
    if not haarcascades:
        return None

    cascade_path = os.path.join(
        haarcascades,
        "haarcascade_frontalface_default.xml",
    )
    classifier = cv2.CascadeClassifier(cascade_path)
    _FACE_CASCADE = classifier
    return classifier if not classifier.empty() else None


def _quality_failures(quality):
    failures = []
    if quality["area_ratio"] < MIN_AREA_RATIO:
        failures.append(f"area={quality['area_ratio']:.3f}<{MIN_AREA_RATIO:.3f}")
    if quality["aspect_ratio"] < MIN_ASPECT_RATIO or quality["aspect_ratio"] > MAX_ASPECT_RATIO:
        failures.append(f"aspect={quality['aspect_ratio']:.2f}!={MIN_ASPECT_RATIO:.2f}-{MAX_ASPECT_RATIO:.2f}")
    if quality["blur"] < MIN_BLUR:
        failures.append(f"blur={quality['blur']:.0f}<{MIN_BLUR:.0f}")
    if quality["brightness"] < MIN_BRIGHTNESS:
        failures.append(f"dark={quality['brightness']:.0f}<{MIN_BRIGHTNESS:.0f}")
    if quality["brightness"] > MAX_BRIGHTNESS:
        failures.append(f"bright={quality['brightness']:.0f}>{MAX_BRIGHTNESS:.0f}")
    if quality["glare_ratio"] > MAX_GLARE_RATIO:
        failures.append(f"glare={quality['glare_ratio']:.2f}>{MAX_GLARE_RATIO:.2f}")
    return failures


def _json_safe_quality(quality):
    result = {}
    for key, value in quality.items():
        if isinstance(value, (bool, np.bool_)):
            result[key] = bool(value)
        elif isinstance(value, (np.floating, float)):
            result[key] = round(float(value), 4)
        elif isinstance(value, (np.integer, int)):
            result[key] = int(value)
        else:
            result[key] = value
    return result
