"""One rally, from the serve to a single proposal. Vision still only proposes.

The half of a bounce comes from the bounce, because the ball is on the table
then. While the ball is in the air, which side of the net it is on, and whether
it has passed an end, are read from the image. The chest camera sits in the
plane of the net, so those two lines stay true at any height. The table-plane
homography does not: a ball above the table is thrown toward the camera.

A disappearance counts only while it is still going on. One over the middle of
the table is an occlusion, and the rally continues if the ball comes back
within two seconds. A disappearance near or past an end closes the rally:
later bounces, such as a ball tapped while walking back to serve, are not a
new point.

This does not arm a rally and does not write the score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from table_tennis.vision.calibration import TableCalibration
from table_tennis.vision.rally_events import RallyEvent
from table_tennis.vision.table import NET_BAND
from table_tennis.vision.track import TrackSample

GONE_NS = 500_000_000
LONG_GAP_NS = 2_000_000_000
# After the ball crossed the net at least once, this long without it means the
# rally is over even when the picture never showed how.
UNCLEAR_GONE_NS = 3_000_000_000


@dataclass(frozen=True, slots=True)
class Verdict:
    winner_id: str
    reason: str
    start_seq: int
    end_seq: int


class PointFold:
    """Folds new samples onto the rally so far. Each sample is read once."""

    def __init__(self, server_id: str, ends: object, calibration: TableCalibration) -> None:
        self._calibration = calibration
        server_end = _end(ends, server_id)
        receiver_id = "p2" if server_id == "p1" else "p1"
        receiver_end = _end(ends, receiver_id)
        self._ready = server_id in {"p1", "p2"} and server_end is not None and receiver_end is not None and server_end != receiver_end
        self._play = _Play(server_id, server_end or "", receiver_id, receiver_end or "")
        self._bounces: set[int] = set()
        self._last: TrackSample | None = None
        self._previous_half: str | None = None
        self._previous_seq: int | None = None
        self._gap = False
        self._terminal = False
        self._closed = False
        self._start: int | None = None
        self._verdict: Verdict | None = None
        self._crossings = 0

    def extend(self, samples: list[TrackSample], events: list[RallyEvent]) -> None:
        if not self._ready or self._closed or self._verdict is not None:
            return
        # An event names an earlier frame than the sample that revealed it, so
        # it is folded before any later sample.
        pending = sorted(events, key=lambda event: (event.frame_seq, 0 if event.kind == "bounce" else 1))
        index = 0
        for sample in samples:
            while index < len(pending) and pending[index].frame_seq <= sample.frame_seq:
                self._event(pending[index])
                index += 1
                if self._verdict is not None:
                    return
            if self._closed or self._verdict is not None:
                return
            self._sample(sample)
        for event in pending[index:]:
            if self._verdict is not None or self._closed:
                return
            self._event(event)

    def verdict(self) -> Verdict | None:
        return self._verdict

    def _event(self, event: RallyEvent) -> None:
        if self._closed or self._terminal:
            return
        if self._start is None:
            self._start = event.frame_seq
        if event.kind == "bounce":
            self._bounces.add(event.frame_seq)
            self._play.bounce(event.side, event.frame_seq)
        elif event.kind == "hit":
            self._play.hit()
        decision = self._play.decision
        if decision is not None and decision[1] == "double_bounce":
            self._verdict = Verdict(decision[0], "double_bounce", self._start or event.frame_seq, self._play.end_seq)

    def _sample(self, sample: TrackSample) -> None:
        if self._start is None:
            self._start = sample.frame_seq
        observed = sample.observation_kind == "observed" and sample.x_px is not None and sample.y_px is not None
        if observed:
            last = self._last
            long_gap = self._gap and last is not None and sample.capture_monotonic_ns - last.capture_monotonic_ns > LONG_GAP_NS
            if self._terminal or long_gap:
                self._closed = True
                return
            self._gap = False
            self._cross(sample)
            self._last = sample
            return
        last = self._last
        if sample.observation_kind != "missing" or last is None or self._gap:
            return
        if sample.capture_monotonic_ns - last.capture_monotonic_ns < GONE_NS:
            return
        self._gap = True
        self._finish(last, sample.frame_seq)

    def ended_without_verdict(self, now_ns: int) -> bool:
        """The rally is over and there is no point to propose: time to ask the players.

        Only after real play. A rally that closes during the serve preparation
        (ball tossed, carried, bounced while waiting) is not a question.
        """
        if not self._ready or self._verdict is not None:
            return False
        if self._play.phase == "play" and (self._terminal or self._closed):
            return True
        last = self._last
        return self._crossings >= 1 and last is not None and now_ns - last.capture_monotonic_ns >= UNCLEAR_GONE_NS

    def _cross(self, sample: TrackSample) -> None:
        assert sample.x_px is not None and sample.y_px is not None
        half = _image_half(sample.x_px, sample.y_px, self._calibration)
        previous = self._previous_half
        previous_seq = self._previous_seq
        if half is not None and previous is not None and half != previous:
            self._crossings += 1
        if (
            half is not None
            and previous is not None
            and half != previous
            and previous_seq is not None
            and previous_seq not in self._bounces
            and sample.frame_seq not in self._bounces
        ):
            self._play.hit()
        if half is not None:
            self._previous_half = half
            self._previous_seq = sample.frame_seq

    def _finish(self, last: TrackSample, end_seq: int) -> None:
        assert last.x_px is not None and last.y_px is not None
        place = _image_place(last.x_px, last.y_px, self._calibration)
        start = self._start if self._start is not None else last.frame_seq
        if place in {"middle", "unknown"}:
            return
        if self._play.decision is not None:
            winner, reason = self._play.decision
            self._verdict = Verdict(winner, reason, start, end_seq)
            return
        play = self._play
        if play.phase == "play" and play.attacker is not None and play.opp_bounces >= 1 and _leaves(place, play.opponent_end()):
            self._verdict = Verdict(play.attacker, "missed_return", start, end_seq)
            return
        if play.phase == "play" and play.attacker is not None and play.opp_bounces == 0 and _leaves(place, play.opponent_end()):
            self._verdict = Verdict(_other(play.attacker), "out_after_hit", start, end_seq)
            return
        if play.phase == "service" and play.server_bounce and _leaves(place, play.server_end):
            self._verdict = Verdict(play.receiver_id, "service_fault", start, end_seq)
            return
        # The ball left near or past an end and the picture does not support a
        # point. Anything after it comes back, such as a ball tapped on the way
        # to serve, is not this rally.
        self._terminal = True


def conclude(
    samples: list[TrackSample],
    events: list[RallyEvent],
    *,
    server_id: str,
    ends: object,
    calibration: TableCalibration,
) -> Verdict | None:
    """Return one point, or nothing. ``ends`` has ``p1`` and ``p2`` court ends."""
    fold = PointFold(server_id, ends, calibration)
    fold.extend(samples, events)
    return fold.verdict()


class _Play:
    def __init__(self, server_id: str, server_end: str, receiver_id: str, receiver_end: str) -> None:
        self.server_id = server_id
        self.server_end = server_end
        self.receiver_id = receiver_id
        self.receiver_end = receiver_end
        self.phase = "service"
        self.attacker: str | None = None
        self.server_bounce = False
        self.opp_bounces = 0
        self.decision: tuple[str, str] | None = None
        self.end_seq = 0

    def opponent_end(self) -> str:
        assert self.attacker is not None
        return self.receiver_end if self.attacker == self.server_id else self.server_end

    def own_end(self) -> str:
        assert self.attacker is not None
        return self.server_end if self.attacker == self.server_id else self.receiver_end

    def bounce(self, side: str | None, frame_seq: int) -> None:
        if side is None or self.decision is not None:
            return
        self.end_seq = frame_seq
        if self.phase == "service":
            if side == self.server_end:
                self.server_bounce = True
            elif side == self.receiver_end and self.server_bounce:
                self.phase = "play"
                self.attacker = self.server_id
                self.opp_bounces = 1
            return
        if side == self.opponent_end():
            self.opp_bounces += 1
            if self.opp_bounces >= 2:
                self.decision = (self.attacker or self.server_id, "double_bounce")
        elif side == self.own_end() and self.opp_bounces >= 1:
            # The return itself was hidden. The ball is now on the new opponent's half.
            self.attacker = _other(self.attacker or self.server_id)
            self.opp_bounces = 1
        elif side == self.own_end():
            self.decision = (_other(self.attacker or self.server_id), "out_after_hit")

    def hit(self) -> None:
        if self.phase != "play" or self.attacker is None or self.decision is not None:
            return
        if self.opp_bounces >= 1:
            self.attacker = _other(self.attacker)
            self.opp_bounces = 0


def _image_half(x_px: float, y_px: float, calibration: TableCalibration) -> str | None:
    """Which end of the net line the pixel is on. Nothing when it is on the net."""
    signed, band = _net_distance(x_px, y_px, calibration)
    if band <= 0 or abs(signed) <= band:
        return None
    near = _mid(calibration, 0, 1)
    anchor, _ = _net_distance(near[0], near[1], calibration)
    if signed * anchor > 0:
        return "end_a"
    return "end_b"


def _image_place(x_px: float, y_px: float, calibration: TableCalibration) -> str:
    """``past_a`` / ``past_b`` are beyond the end lines. The side lines are not used.

    A ball above the table moves toward the camera, which sits beside the net,
    so it crosses a side line while it is still in play.
    """
    corners = calibration.corners_px
    if len(corners) != 4:
        return "unknown"
    near = _mid(calibration, 0, 1)
    far = _mid(calibration, 2, 3)
    if _beyond(x_px, y_px, far, corners[0], corners[1]):
        return "past_a"
    if _beyond(x_px, y_px, near, corners[2], corners[3]):
        return "past_b"
    signed, band = _net_distance(x_px, y_px, calibration)
    if band <= 0 or abs(signed) <= band:
        return "middle"
    span = (far[0] - near[0]) ** 2 + (far[1] - near[1]) ** 2
    if span <= 0:
        return "middle"
    along = ((x_px - near[0]) * (far[0] - near[0]) + (y_px - near[1]) * (far[1] - near[1])) / span
    if along <= 0.2 or along >= 0.8:
        return "end"
    return "middle"


def _mid(calibration: TableCalibration, i: int, j: int) -> tuple[float, float]:
    a = calibration.corners_px[i]
    b = calibration.corners_px[j]
    return (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0


def _net_distance(x_px: float, y_px: float, calibration: TableCalibration) -> tuple[float, float]:
    if len(calibration.corners_px) != 4:
        return 0.0, 0.0
    start, end = calibration.net_px
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length <= 0:
        return 0.0, 0.0
    signed = _cross(end[0] - start[0], end[1] - start[1], x_px - start[0], y_px - start[1]) / length
    near = _mid(calibration, 0, 1)
    far = _mid(calibration, 2, 3)
    table = math.hypot(far[0] - near[0], far[1] - near[1])
    return signed, table * NET_BAND


def _beyond(
    x_px: float,
    y_px: float,
    interior: tuple[float, float],
    a: tuple[int, int],
    b: tuple[int, int],
) -> bool:
    """True when the pixel is on the far side of the line through ``a`` and ``b``."""
    inside = _cross(b[0] - a[0], b[1] - a[1], interior[0] - a[0], interior[1] - a[1])
    point = _cross(b[0] - a[0], b[1] - a[1], x_px - a[0], y_px - a[1])
    return inside * point < 0


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _leaves(place: str, end: str) -> bool:
    return (place == "past_a" and end == "end_a") or (place == "past_b" and end == "end_b")


def _end(ends: object, player: str) -> str | None:
    value = getattr(ends, player, None)
    if value in {"end_a", "end_b"}:
        return value
    return None


def _other(player: str) -> str:
    return "p2" if player == "p1" else "p1"
