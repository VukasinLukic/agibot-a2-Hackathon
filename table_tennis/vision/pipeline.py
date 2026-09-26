"""Kandidati -> BallNet -> MHT, na radnoj širini. Tačka se vraća u pikselima originalnog kadra.

Kadar širi od ``work_width`` se smanjuje (``INTER_AREA``), uži ostaje kakav je.
ROI (kalibrisani sto plus pojas iznad, ili ``roi`` iz configa) ograničava
kandidate. Vreme za tracker je u kadrovima od 30 fps, iz ``capture_monotonic_ns``,
pa ispušten kadar ne kvari brzinu. ``last_ms`` meri svaki korak (resize je u ``candidates``).
"""

from __future__ import annotations

import math
import time
from typing import Any, Protocol

import cv2
import numpy as np

from table_tennis.vision.candidates import REF_WIDTH, CandidateParams, CandidateSource
from table_tennis.vision.config import Roi
from table_tennis.vision.frame import Frame
from table_tennis.vision.image import BgrImage
from table_tennis.vision.mht import TrackHit, Tracker, TrackerParams

TRACK_UNIT_NS = 1_000_000_000 / 30


class PatchScorer(Protocol):
    def probs(self, patches: np.ndarray) -> Any: ...


class BallNetPipeline:
    def __init__(
        self,
        net: PatchScorer,
        *,
        work_width: int = REF_WIDTH,
        compensate: bool = True,
        roi: Roi | None = None,
        tracker_params: TrackerParams | None = None,
        candidate_params: CandidateParams | None = None,
    ) -> None:
        if work_width <= 0:
            raise ValueError("work_width must be positive")
        self._net = net
        self._work_width = work_width
        self._roi = roi
        self._tracker_params = tracker_params or TrackerParams()
        self._candidate_params = candidate_params or CandidateParams(compensate=compensate)
        self._size: tuple[int, int] | None = None
        self._work: tuple[int, int] = (0, 0)
        self._scale = (1.0, 1.0)
        self._source: CandidateSource | None = None
        self._tracker: Tracker | None = None
        self._origin_ns: int | None = None
        self.last_candidates = 0
        self.last_ms = {"candidates": 0.0, "patches": 0.0, "net": 0.0, "tracker": 0.0}

    def step(self, frame: Frame) -> TrackHit | None:
        """Jedan kadar. ``TrackHit`` je u pikselima ulaznog kadra, ``None`` znači bez loptice."""
        started = time.perf_counter()
        image = _bgr_array(frame)
        source, tracker = self._ready(frame.width, frame.height)
        if self._work != (frame.width, frame.height):
            image = cv2.resize(image, self._work, interpolation=_resize_interpolation(frame.width, frame.height, self._work))
        candidates = source.step(image)
        found = time.perf_counter()
        patches = source.patches(candidates) if candidates else None
        cut = time.perf_counter()
        probs = self._net.probs(patches) if patches is not None else ()
        scored = time.perf_counter()
        if self._origin_ns is None:
            self._origin_ns = frame.capture_monotonic_ns
        t = (frame.capture_monotonic_ns - self._origin_ns) / TRACK_UNIT_NS
        hit = tracker.step(t, candidates, [float(p) for p in probs])
        done = time.perf_counter()
        self.last_candidates = len(candidates)
        self.last_ms = {
            "candidates": (found - started) * 1000.0,
            "patches": (cut - found) * 1000.0,
            "net": (scored - cut) * 1000.0,
            "tracker": (done - scored) * 1000.0,
        }
        if hit is None:
            return None
        sx, sy = self._scale
        return TrackHit(hit.kind, (hit.x + 0.5) * sx - 0.5, (hit.y + 0.5) * sy - 0.5, hit.track_id, hit.score, hit.prob)

    def _ready(self, width: int, height: int) -> tuple[CandidateSource, Tracker]:
        if self._size == (width, height) and self._source is not None and self._tracker is not None:
            return self._source, self._tracker
        work_w = min(width, self._work_width)
        work_h = max(1, round(height * work_w / width))
        self._size = (width, height)
        self._work = (work_w, work_h)
        self._scale = (width / work_w, height / work_h)
        self._source = CandidateSource(work_w, work_h, self._candidate_params, _work_roi(self._roi, self._scale))
        self._tracker = Tracker(self._tracker_params.for_frame(work_w, work_h, REF_WIDTH))
        self._origin_ns = None
        return self._source, self._tracker


def _resize_interpolation(width: int, height: int, work: tuple[int, int]) -> int:
    """``INTER_AREA`` only when the scale is a whole number. Otherwise it is much slower."""
    work_w, work_h = work
    if work_w > 0 and work_h > 0 and width % work_w == 0 and height % work_h == 0:
        return cv2.INTER_AREA
    return cv2.INTER_LINEAR


def _work_roi(roi: Roi | None, scale: tuple[float, float]) -> tuple[int, int, int, int] | None:
    if roi is None:
        return None
    sx, sy = scale
    x0 = math.floor(roi.x / sx)
    y0 = math.floor(roi.y / sy)
    x1 = math.ceil((roi.x + roi.width) / sx)
    y1 = math.ceil((roi.y + roi.height) / sy)
    return x0, y0, x1, y1


def _bgr_array(frame: Frame) -> np.ndarray:
    """H × W × 3 uint8 bez kopije kad god može."""
    image = frame.image
    if isinstance(image, BgrImage):
        return np.frombuffer(image.data, dtype=np.uint8).reshape(image.height, image.width, 3)
    array = np.asarray(image)
    if array.dtype != np.uint8 or array.shape != (frame.height, frame.width, 3):
        raise ValueError("tracker expects a BgrImage or a BGR uint8 array")
    return np.ascontiguousarray(array)
