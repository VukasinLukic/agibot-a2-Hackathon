"""Phase 5: two far-half bounces can name a missed return. Nothing is sent."""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.sound.sequence import missed_return_proposal
from table_tennis.vision.events import _other_player

PERIOD_NS = 40_000_000
RACKET_NS = 0
FIRST_NS = 200_000_000
SECOND_NS = 400_000_000
QUIET_NS = SECOND_NS + 1_500_000_000 + 1
END_A_MM = 200.0
END_B_MM = 2_000.0


@dataclass(frozen=True)
class _Ends:
    p1: str
    p2: str


@dataclass(frozen=True)
class _Sight:
    capture_monotonic_ns: int
    observation_kind: str
    frame_seq: int
    y_mm: float | None = None
    x_px: float | None = None
    y_px: float | None = None


class _Plane:
    def __init__(self, y_mm: float, inside: bool = True) -> None:
        self.y_mm = y_mm
        self.inside_table = inside

    def project_to_table_plane(self, x_px: float, y_px: float) -> _Plane:
        del x_px, y_px
        return self


def _contacts() -> list[tuple[int, str]]:
    return [(RACKET_NS, "racket"), (FIRST_NS, "table"), (SECOND_NS, "table")]


def _sights() -> list[_Sight]:
    return [
        _Sight(50_000_000, "observed", 10, END_A_MM),
        _Sight(FIRST_NS, "observed", 20, END_B_MM),
        _Sight(SECOND_NS, "observed", 30, END_B_MM),
    ]


class SequenceTests(unittest.TestCase):
    def test_two_far_bounces_name_the_player_off_that_end(self) -> None:
        ends = _Ends("end_a", "end_b")
        proposal = missed_return_proposal(
            _contacts(),
            _sights(),
            ends,
            quiet_until_ns=QUIET_NS,
            period_ns=PERIOD_NS,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        fixture = json.loads(
            (REPO_ROOT / "table_tennis" / "vision" / "fixtures" / "missed_return.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(proposal), set(fixture["proposals"][0]))
        self.assertEqual(proposal["reason"], "missed_return")
        self.assertEqual(proposal["winner_id"], "p1")
        self.assertEqual(proposal["winner_id"], _other_player(ends, "end_b"))
        self.assertEqual(proposal["capture_start_seq"], 10)
        self.assertEqual(proposal["capture_end_seq"], 30)
        swapped = _Ends("end_b", "end_a")
        other = missed_return_proposal(
            _contacts(),
            _sights(),
            swapped,
            quiet_until_ns=QUIET_NS,
            period_ns=PERIOD_NS,
        )
        assert other is not None
        self.assertEqual(other["winner_id"], "p2")
        self.assertEqual(other["winner_id"], _other_player(swapped, "end_b"))

    def test_predicted_own_half_and_short_silence_stay_quiet(self) -> None:
        ends = _Ends("end_a", "end_b")
        predicted = _sights()
        predicted[1] = _Sight(FIRST_NS, "predicted", 20, END_B_MM)
        self.assertIsNone(
            missed_return_proposal(
                _contacts(),
                predicted,
                ends,
                quiet_until_ns=QUIET_NS,
                period_ns=PERIOD_NS,
            )
        )
        own = [
            _Sight(50_000_000, "observed", 10, END_B_MM),
            _Sight(FIRST_NS, "observed", 20, END_B_MM),
            _Sight(SECOND_NS, "observed", 30, END_B_MM),
        ]
        self.assertIsNone(
            missed_return_proposal(_contacts(), own, ends, quiet_until_ns=QUIET_NS, period_ns=PERIOD_NS)
        )
        self.assertIsNone(
            missed_return_proposal(
                _contacts(),
                _sights(),
                ends,
                quiet_until_ns=SECOND_NS + 1_500_000_000,
                period_ns=PERIOD_NS,
            )
        )
        unclear = [(RACKET_NS, "racket"), (FIRST_NS, "abstain"), (SECOND_NS, "table")]
        self.assertIsNone(
            missed_return_proposal(unclear, _sights(), ends, quiet_until_ns=QUIET_NS, period_ns=PERIOD_NS)
        )

    def test_homography_supplies_the_half_when_the_sight_has_no_plane_y(self) -> None:
        ends = _Ends("end_a", "end_b")
        sights = [
            _Sight(50_000_000, "observed", 10, x_px=1, y_px=1),
            _Sight(FIRST_NS, "observed", 20, x_px=2, y_px=2),
            _Sight(SECOND_NS, "observed", 30, x_px=3, y_px=3),
        ]
        calls = {"n": 0}

        class _Switch:
            def project_to_table_plane(self, x_px: float, y_px: float) -> _Plane:
                del y_px
                calls["n"] += 1
                if x_px == 1:
                    return _Plane(END_A_MM)
                return _Plane(END_B_MM)

        proposal = missed_return_proposal(
            _contacts(),
            sights,
            ends,
            quiet_until_ns=QUIET_NS,
            period_ns=PERIOD_NS,
            calibration=_Switch(),
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal["winner_id"], "p1")
        self.assertGreaterEqual(calls["n"], 3)


if __name__ == "__main__":
    unittest.main()
