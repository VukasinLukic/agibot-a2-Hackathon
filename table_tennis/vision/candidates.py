"""Kandidati za lopticu: razlika tri kadra posle kompenzacije pokreta kamere.

Kamera se pomera sa robotom. Prethodna dva kadra se poravnaju na trenutni
(LK optički tok na 320 px širine, ``estimateAffinePartial2D`` sa RANSAC-om),
pa ostaje ono što je *svetlije* od oba poravnata kadra za više od praga.
Poređenje ide sa 3 × 3 dilatacijom starih kadrova, da sitna greška poravnanja
ne pravi kandidate na ivicama.

Pragovi površine i veličina patch-a su podešeni na širini ``REF_WIDTH`` i
skaliraju se sa radnom širinom. Kandidat nije loptica; to odlučuje ``BallNet``.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

REF_WIDTH = 960
PATCH = 32
_KERNEL = np.ones((3, 3), np.uint8)


@dataclass(frozen=True, slots=True)
class CandidateParams:
    """Pikseli i površine važe na ``REF_WIDTH``; ``CandidateSource`` ih skalira."""

    threshold: int = 18
    min_area: float = 6.0
    max_area: float = 2500.0
    max_candidates: int = 128
    patch_min_side: float = 24.0
    patch_scale: float = 1.6
    compensate: bool = True
    feature_width: int = 320
    max_features: int = 200
    # Also a ball darker than both old frames: in front of a bright window the ball
    # is not brighter than what it covers. Off keeps the original behaviour.
    darker: bool = False


@dataclass(frozen=True, slots=True)
class Candidate:
    """Komponenta maske u pikselima radnog kadra. ``contrast``/``cmax`` su iznad oba stara kadra."""

    x: float
    y: float
    bw: int
    bh: int
    area: int
    fill: float
    sat: float
    val: float
    contrast: float
    cmax: float


class Aligner:
    """Affine prethodni -> trenutni kadar. Neuspeh je identitet, ne greška."""

    def __init__(self, feature_width: int, max_features: int) -> None:
        self._feature_width = feature_width
        self._max_features = max_features
        self._previous: np.ndarray | None = None

    def step(self, gray: np.ndarray) -> np.ndarray:
        height, width = gray.shape
        small_w = min(self._feature_width, width)
        small = cv2.resize(gray, (small_w, max(1, round(small_w * height / width))), interpolation=cv2.INTER_AREA)
        motion = np.eye(2, 3, dtype=np.float32)
        previous = self._previous
        self._previous = small
        if previous is None or previous.shape != small.shape:
            return motion
        points = cv2.goodFeaturesToTrack(previous, self._max_features, 0.01, 6)
        if points is None or len(points) < 12:
            return motion
        moved, status, _err = cv2.calcOpticalFlowPyrLK(previous, small, points, None, winSize=(15, 15), maxLevel=3)
        ok = status.ravel() == 1
        if int(ok.sum()) < 12:
            return motion
        affine, _inliers = cv2.estimateAffinePartial2D(
            points[ok], moved[ok], method=cv2.RANSAC, ransacReprojThreshold=0.7
        )
        if affine is None:
            return motion
        motion = affine.astype(np.float32)
        motion[:, 2] *= width / small_w
        return motion


class CandidateSource:
    """Jedan poziv ``step`` po kadru radne veličine. Prva dva kadra nemaju kandidate."""

    def __init__(
        self,
        width: int,
        height: int,
        params: CandidateParams | None = None,
        roi: tuple[int, int, int, int] | None = None,
    ) -> None:
        self.params = params or CandidateParams()
        self.width = width
        self.height = height
        scale = width / REF_WIDTH
        self._min_area = max(2.0, self.params.min_area * scale * scale)
        self._max_area = self.params.max_area * scale * scale
        self._patch_min = max(8.0, self.params.patch_min_side * scale)
        if roi is None:
            roi = (0, 0, width, height)
        x0, y0, x1, y1 = roi
        self.roi = (max(0, x0), max(0, y0), min(width, x1), min(height, y1))
        self._aligner = Aligner(self.params.feature_width, self.params.max_features) if self.params.compensate else None
        self._history: list[tuple[np.ndarray, np.ndarray | None]] = []
        self._bgr: np.ndarray | None = None
        self._current: np.ndarray | None = None
        self._warped: np.ndarray | None = None

    def step(self, bgr: np.ndarray) -> list[Candidate]:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        motion = self._aligner.step(gray) if self._aligner is not None else None
        self._history.append((gray, motion))
        del self._history[:-3]
        self._bgr = None
        x0, y0, x1, y1 = self.roi
        if len(self._history) < 3 or x1 <= x0 or y1 <= y0:
            return []
        (older, _), (previous, motion_1), (_current, motion_0) = self._history
        current = gray[y0:y1, x0:x1]
        if motion_0 is None or motion_1 is None:
            warped_1 = previous[y0:y1, x0:x1]
            warped_2 = older[y0:y1, x0:x1]
        else:
            size = (x1 - x0, y1 - y0)
            warped_1 = _warp(previous, motion_0, x0, y0, size)
            warped_2 = _warp(older, _compose(motion_1, motion_0), x0, y0, size)
        local_1 = cv2.dilate(warped_1, _KERNEL)
        local_2 = cv2.dilate(warped_2, _KERNEL)
        # uint8 subtract saturates at 0; the threshold is positive, so the mask is the same.
        brighter = cv2.min(cv2.subtract(current, local_1), cv2.subtract(current, local_2))
        moved = brighter > self.params.threshold
        if self.params.darker:
            dim_1 = cv2.erode(warped_1, _KERNEL)
            dim_2 = cv2.erode(warped_2, _KERNEL)
            darker = cv2.min(cv2.subtract(dim_1, current), cv2.subtract(dim_2, current))
            moved |= darker > self.params.threshold
        mask = cv2.morphologyEx(moved.view(np.uint8), cv2.MORPH_CLOSE, _KERNEL)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
        self._bgr = bgr
        self._current = current
        self._warped = warped_1
        if count <= 1:
            return []
        areas = stats[1:, cv2.CC_STAT_AREA]
        keep = np.flatnonzero((areas >= self._min_area) & (areas <= self._max_area)) + 1
        found: list[Candidate] = []
        for index in keep.tolist():
            left, top, box_w, box_h, area = (int(value) for value in stats[index])
            rows = slice(top, top + box_h)
            cols = slice(left, left + box_w)
            inside = labels[rows, cols] == index
            box = bgr[y0 + top : y0 + top + box_h, x0 + left : x0 + left + box_w]
            hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)[inside]
            # Signed, as in the prototype: closing may add pixels darker than the old frames.
            here = current[rows, cols].astype(np.int16)
            lift = np.minimum(here - local_1[rows, cols], here - local_2[rows, cols])[inside]
            if self.params.darker:
                drop = np.minimum(dim_1[rows, cols] - here, dim_2[rows, cols] - here)[inside]
                if drop.mean() > lift.mean():
                    lift = drop  # contrast is how far it stands out, either way
            found.append(
                Candidate(
                    x=float(centroids[index, 0]) + x0,
                    y=float(centroids[index, 1]) + y0,
                    bw=box_w,
                    bh=box_h,
                    area=area,
                    fill=area / (box_w * box_h),
                    sat=float(hsv[:, 1].mean()),
                    val=float(hsv[:, 2].mean()),
                    contrast=float(lift.mean()),
                    cmax=float(lift.max()),
                )
            )
        if len(found) > self.params.max_candidates:
            found.sort(key=lambda item: item.contrast, reverse=True)
            del found[self.params.max_candidates :]
        return found

    def patch_side(self, candidate: Candidate) -> float:
        return max(self._patch_min, self.params.patch_scale * max(candidate.bw, candidate.bh))

    def patches(self, candidates: list[Candidate]) -> np.ndarray:
        """N × 32 × 32 × 4 float32 u [0, 1]: B, G, R trenutnog kadra i diff prema prethodnom (+128).

        Svaki patch se seče iz malog isečka oko kandidata, ne iz celog kadra.
        """
        out = np.zeros((len(candidates), PATCH, PATCH, 4), np.uint8)
        if not candidates or self._bgr is None or self._current is None or self._warped is None:
            return out.astype(np.float32)
        x0, y0, x1, y1 = self.roi
        crop_w, crop_h = x1 - x0, y1 - y0
        for index, candidate in enumerate(candidates):
            side = self.patch_side(candidate)
            cx = candidate.x - x0
            cy = candidate.y - y0
            left = max(0, int(np.floor(cx - side / 2)) - 2)
            top = max(0, int(np.floor(cy - side / 2)) - 2)
            right = min(crop_w, int(np.ceil(cx + side / 2)) + 3)
            bottom = min(crop_h, int(np.ceil(cy + side / 2)) + 3)
            if right <= left or bottom <= top:
                continue
            current = self._current[top:bottom, left:right].astype(np.int16)
            diff = np.clip(current - self._warped[top:bottom, left:right] + 128, 0, 255).astype(np.uint8)
            color = self._bgr[y0 + top : y0 + bottom, x0 + left : x0 + right]
            stack = np.dstack((color, diff))
            step = side / PATCH
            inverse = np.float32([[step, 0, cx - side / 2 - left], [0, step, cy - side / 2 - top]])
            out[index] = cv2.warpAffine(
                stack,
                inverse,
                (PATCH, PATCH),
                flags=cv2.INTER_AREA | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REPLICATE,
            )
        return out.astype(np.float32) * np.float32(1.0 / 255.0)


def _compose(first: np.ndarray, then: np.ndarray) -> np.ndarray:
    """``first`` pa ``then``, oba 2 × 3."""
    a = np.vstack([first, (0.0, 0.0, 1.0)])
    b = np.vstack([then, (0.0, 0.0, 1.0)])
    return (b @ a)[:2].astype(np.float32)


def _warp(gray: np.ndarray, motion: np.ndarray, x0: int, y0: int, size: tuple[int, int]) -> np.ndarray:
    """Stari kadar u koordinatama trenutnog, samo za isečak koji počinje u (x0, y0)."""
    shifted = motion.copy()
    shifted[0, 2] -= x0
    shifted[1, 2] -= y0
    return cv2.warpAffine(gray, shifted, size, borderMode=cv2.BORDER_REPLICATE)
