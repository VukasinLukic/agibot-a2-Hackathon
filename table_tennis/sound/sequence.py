"""Offline bounce order. One missed return, or nothing. No HTTP.

A proposal needs a racket, then two table contacts on the far half, then
silence longer than 1.5 s. The ball must also have been observed on the near
half before that first bounce. Predicted positions do not count. The winner
is the player who is not standing on the receiving end, the same split as
``vision/events.py``. ``service_fault`` is not returned from here.
"""

from __future__ import annotations

from typing import Sequence

from table_tennis.sound.sync import nearest_frame
from table_tennis.vision.calibration import TABLE_LENGTH_MM

_KINDS = frozenset({"table", "racket", "floor", "abstain"})
_SILENCE_NS = 1_500_000_000
_NET_BAND = 0.08
_CONFIDENCE = 0.8


def missed_return_proposal(
    contacts: Sequence[tuple[int, str]],
    sights: Sequence[object],
    ends: object,
    *,
    quiet_until_ns: int,
    period_ns: int,
    calibration: object | None = None,
) -> dict[str, object] | None:
    """Return one ``missed_return`` proposal, or nothing. This does not send it."""
    _require_ns(quiet_until_ns, "quiet_until_ns")
    if type(period_ns) is not int or isinstance(period_ns, bool) or period_ns <= 0:
        raise ValueError("period_ns must be a positive int")
    ordered = _contacts(contacts)
    if [kind for _, kind in ordered] != ["racket", "table", "table"]:
        return None
    if quiet_until_ns - ordered[-1][0] <= _SILENCE_NS:
        return None
    first = _bounce(sights, ordered[1][0], period_ns, calibration)
    second = _bounce(sights, ordered[2][0], period_ns, calibration)
    if first is None or second is None:
        return None
    first_half, _first_seq = first
    second_half, end_seq = second
    approach = _approach(sights, ordered[1][0], first_half, calibration)
    if approach is None or first_half != second_half:
        return None
    _approach_half, start_seq = approach
    return {
        "winner_id": _other_player(ends, first_half),
        "confidence": _CONFIDENCE,
        "reason": "missed_return",
        "capture_start_seq": start_seq,
        "capture_end_seq": end_seq,
        "rally_id": "from_context",
        "calibration_id": "from_context",
        "assignment_version": "from_context",
    }


def _contacts(contacts: Sequence[tuple[int, str]]) -> list[tuple[int, str]]:
    ordered: list[tuple[int, str]] = []
    previous = -1
    for time_ns, kind in contacts:
        _require_ns(time_ns, "contact time_ns")
        if kind not in _KINDS:
            raise ValueError("contact kind must be table, racket, floor, or abstain")
        if time_ns <= previous:
            raise ValueError("contact times must increase")
        previous = time_ns
        ordered.append((time_ns, kind))
    return ordered


def _require_ns(value: int, name: str) -> None:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative int")


def _bounce(
    sights: Sequence[object],
    bounce_ns: int,
    period_ns: int,
    calibration: object | None,
) -> tuple[str, int] | None:
    sight = nearest_frame(bounce_ns, sights, period_ns)
    if sight is None or getattr(sight, "observation_kind", None) != "observed":
        return None
    half = _half(_plane_y(sight, calibration))
    frame_seq = getattr(sight, "frame_seq", None)
    if half is None or type(frame_seq) is not int or isinstance(frame_seq, bool):
        return None
    return half, frame_seq


def _approach(
    sights: Sequence[object],
    bounce_ns: int,
    receiver_half: str,
    calibration: object | None,
) -> tuple[str, int] | None:
    best: tuple[str, int] | None = None
    best_stamp: int | None = None
    for sight in sights:
        stamp = getattr(sight, "capture_monotonic_ns", None)
        if type(stamp) is not int or isinstance(stamp, bool) or stamp < 0:
            raise ValueError("sight capture_monotonic_ns must be a non-negative int")
        if stamp >= bounce_ns or getattr(sight, "observation_kind", None) != "observed":
            continue
        half = _half(_plane_y(sight, calibration))
        frame_seq = getattr(sight, "frame_seq", None)
        if half is None or half == receiver_half or type(frame_seq) is not int or isinstance(frame_seq, bool):
            continue
        if best_stamp is None or stamp < best_stamp:
            best = (half, frame_seq)
            best_stamp = stamp
    return best


def _plane_y(sight: object, calibration: object | None) -> float | None:
    y_mm = getattr(sight, "y_mm", None)
    if isinstance(y_mm, (int, float)) and not isinstance(y_mm, bool):
        return float(y_mm)
    if calibration is None:
        return None
    x_px = getattr(sight, "x_px", None)
    y_px = getattr(sight, "y_px", None)
    if not isinstance(x_px, (int, float)) or not isinstance(y_px, (int, float)):
        return None
    if isinstance(x_px, bool) or isinstance(y_px, bool):
        return None
    project = getattr(calibration, "project_to_table_plane", None)
    if project is None:
        return None
    projected = project(float(x_px), float(y_px))
    if not getattr(projected, "inside_table", False):
        return None
    projected_y = getattr(projected, "y_mm", None)
    if not isinstance(projected_y, (int, float)) or isinstance(projected_y, bool):
        return None
    return float(projected_y)


def _half(y_mm: float | None) -> str | None:
    if y_mm is None:
        return None
    middle = TABLE_LENGTH_MM / 2.0
    band = TABLE_LENGTH_MM * _NET_BAND
    if y_mm < middle - band:
        return "end_a"
    if y_mm > middle + band:
        return "end_b"
    return None


def _other_player(ends: object, receiver_end: str) -> str:
    from table_tennis.vision.events import _other_player as vision_other

    return vision_other(ends, receiver_end)
