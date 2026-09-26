"""Deterministic ball track. Color and motion only; no model and no score.

HSV bounds, diameter and the missing-frame limit come from ``VisionConfig``.
A short gap is ``predicted``. A longer gap is ``missing`` and drops the track.
Neither kind is a bounce or a point.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from table_tennis.vision.calibration import TableCalibration
from table_tennis.vision.config import Roi, VisionConfig
from table_tennis.vision.frame import Frame
from table_tennis.vision.image import BgrImage

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
        return {
            "frame_seq": self.frame_seq,
            "capture_monotonic_ns": self.capture_monotonic_ns,
            "detected": self.detected,
            "x_px": self.x_px,
            "y_px": self.y_px,
            "observation_kind": self.observation_kind,
            "confidence": self.confidence,
            "calibration_id": self.calibration_id,
        }


@dataclass(frozen=True, slots=True)
class _Blob:
    x: float
    y: float
    diameter: int


class BallTracker:
    def __init__(self, config: VisionConfig, calibration: TableCalibration | None = None) -> None:
        if not config.ball.configured or config.ball.hsv_lower is None or config.ball.hsv_upper is None:
            raise ValueError("ball color is not configured")
        self._lower = config.ball.hsv_lower
        self._upper = config.ball.hsv_upper
        self._min_diameter = config.min_diameter_px
        self._max_diameter = config.max_diameter_px
        self._missing_limit = config.missing_frames
        self._roi = _search_roi(config.roi, calibration)
        self._calibration_id = calibration.calibration_id if calibration is not None and calibration.ready else None
        self._previous: bytes | None = None
        self._last_ns: int | None = None
        self._misses = 0
        self._state: list[float] | None = None
        self._cov: list[list[float]] | None = None

    def update(self, frame: Frame) -> TrackSample:
        pixels = _bgr_bytes(frame)
        blobs = _blobs(
            pixels,
            frame.width,
            frame.height,
            self._previous,
            self._lower,
            self._upper,
            self._roi,
            self._min_diameter,
            self._max_diameter,
        )
        self._previous = pixels
        chosen = _choose(blobs, self._state, self._cov, self._last_ns, frame.capture_monotonic_ns, self._max_diameter)
        if chosen is not None:
            self._misses = 0
            sample = self._observed(frame, chosen)
            self._last_ns = frame.capture_monotonic_ns
            return sample
        if self._state is None:
            return self._missing(frame)
        dt = _dt(self._last_ns, frame.capture_monotonic_ns)
        self._last_ns = frame.capture_monotonic_ns
        self._predict(dt)
        self._misses += 1
        if self._misses >= self._missing_limit:
            self._state = None
            self._cov = None
            self._misses = 0
            return self._missing(frame)
        assert self._state is not None
        return self._predicted(frame, self._state[0], self._state[1])

    def _observed(self, frame: Frame, blob: _Blob) -> TrackSample:
        if self._state is None or self._cov is None:
            self._state = [blob.x, blob.y, 0.0, 0.0]
            self._cov = _initial_cov()
        else:
            self._predict(_dt(self._last_ns, frame.capture_monotonic_ns))
            _correct(self._state, self._cov, blob.x, blob.y)
        return TrackSample(
            frame_seq=frame.frame_seq,
            capture_monotonic_ns=frame.capture_monotonic_ns,
            detected=True,
            x_px=blob.x,
            y_px=blob.y,
            observation_kind="observed",
            confidence=0.8,
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
    gate = float(max(max_diameter * 2, 12))
    near = [blob for blob in blobs if (blob.x - pred_x) ** 2 + (blob.y - pred_y) ** 2 <= gate * gate]
    if len(near) == 1:
        return near[0]
    return None


def _blobs(
    pixels: bytes,
    width: int,
    height: int,
    previous: bytes | None,
    lower: tuple[int, int, int],
    upper: tuple[int, int, int],
    roi: Roi,
    min_diameter: int,
    max_diameter: int,
) -> list[_Blob]:
    if previous is None:
        return []
    mask = bytearray(width * height)
    x1 = min(width, roi.x + roi.width)
    y1 = min(height, roi.y + roi.height)
    for y in range(max(0, roi.y), y1):
        for x in range(max(0, roi.x), x1):
            index = (y * width + x) * 3
            if not _hsv_match(pixels[index], pixels[index + 1], pixels[index + 2], lower, upper):
                continue
            if _channel_delta(pixels, previous, index) < MOTION_DELTA:
                continue
            mask[y * width + x] = 1
    found: list[_Blob] = []
    seen = bytearray(width * height)
    for y in range(max(0, roi.y), y1):
        for x in range(max(0, roi.x), x1):
            start = y * width + x
            if mask[start] == 0 or seen[start]:
                continue
            blob = _component(mask, seen, width, height, x, y)
            if blob is not None and min_diameter <= blob.diameter <= max_diameter:
                found.append(blob)
    return found


def _component(
    mask: bytearray,
    seen: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
) -> _Blob | None:
    stack = [(x, y)]
    seen[y * width + x] = 1
    count = 0
    sum_x = 0
    sum_y = 0
    min_x = x
    max_x = x
    min_y = y
    max_y = y
    while stack:
        cx, cy = stack.pop()
        count += 1
        sum_x += cx
        sum_y += cy
        min_x = min(min_x, cx)
        max_x = max(max_x, cx)
        min_y = min(min_y, cy)
        max_y = max(max_y, cy)
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            flat = ny * width + nx
            if mask[flat] == 0 or seen[flat]:
                continue
            seen[flat] = 1
            stack.append((nx, ny))
    box_w = max_x - min_x + 1
    box_h = max_y - min_y + 1
    if box_w == 0 or box_h == 0:
        return None
    aspect = box_w / box_h if box_w < box_h else box_h / box_w
    if aspect < 0.6 or count / (box_w * box_h) < 0.45:
        return None
    return _Blob(x=sum_x / count, y=sum_y / count, diameter=max(box_w, box_h))


def _hsv_match(b: int, g: int, r: int, lower: tuple[int, int, int], upper: tuple[int, int, int]) -> bool:
    hue, saturation, value = _hsv(b, g, r)
    if not _hue_in(hue, lower[0], upper[0]):
        return False
    return lower[1] <= saturation <= upper[1] and lower[2] <= value <= upper[2]


def _hsv(b: int, g: int, r: int) -> tuple[int, int, int]:
    maximum = max(b, g, r)
    span = maximum - min(b, g, r)
    if span == 0 or maximum == 0:
        return 0, 0, maximum
    if maximum == r:
        hue = (60.0 * ((g - b) / span) + 360.0) % 360.0
    elif maximum == g:
        hue = (60.0 * ((b - r) / span) + 120.0) % 360.0
    else:
        hue = (60.0 * ((r - g) / span) + 240.0) % 360.0
    return int(hue / 2.0), int(span / maximum * 255.0), maximum


def _hue_in(hue: int, lower: int, upper: int) -> bool:
    if lower <= upper:
        return lower <= hue <= upper
    return hue >= lower or hue <= upper


def _channel_delta(current: bytes, previous: bytes, index: int) -> int:
    return (
        abs(current[index] - previous[index])
        + abs(current[index + 1] - previous[index + 1])
        + abs(current[index + 2] - previous[index + 2])
    )


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
    if isinstance(image, BgrImage):
        return bytes(image.data)
    data = getattr(image, "data", None)
    if isinstance(data, (bytes, bytearray)) and len(data) == frame.width * frame.height * 3:
        return bytes(data)
    raise ValueError("tracker expects a BgrImage")


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
