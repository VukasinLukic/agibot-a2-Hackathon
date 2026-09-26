"""One rally, from the serve to a single proposal. Vision still only proposes.

The ball's half comes from a bounce, because that is the moment it is on the
table. A point in the air is only a direction. A proposal waits until the ball
has been gone for half a second, unless the point is already over (a second
bounce on the same half). Disappearing over the middle of the table is an
occlusion and stays silent.

This does not arm a rally and does not write the score.
"""

from __future__ import annotations

from dataclasses import dataclass

from table_tennis.vision.calibration import TableCalibration
from table_tennis.vision.rally_events import RallyEvent
from table_tennis.vision.table import TABLE_LENGTH_MM, TABLE_WIDTH_MM, table_half
from table_tennis.vision.track import TrackSample

GONE_NS = 500_000_000
_PAST_MM = 80.0


@dataclass(frozen=True, slots=True)
class Verdict:
    winner_id: str
    reason: str
    start_seq: int
    end_seq: int


def conclude(
    samples: list[TrackSample],
    events: list[RallyEvent],
    *,
    server_id: str,
    ends: object,
    calibration: TableCalibration,
) -> Verdict | None:
    """Return one point, or nothing. ``ends`` has ``p1`` and ``p2`` court ends."""
    if server_id not in {"p1", "p2"}:
        return None
    server_end = _end(ends, server_id)
    receiver_id = "p2" if server_id == "p1" else "p1"
    receiver_end = _end(ends, receiver_id)
    if server_end is None or receiver_end is None or server_end == receiver_end:
        return None

    play = _Play(server_id, server_end, receiver_id, receiver_end)
    bounce_frames = {event.frame_seq for event in events if event.kind == "bounce"}
    marks: list[tuple[int, str, str | None]] = [(event.frame_seq, event.kind, event.side) for event in events]
    for frame_seq in _returns(samples, bounce_frames, calibration):
        marks.append((frame_seq, "hit", None))
    marks.sort(key=lambda item: (item[0], 0 if item[1] == "bounce" else 1))

    start_seq = samples[0].frame_seq if samples else 0
    for frame_seq, kind, side in marks:
        if play.decision is not None:
            break
        if kind == "bounce":
            play.bounce(side, frame_seq)
        elif kind == "hit":
            play.hit()
        start_seq = min(start_seq, frame_seq)

    if play.decision is not None and play.decision[1] == "double_bounce":
        return Verdict(play.decision[0], "double_bounce", start_seq, play.end_seq)

    gone = _gone(samples)
    if gone is None:
        return None
    last, end_seq = gone
    place = _place(last.x_px, last.y_px, calibration)
    if place in {"middle", "unknown"}:
        return None
    if play.decision is not None:
        winner, reason = play.decision
        return Verdict(winner, reason, start_seq, end_seq)
    left = place in {"past_a", "past_b", "floor"}
    if not left:
        return None
    if play.phase == "play" and play.attacker is not None and play.opp_bounces >= 1:
        if _leaves(place, play.opponent_end()) or place == "floor":
            return Verdict(play.attacker, "missed_return", start_seq, end_seq)
    if play.phase == "play" and play.attacker is not None and play.opp_bounces == 0:
        if _leaves(place, play.opponent_end()) or place == "floor":
            return Verdict(_other(play.attacker), "out_after_hit", start_seq, end_seq)
    if play.phase == "service":
        return Verdict(receiver_id, "service_fault", start_seq, end_seq)
    return None


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
            elif side == self.receiver_end and not self.server_bounce:
                self.decision = (self.receiver_id, "service_fault")
            elif side == self.receiver_end:
                self.phase = "play"
                self.attacker = self.server_id
                self.opp_bounces = 1
            return
        if side == self.opponent_end():
            self.opp_bounces += 1
            if self.opp_bounces >= 2:
                self.decision = (self.attacker or self.server_id, "double_bounce")
        elif side == self.own_end():
            self.decision = (_other(self.attacker or self.server_id), "out_after_hit")

    def hit(self) -> None:
        if self.phase != "play" or self.attacker is None or self.decision is not None:
            return
        if self.opp_bounces >= 1:
            self.attacker = _other(self.attacker)
            self.opp_bounces = 0


def _returns(samples: list[TrackSample], bounce_frames: set[int], calibration: TableCalibration) -> list[int]:
    """A return the detector missed: the ball is seen on both halves, and neither frame is a bounce."""
    found: list[int] = []
    previous_half: str | None = None
    previous_seq: int | None = None
    for sample in samples:
        if sample.observation_kind != "observed" or sample.x_px is None or sample.y_px is None:
            continue
        half = table_half(_plane_y(sample.x_px, sample.y_px, calibration))
        if (
            half is not None
            and previous_half is not None
            and half != previous_half
            and previous_seq is not None
            and previous_seq not in bounce_frames
            and sample.frame_seq not in bounce_frames
        ):
            found.append(sample.frame_seq)
        if half is not None:
            previous_half = half
            previous_seq = sample.frame_seq
    return found


def _gone(samples: list[TrackSample]) -> tuple[TrackSample, int] | None:
    """The last ball, once it has been missing for half a second without coming back."""
    last: TrackSample | None = None
    for sample in samples:
        if sample.observation_kind == "observed" and sample.x_px is not None and sample.y_px is not None:
            last = sample
            continue
        if last is None or sample.observation_kind != "missing":
            continue
        if sample.capture_monotonic_ns - last.capture_monotonic_ns >= GONE_NS:
            return last, sample.frame_seq
    return None


def _place(x_px: float | None, y_px: float | None, calibration: TableCalibration) -> str:
    """Where the last ball was. ``middle`` is still over the table and is not an ending."""
    if x_px is None or y_px is None:
        return "unknown"
    projected = calibration.project_to_table_plane(x_px, y_px)
    if projected.y_mm < -_PAST_MM:
        return "past_a"
    if projected.y_mm > TABLE_LENGTH_MM + _PAST_MM:
        return "past_b"
    if projected.x_mm < -_PAST_MM or projected.x_mm > TABLE_WIDTH_MM + _PAST_MM:
        return "floor"
    if not projected.inside_table:
        return "floor"
    if table_half(projected.y_mm) is None:
        return "middle"
    if projected.y_mm <= TABLE_LENGTH_MM * 0.2 or projected.y_mm >= TABLE_LENGTH_MM * 0.8:
        return "end"
    return "middle"


def _leaves(place: str, end: str) -> bool:
    return (place == "past_a" and end == "end_a") or (place == "past_b" and end == "end_b")


def _plane_y(x_px: float, y_px: float, calibration: TableCalibration) -> float:
    return calibration.project_to_table_plane(x_px, y_px).y_mm


def _end(ends: object, player: str) -> str | None:
    value = getattr(ends, player, None)
    if value in {"end_a", "end_b"}:
        return value
    return None


def _other(player: str) -> str:
    return "p2" if player == "p1" else "p1"
