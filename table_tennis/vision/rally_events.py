"""Bounce and hit candidates from the ball track. Evidence for a proposal, not a point.

A bounce is a sharp down-to-up turn of the image y velocity. A hit is a flip
of the x velocity with speed on both sides (the ball goes back across the
table). Both need three observed samples close in time, so an event comes out
one sample late. With a ready calibration a bounce must project inside the
table (plus a small margin); a bounce in a hand or on the floor is dropped.
The half is the same 8% net split as a point proposal, so a bounce in the net
band is dropped too. Without calibration ``side`` stays None.

Thresholds are for a 960 px wide working frame and scale with the frame width.
On the tuning clip (handheld phone, 30 fps) 23 of 26 labeled table bounces
were found on the held-out halves. Hits are often hidden by the paddle, so
treat a missing hit as unknown, not as no hit.
"""

from __future__ import annotations

from dataclasses import dataclass

from table_tennis.vision.calibration import TABLE_LENGTH_MM, TABLE_WIDTH_MM, TableCalibration
from table_tennis.vision.table import table_half
from table_tennis.vision.track import TrackSample

REFERENCE_WIDTH_PX = 960
TURN_SPEED_PX_S = 120.0
HIT_SPEED_PX_S = 150.0
MAX_GAP_NS = 100_000_000
TABLE_MARGIN_MM = 60.0
_MERGE_NS = 50_000_000


@dataclass(frozen=True, slots=True)
class RallyEvent:
    kind: str
    frame_seq: int
    capture_monotonic_ns: int
    x_px: float
    y_px: float
    side: str | None
    confidence: float


class RallyEventDetector:
    def __init__(self, frame_width: int, calibration: TableCalibration | None = None) -> None:
        if frame_width <= 0:
            raise ValueError("frame_width must be positive")
        scale = frame_width / REFERENCE_WIDTH_PX
        self._turn = TURN_SPEED_PX_S * scale
        self._hit = HIT_SPEED_PX_S * scale
        ready = calibration is not None and calibration.ready and calibration.homography is not None
        self._calibration = calibration if ready else None
        self._recent: list[TrackSample] = []
        self._last: RallyEvent | None = None

    def add(self, sample: TrackSample) -> list[RallyEvent]:
        """Feed every sample. Predicted and missing samples break the chain."""
        if sample.observation_kind != "observed" or sample.x_px is None or sample.y_px is None:
            self._recent.clear()
            return []
        if self._recent and sample.capture_monotonic_ns - self._recent[-1].capture_monotonic_ns > MAX_GAP_NS:
            self._recent.clear()
        self._recent.append(sample)
        del self._recent[:-3]
        if len(self._recent) < 3:
            return []
        event = self._classify(*self._recent)
        if event is None:
            return []
        last = self._last
        if (
            last is not None
            and event.capture_monotonic_ns - last.capture_monotonic_ns <= _MERGE_NS
            and (event.kind == last.kind or last.kind == "hit")
        ):
            return []
        self._last = event
        return [event]

    def reset(self) -> None:
        self._recent.clear()
        self._last = None

    def _classify(self, a: TrackSample, b: TrackSample, c: TrackSample) -> RallyEvent | None:
        dt1 = (b.capture_monotonic_ns - a.capture_monotonic_ns) / 1e9
        dt2 = (c.capture_monotonic_ns - b.capture_monotonic_ns) / 1e9
        if dt1 <= 0 or dt2 <= 0:
            return None
        assert a.x_px is not None and a.y_px is not None
        assert b.x_px is not None and b.y_px is not None
        assert c.x_px is not None and c.y_px is not None
        vx1, vy1 = (b.x_px - a.x_px) / dt1, (b.y_px - a.y_px) / dt1
        vx2, vy2 = (c.x_px - b.x_px) / dt2, (c.y_px - b.y_px) / dt2
        confidence = min(a.confidence, b.confidence, c.confidence)
        if vx1 * vx2 < 0 and min(abs(vx1), abs(vx2)) >= self._hit:
            return self._event("hit", b, None, confidence)
        if vy1 >= self._turn and vy2 <= -self._turn:
            side = self._table_side(b.x_px, b.y_px)
            if self._calibration is not None and side is None:
                return None
            return self._event("bounce", b, side, confidence)
        return None

    def _table_side(self, x_px: float, y_px: float) -> str | None:
        if self._calibration is None:
            return None
        projected = self._calibration.project_to_table_plane(x_px, y_px)
        margin = TABLE_MARGIN_MM
        if not (-margin <= projected.x_mm <= TABLE_WIDTH_MM + margin):
            return None
        if not (-margin <= projected.y_mm <= TABLE_LENGTH_MM + margin):
            return None
        return table_half(projected.y_mm)

    @staticmethod
    def _event(kind: str, sample: TrackSample, side: str | None, confidence: float) -> RallyEvent:
        assert sample.x_px is not None and sample.y_px is not None
        return RallyEvent(
            kind=kind,
            frame_seq=sample.frame_seq,
            capture_monotonic_ns=sample.capture_monotonic_ns,
            x_px=sample.x_px,
            y_px=sample.y_px,
            side=side,
            confidence=confidence,
        )
