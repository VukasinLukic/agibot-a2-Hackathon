"""Table size and the two ends. No camera, no image, no HTTP.

``end_a`` is the near half of the length and ``end_b`` the far half. A band
around the net belongs to neither end: a ball there has not changed sides.
"""

from __future__ import annotations

TABLE_WIDTH_MM = 1525.0
TABLE_LENGTH_MM = 2740.0
NET_BAND = 0.08


def table_half(y_mm: float | None) -> str | None:
    """``end_a``, ``end_b``, or nothing when ``y_mm`` is missing or near the net."""
    if y_mm is None:
        return None
    middle = TABLE_LENGTH_MM / 2.0
    band = TABLE_LENGTH_MM * NET_BAND
    if y_mm < middle - band:
        return "end_a"
    if y_mm > middle + band:
        return "end_b"
    return None
