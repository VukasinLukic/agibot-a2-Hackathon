"""Ball track. OpenCV color and motion, with BlurBall when a checkpoint is set.

HSV bounds, diameter and the missing-frame limit come from ``VisionConfig``.
A short gap is ``predicted``. A longer gap is ``missing`` and drops the track.
Neither kind is a bounce or a point. The learned locator, when it answers,
is the blur center from BlurBall. It never writes the score.

With ``ballnet_path`` the frame goes through ``pipeline.BallNetPipeline``
(candidates -> BallNet -> MHT) instead, and HSV/BlurBall are not called.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from table_tennis.vision.calibration import TableCalibration
from table_tennis.vision.config import Roi, VisionConfig
from table_tennis.vision.frame import Frame
from table_tennis.vision.image import BgrImage

if TYPE_CHECKING:
    from table_tennis.vision.pipeline import BallNetPipeline, PatchScorer

MOTION_DELTA = 30
_MEASUREMENT_VAR = 16.0
_VELOCITY_VAR = 40.0

_OBSERVED = (40, 220, 40)
_PREDICTED = (40, 220, 220)


@dataclass(frozen=True, slots=True)
class TrackSample:
    """Same fields as the shared VisionObservation, plus an explicit bounce flag."""

    frame_seq: int
    capture_monotonic_ns: int
    detected: bool
    x_px: float | None
    y_px: float | None
    observation_kind: str
    confidence: float
    calibration_id: str | None
    proves_bounce: bool = False

    def as_observation(self) -> dict[str, object]:
        if self.observation_kind not in {"observed", "predicted", "missing"}:
            raise ValueError("observation_kind must be observed, predicted, or missing")
        if self.proves_bounce:
            raise ValueError("a track sample cannot prove a bounce")
        if self.observation_kind == "missing" and (self.x_px is not None or self.y_px is not None):
            raise ValueError("missing observation cannot carry coordinates")
        if self.observation_kind != "missing" and (self.x_px is None or self.y_px is None):
            raise ValueError("observed/predicted observation needs x_px and y_px")
        payload = {
            "frame_seq": self.frame_seq,
            "capture_monotonic_ns": self.capture_monotonic_ns,
            "detected": self.detected,
            "x_px": self.x_px,
            "y_px": self.y_px,
            "observation_kind": self.observation_kind,
            "confidence": self.confidence,
            "calibration_id": self.calibration_id,
        }
        from table_tennis.contracts import VisionObservation

        return VisionObservation.model_validate(payload).model_dump()


@dataclass(frozen=True, slots=True)
class _Blob:
    x: float
    y: float
    diameter: int
    axis_x: float = 0.0
    axis_y: float = 0.0
    streak: float = 0.0
    confidence: float = 0.8


class BallTracker:
    def __init__(
        self,
        config: VisionConfig,
        calibration: TableCalibration | None = None,
        model: object | None = None,
        ballnet: PatchScorer | None = None,
    ) -> None:
        self._roi = _search_roi(config.roi, calibration)
        self._calibration_id = calibration.calibration_id if calibration is not None and calibration.ready else None
        self.table_limited = self._calibration_id is not None and self._roi.width < 10**8
        self._missing_limit = config.missing_frames
        self._learned = _optional_pipeline(config, self._roi, ballnet)
        if self._learned is not None and model is not None:
            raise ValueError("BallNet and BlurBall are exclusive")
        self._model: object | None = None
        if self._learned is None:
            self._model = model if model is not None else _optional_model(config.model_path)
        if self._learned is None and self._model is None and (
            not config.ball.configured or config.ball.hsv_lower is None or config.ball.hsv_upper is None
        ):
            raise ValueError("ball color is not configured")
        self._color_on = config.ball.hsv_lower is not None and config.ball.hsv_upper is not None
        self._lower = config.ball.hsv_lower or (0, 0, 0)
        self._upper = config.ball.hsv_upper or (0, 0, 0)
        self._min_diameter = config.min_diameter_px
        self._max_diameter = config.max_diameter_px
        self._older: bytes | None = None
        self._previous: bytes | None = None
        self._last_ns: int | None = None
        self._misses = 0
        self._state: list[float] | None = None
        self._cov: list[list[float]] | None = None

    @property
    def pipeline(self) -> BallNetPipeline | None:
        """The BallNet path (per-stage ``last_ms``), or None on the HSV/BlurBall path."""
        return self._learned

    def update(self, frame: Frame) -> TrackSample:
        if self._learned is not None:
            return _checked(self._from_learned(frame))
        pixels = _bgr_bytes(frame)
        chosen = _from_model(self._model, pixels, frame.width, frame.height, self._min_diameter)
        if chosen is None and self._color_on:
            blobs = _blobs(
                pixels,
                frame.width,
                frame.height,
                self._previous,
                self._older,
                self._lower,
                self._upper,
                self._roi,
                self._min_diameter,
                self._max_diameter,
            )
            chosen = _choose(
                blobs, self._state, self._cov, self._last_ns, frame.capture_monotonic_ns, self._max_diameter
            )
        self._older = self._previous
        self._previous = pixels
        if chosen is not None:
            self._misses = 0
            sample = self._observed(frame, chosen)
            self._last_ns = frame.capture_monotonic_ns
            return _checked(sample)
        if self._state is None:
            return _checked(self._missing(frame))
        dt = _dt(self._last_ns, frame.capture_monotonic_ns)
        self._last_ns = frame.capture_monotonic_ns
        self._predict(dt)
        self._misses += 1
        if self._misses >= self._missing_limit:
            self._state = None
            self._cov = None
            self._misses = 0
            return _checked(self._missing(frame))
        assert self._state is not None
        return _checked(self._predicted(frame, self._state[0], self._state[1]))

    def _from_learned(self, frame: Frame) -> TrackSample:
        assert self._learned is not None
        hit = self._learned.step(frame)
        if hit is None:
            return self._missing(frame)
        if hit.kind == "observed":
            return TrackSample(
                frame_seq=frame.frame_seq,
                capture_monotonic_ns=frame.capture_monotonic_ns,
                detected=True,
                x_px=hit.x,
                y_px=hit.y,
                observation_kind="observed",
                confidence=min(max(hit.prob, 0.0), 1.0),
                calibration_id=self._calibration_id,
            )
        return self._predicted(frame, hit.x, hit.y)

    def _observed(self, frame: Frame, blob: _Blob) -> TrackSample:
        if self._state is None or self._cov is None:
            self._state = [blob.x, blob.y, 0.0, 0.0]
            self._cov = _initial_cov()
        else:
            self._predict(_dt(self._last_ns, frame.capture_monotonic_ns))
            _correct(self._state, self._cov, blob.x, blob.y)
        _follow_streak(self._state, blob, _dt(self._last_ns, frame.capture_monotonic_ns))
        return TrackSample(
            frame_seq=frame.frame_seq,
            capture_monotonic_ns=frame.capture_monotonic_ns,
            detected=True,
            x_px=blob.x,
            y_px=blob.y,
            observation_kind="observed",
            confidence=blob.confidence,
            calibration_id=self._calibration_id,
        )

    def _predicted(self, frame: Frame, x: float, y: float) -> TrackSample:
        return TrackSample(
            frame_seq=frame.frame_seq,
            capture_monotonic_ns=frame.capture_monotonic_ns,
            detected=False,
            x_px=x,
            y_px=y,
            observation_kind="predicted",
            confidence=0.3,
            calibration_id=self._calibration_id,
        )

    def _missing(self, frame: Frame) -> TrackSample:
        return TrackSample(
            frame_seq=frame.frame_seq,
            capture_monotonic_ns=frame.capture_monotonic_ns,
            detected=False,
            x_px=None,
            y_px=None,
            observation_kind="missing",
            confidence=0.0,
            calibration_id=self._calibration_id,
        )

    def _predict(self, dt: float) -> None:
        assert self._state is not None and self._cov is not None
        x, y, vx, vy = self._state
        self._state = [x + vx * dt, y + vy * dt, vx, vy]
        self._cov = _predict_cov(self._cov, dt)


def write_track_csv(path: Path | str, samples: Sequence[TrackSample]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "frame_seq",
                "capture_monotonic_ns",
                "observation_kind",
                "detected",
                "x_px",
                "y_px",
                "confidence",
                "calibration_id",
                "proves_bounce",
            ]
        )
        for sample in samples:
            row = sample.as_observation()
            writer.writerow(
                [
                    row["frame_seq"],
                    row["capture_monotonic_ns"],
                    row["observation_kind"],
                    row["detected"],
                    "" if row["x_px"] is None else row["x_px"],
                    "" if row["y_px"] is None else row["y_px"],
                    row["confidence"],
                    row["calibration_id"] or "",
                    sample.proves_bounce,
                ]
            )


def mark_track(image: BgrImage, sample: TrackSample) -> BgrImage:
    """Copy the frame and ring an observed or predicted sample. Missing draws nothing."""
    marked = image.copy()
    if sample.x_px is None or sample.y_px is None or sample.observation_kind == "missing":
        return marked
    color = _OBSERVED if sample.observation_kind == "observed" else _PREDICTED
    _ring(marked, int(round(sample.x_px)), int(round(sample.y_px)), 4, color)
    return marked


def _optional_model(model_path: str | None) -> object | None:
    if not model_path:
        return None
    from table_tennis.vision.blurball import BlurBallDetector

    return BlurBallDetector(model_path)


def _optional_pipeline(config: VisionConfig, roi: Roi, net: PatchScorer | None) -> BallNetPipeline | None:
    """BallNet path when weights are configured or a scorer is passed in. Imports numpy/OpenCV lazily."""
    if net is None and not config.ballnet_path:
        return None
    from table_tennis.vision.mht import TrackerParams
    from table_tennis.vision.pipeline import BallNetPipeline

    if net is None:
        from table_tennis.vision.ballnet import load_ballnet

        net = load_ballnet(config.ballnet_path)
    params = TrackerParams()
    params = replace(params, coast=min(params.coast, config.missing_frames - 1))
    return BallNetPipeline(
        net,
        work_width=config.work_width_px,
        compensate=config.compensate_motion,
        roi=None if roi.width >= 10**8 else roi,
        tracker_params=params,
    )


def _from_model(
    model: object | None, pixels: bytes, width: int, height: int, min_diameter: int
) -> _Blob | None:
    locate = getattr(model, "locate", None)
    if not callable(locate):
        return None
    found = locate(pixels, width, height)
    if not isinstance(found, tuple) or len(found) < 3:
        return None
    x, y, score = float(found[0]), float(found[1]), float(found[2])
    if score < 0.7:
        return None
    return _Blob(x=x, y=y, diameter=min_diameter, confidence=min(score, 1.0))


def _follow_streak(state: list[float] | None, blob: _Blob, dt: float) -> None:
    """A blur streak is a line. Its middle is the position; its axis sets speed."""
    if state is None or blob.streak <= 0.0 or dt <= 0.0:
        return
    along = state[2] * blob.axis_x + state[3] * blob.axis_y
    if abs(along) < 1.0:
        return
    sign = 1.0 if along >= 0.0 else -1.0
    speed = blob.streak / dt
    state[2] = sign * blob.axis_x * speed
    state[3] = sign * blob.axis_y * speed


def _choose(
    blobs: list[_Blob],
    state: list[float] | None,
    cov: list[list[float]] | None,
    last_ns: int | None,
    now_ns: int,
    max_diameter: int,
) -> _Blob | None:
    if not blobs:
        return None
    if state is None or cov is None or last_ns is None:
        return blobs[0] if len(blobs) == 1 else None
    dt = _dt(last_ns, now_ns)
    pred_x = state[0] + state[2] * dt
    pred_y = state[1] + state[3] * dt
    gate = float(max(max_diameter * 8, 64))
    best: _Blob | None = None
    best_dist = gate * gate
    for blob in blobs:
        dist = (blob.x - pred_x) ** 2 + (blob.y - pred_y) ** 2
        if dist <= best_dist:
            best = blob
            best_dist = dist
    return best


def _blobs(
    pixels: bytes,
    width: int,
    height: int,
    previous: bytes | None,
    older: bytes | None,
    lower: tuple[int, int, int],
    upper: tuple[int, int, int],
    roi: Roi,
    min_diameter: int,
    max_diameter: int,
) -> list[_Blob]:
    if previous is None:
        return []
    import cv2
    import numpy as np

    current = np.frombuffer(pixels, dtype=np.uint8).reshape((height, width, 3))
    prior = np.frombuffer(previous, dtype=np.uint8).reshape((height, width, 3))
    hsv = cv2.cvtColor(current, cv2.COLOR_BGR2HSV)
    color = _color_mask(cv2, np, hsv, lower, upper)
    motion = _motion_mask(cv2, np, current, prior, older, width, height)
    mask = cv2.bitwise_and(color, motion)
    x0 = min(width, max(0, roi.x))
    y0 = min(height, max(0, roi.y))
    x1 = min(width, max(0, roi.x + roi.width))
    y1 = min(height, max(0, roi.y + roi.height))
    if x1 <= x0 or y1 <= y0:
        return []
    clipped = np.zeros_like(mask)
    clipped[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
    color_clipped = np.zeros_like(color)
    color_clipped[y0:y1, x0:x1] = color[y0:y1, x0:x1]
    _color_count, color_labels, color_stats, _color_centroids = cv2.connectedComponentsWithStats(color_clipped)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(clipped)
    found: list[_Blob] = []
    for index in range(1, count):
        _left, _top, box_w, box_h, area = (int(stats[index, i]) for i in range(5))
        if box_w < 1 or box_h < 1 or area < 4:
            continue
        short = min(box_w, box_h)
        long = max(box_w, box_h)
        if short / long < 0.2 or area / (box_w * box_h) < 0.25:
            continue
        if not min_diameter <= short <= max_diameter:
            continue
        cx, cy = float(centroids[index, 0]), float(centroids[index, 1])
        color_label = int(color_labels[int(round(cy)), int(round(cx))])
        if color_label > 0:
            color_short = min(int(color_stats[color_label, 2]), int(color_stats[color_label, 3]))
            if color_short > max_diameter:
                continue
        axis_x, axis_y, streak = (0.0, 0.0, 0.0)
        if long >= short * 1.8:
            axis_x, axis_y, streak = _streak_axis(np, labels, index, float(long))
        found.append(_Blob(x=cx, y=cy, diameter=short, axis_x=axis_x, axis_y=axis_y, streak=streak))
    return found


def _color_mask(cv2: object, np: object, hsv: object, lower: tuple[int, int, int], upper: tuple[int, int, int]) -> object:
    low = np.array(lower, dtype=np.uint8)
    high = np.array(upper, dtype=np.uint8)
    if lower[0] <= upper[0]:
        return cv2.inRange(hsv, low, high)
    upper_wrap = np.array((179, upper[1], upper[2]), dtype=np.uint8)
    lower_wrap = np.array((0, lower[1], lower[2]), dtype=np.uint8)
    return cv2.bitwise_or(cv2.inRange(hsv, low, upper_wrap), cv2.inRange(hsv, lower_wrap, high))


def _motion_mask(
    cv2: object,
    np: object,
    current: object,
    prior: object,
    older: bytes | None,
    width: int,
    height: int,
) -> object:
    moved = cv2.absdiff(current, prior).sum(axis=2) >= MOTION_DELTA
    if older is not None:
        earlier = np.frombuffer(older, dtype=np.uint8).reshape((height, width, 3))
        moved = moved & (cv2.absdiff(current, earlier).sum(axis=2) >= MOTION_DELTA)
    return moved.astype(np.uint8) * 255


def _streak_axis(np: object, labels: object, index: int, length: float) -> tuple[float, float, float]:
    ys, xs = np.where(labels == index)
    if len(xs) < 2:
        return 0.0, 0.0, 0.0
    dx = xs.astype(np.float64) - float(xs.mean())
    dy = ys.astype(np.float64) - float(ys.mean())
    cov_xx = float((dx * dx).mean())
    cov_yy = float((dy * dy).mean())
    cov_xy = float((dx * dy).mean())
    if abs(cov_xy) < 1e-6:
        axis_x, axis_y = (1.0, 0.0) if cov_xx >= cov_yy else (0.0, 1.0)
    else:
        lam = (cov_xx + cov_yy + float(np.hypot(cov_xx - cov_yy, 2 * cov_xy))) / 2
        axis_x = cov_xy
        axis_y = lam - cov_xx
        norm = float(np.hypot(axis_x, axis_y)) or 1.0
        axis_x /= norm
        axis_y /= norm
    return axis_x, axis_y, length


def _checked(sample: TrackSample) -> TrackSample:
    sample.as_observation()
    return sample


def _search_roi(roi: Roi | None, calibration: TableCalibration | None) -> Roi:
    if calibration is None or not calibration.ready or len(calibration.corners_px) != 4:
        if roi is None:
            return Roi(0, 0, 10**9, 10**9)
        return roi
    xs = [point[0] for point in calibration.corners_px]
    ys = [point[1] for point in calibration.corners_px]
    span = max(ys) - min(ys)
    top = max(0, min(ys) - span)
    table = Roi(min(xs), top, max(xs) - min(xs) + 1, max(ys) - top + 1)
    if roi is None:
        return table
    x0 = max(roi.x, table.x)
    y0 = max(roi.y, table.y)
    x1 = min(roi.x + roi.width, table.x + table.width)
    y1 = min(roi.y + roi.height, table.y + table.height)
    if x1 <= x0 or y1 <= y0:
        return Roi(0, 0, 0, 0)
    return Roi(x0, y0, x1 - x0, y1 - y0)


def _bgr_bytes(frame: Frame) -> bytes:
    image = frame.image
    expected = frame.width * frame.height * 3
    if isinstance(image, BgrImage):
        return bytes(image.data)
    tobytes = getattr(image, "tobytes", None)
    if callable(tobytes):
        raw = bytes(tobytes())
        if len(raw) == expected:
            return raw
    data = getattr(image, "data", None)
    if isinstance(data, (bytes, bytearray)) and len(data) == expected:
        return bytes(data)
    raise ValueError("tracker expects a BgrImage or a BGR array")


def _dt(previous_ns: int | None, now_ns: int) -> float:
    if previous_ns is None:
        return 0.0
    elapsed = now_ns - previous_ns
    if elapsed <= 0:
        return 1e-6
    return elapsed / 1_000_000_000


def _initial_cov() -> list[list[float]]:
    return [
        [25.0, 0.0, 0.0, 0.0],
        [0.0, 25.0, 0.0, 0.0],
        [0.0, 0.0, 250_000.0, 0.0],
        [0.0, 0.0, 0.0, 250_000.0],
    ]


def _predict_cov(cov: list[list[float]], dt: float) -> list[list[float]]:
    transition = [
        [1.0, 0.0, dt, 0.0],
        [0.0, 1.0, 0.0, dt],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    process = [
        [dt, 0.0, 0.0, 0.0],
        [0.0, dt, 0.0, 0.0],
        [0.0, 0.0, _VELOCITY_VAR * dt, 0.0],
        [0.0, 0.0, 0.0, _VELOCITY_VAR * dt],
    ]
    return _add(_matmul(_matmul(transition, cov), _transpose(transition)), process)


def _correct(state: list[float], cov: list[list[float]], measured_x: float, measured_y: float) -> None:
    s00 = cov[0][0] + _MEASUREMENT_VAR
    s01 = cov[0][1]
    s10 = cov[1][0]
    s11 = cov[1][1] + _MEASUREMENT_VAR
    det = s00 * s11 - s01 * s10
    if abs(det) < 1e-9:
        return
    inv00 = s11 / det
    inv01 = -s01 / det
    inv10 = -s10 / det
    inv11 = s00 / det
    innovation_x = measured_x - state[0]
    innovation_y = measured_y - state[1]
    gain = []
    for row in range(4):
        gain.append(
            (
                cov[row][0] * inv00 + cov[row][1] * inv10,
                cov[row][0] * inv01 + cov[row][1] * inv11,
            )
        )
        state[row] += gain[row][0] * innovation_x + gain[row][1] * innovation_y
    updated = [[0.0] * 4 for _ in range(4)]
    for row in range(4):
        for col in range(4):
            updated[row][col] = cov[row][col] - gain[row][0] * cov[0][col] - gain[row][1] * cov[1][col]
    for row in range(4):
        cov[row][:] = updated[row]


def _matmul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    out = [[0.0] * len(right[0]) for _ in range(len(left))]
    for i, row in enumerate(left):
        for k, value in enumerate(row):
            if value == 0.0:
                continue
            for j in range(len(right[0])):
                out[i][j] += value * right[k][j]
    return out


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [list(column) for column in zip(*matrix, strict=True)]


def _add(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [[a + b for a, b in zip(row_a, row_b, strict=True)] for row_a, row_b in zip(left, right, strict=True)]


def _ring(image: BgrImage, cx: int, cy: int, radius: int, color: tuple[int, int, int]) -> None:
    x = radius
    y = 0
    err = 1 - x
    while x >= y:
        for px, py in (
            (cx + x, cy + y),
            (cx + y, cy + x),
            (cx - y, cy + x),
            (cx - x, cy + y),
            (cx - x, cy - y),
            (cx - y, cy - x),
            (cx + y, cy - x),
            (cx + x, cy - y),
        ):
            if 0 <= px < image.width and 0 <= py < image.height:
                image.set(px, py, color)
        y += 1
        if err < 0:
            err += 2 * y + 1
        else:
            x -= 1
            err += 2 * (y - x) + 1
