"""Match a bounce time to a camera frame. This does not read the table plane."""

from __future__ import annotations

from typing import Sequence


def nearest_frame(bounce_ns: int, frames: Sequence[object], period_ns: int) -> object | None:
    """Frame whose ``capture_monotonic_ns`` is closest, within two camera periods.

    An empty result means no frame sits in that window. The table side is not
    computed here.
    """
    if type(bounce_ns) is not int or isinstance(bounce_ns, bool) or bounce_ns < 0:
        raise ValueError("bounce_ns must be a non-negative int")
    if type(period_ns) is not int or isinstance(period_ns, bool) or period_ns <= 0:
        raise ValueError("period_ns must be a positive int")
    window_ns = 2 * period_ns
    best: object | None = None
    best_distance = window_ns + 1
    best_time = 0
    for frame in frames:
        stamp = getattr(frame, "capture_monotonic_ns", None)
        if type(stamp) is not int or isinstance(stamp, bool) or stamp < 0:
            raise ValueError("frame capture_monotonic_ns must be a non-negative int")
        distance = abs(stamp - bounce_ns)
        if distance > window_ns:
            continue
        if best is None or distance < best_distance or (distance == best_distance and stamp < best_time):
            best = frame
            best_distance = distance
            best_time = stamp
    return best
