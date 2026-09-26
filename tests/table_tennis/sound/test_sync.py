"""Phase 3: a bounce picks the nearest frame inside two camera periods."""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.sound.sync import nearest_frame

PERIOD_NS = 40_000_000


@dataclass(frozen=True)
class _Frame:
    capture_monotonic_ns: int
    name: str


def _row() -> list[_Frame]:
    return [
        _Frame(0, "a"),
        _Frame(PERIOD_NS, "b"),
        _Frame(2 * PERIOD_NS, "c"),
    ]


class SyncTests(unittest.TestCase):
    def test_known_spacing_picks_the_nearest_frame(self) -> None:
        frames = _row()
        self.assertEqual(nearest_frame(PERIOD_NS, frames, PERIOD_NS), frames[1])
        later = nearest_frame(PERIOD_NS + 10_000_000, frames, PERIOD_NS)
        self.assertEqual(later, frames[1])

    def test_a_bounce_outside_two_periods_picks_nothing(self) -> None:
        frames = _row()
        outside = 2 * PERIOD_NS + 2 * PERIOD_NS + 1
        self.assertIsNone(nearest_frame(outside, frames, PERIOD_NS))
        self.assertIsNone(nearest_frame(PERIOD_NS, [], PERIOD_NS))

    def test_the_window_edge_is_inside_and_a_tie_keeps_the_earlier_frame(self) -> None:
        frames = _row()
        edge = nearest_frame(2 * PERIOD_NS + 2 * PERIOD_NS, frames, PERIOD_NS)
        self.assertEqual(edge, frames[2])
        tied = [
            _Frame(0, "early"),
            _Frame(2 * PERIOD_NS, "late"),
        ]
        self.assertEqual(nearest_frame(PERIOD_NS, tied, PERIOD_NS), tied[0])


if __name__ == "__main__":
    unittest.main()
