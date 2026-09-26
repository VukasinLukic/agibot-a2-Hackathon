from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pytest

pytest.importorskip("numpy")

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.core.ports import SequentialIdGenerator
from table_tennis.vision.calibration import CalibrationGate
from table_tennis.vision.frame import ORIGIN_A2_FISHEYE, Frame
from table_tennis.vision.image import BgrImage
from table_tennis.vision.rally_events import RallyEventDetector
from table_tennis.vision.track import TrackSample

PERIOD_NS = 33_333_333


def _calibration():
    # end_a along y=16, end_b along y=64, net at y=40.
    gate = CalibrationGate(ids=SequentialIdGenerator(), margin_px=8)
    frame = Frame(1, 1_000_000, 100, 80, BgrImage(100, 80), "fisheye-left", ORIGIN_A2_FISHEYE)
    return gate.submit(frame, ((20, 16), (80, 16), (80, 64), (20, 64)), ((20, 40), (80, 40)))


def _obs(seq: int, x: float, y: float) -> TrackSample:
    return TrackSample(seq, seq * PERIOD_NS, True, x, y, "observed", 0.9, None)


def _missing(seq: int) -> TrackSample:
    return TrackSample(seq, seq * PERIOD_NS, False, None, None, "missing", 0.0, None)


def _feed(detector: RallyEventDetector, samples: list[TrackSample]) -> list:
    events = []
    for sample in samples:
        events.extend(detector.add(sample))
    return events


class RallyEventTests(unittest.TestCase):
    def test_table_bounce_names_the_half(self) -> None:
        detector = RallyEventDetector(100, _calibration())
        events = _feed(detector, [_obs(1, 40, 50), _obs(2, 44, 58), _obs(3, 48, 52)])
        self.assertEqual([(e.kind, e.frame_seq, e.side) for e in events], [("bounce", 2, "end_b")])

    def test_bounce_off_the_table_is_dropped(self) -> None:
        detector = RallyEventDetector(100, _calibration())
        self.assertEqual(_feed(detector, [_obs(1, 92, 70), _obs(2, 94, 78), _obs(3, 96, 72)]), [])

    def test_without_calibration_the_side_is_unknown(self) -> None:
        events = _feed(RallyEventDetector(100), [_obs(1, 40, 20), _obs(2, 44, 28), _obs(3, 48, 22)])
        self.assertEqual([(e.kind, e.side) for e in events], [("bounce", None)])

    def test_x_reversal_is_a_hit(self) -> None:
        events = _feed(RallyEventDetector(100), [_obs(1, 50, 30), _obs(2, 58, 28), _obs(3, 51, 27)])
        self.assertEqual([(e.kind, e.frame_seq) for e in events], [("hit", 2)])

    def test_apex_is_not_a_bounce(self) -> None:
        events = _feed(RallyEventDetector(100), [_obs(1, 40, 30), _obs(2, 44, 26), _obs(3, 48, 30)])
        self.assertEqual(events, [])

    def test_missing_sample_breaks_the_chain(self) -> None:
        detector = RallyEventDetector(100, _calibration())
        samples = [_obs(1, 40, 50), _obs(2, 44, 58), _missing(3), _obs(4, 48, 52)]
        self.assertEqual(_feed(detector, samples), [])

    def test_slow_wobble_is_ignored(self) -> None:
        events = _feed(RallyEventDetector(960), [_obs(1, 400, 300), _obs(2, 401, 302), _obs(3, 400, 300)])
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
