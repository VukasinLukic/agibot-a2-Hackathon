"""Vision asks the players only after real play, once per rally."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pytest

pytest.importorskip("numpy")

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision.events import RallyJudge
from table_tennis.vision.track import TrackSample

from tests.table_tennis.vision.test_events import RALLY, _at, _calibration, _Ids, _snapshot

OTHER_RALLY = "00000000-0000-4000-8000-0000000000b2"
_S = 1_000_000_000


def _gone(seq: int, after: TrackSample, seconds: float, cal: str) -> TrackSample:
    return TrackSample(seq, after.capture_monotonic_ns + int(seconds * _S), False, None, None, "missing", 0.0, cal)


def _judge(samples: list[TrackSample]) -> tuple[RallyJudge, object]:
    calibration = _calibration()
    judge = RallyJudge(calibration, new_id=_Ids())
    snapshot = _snapshot(calibration.calibration_id)
    for sample in samples:
        judge.add(sample, RALLY)
    return judge, snapshot


class UnclearTests(unittest.TestCase):
    def _crossed(self, cal: str) -> list[TrackSample]:
        # End A is the top of the test table, the net is y = 40. No bounce, no hit.
        return [_at(1, 50, 30, cal), _at(2, 50, 34, cal), _at(3, 50, 46, cal), _at(4, 50, 50, cal)]

    def test_ball_crossed_the_net_then_gone_three_seconds_asks_once(self) -> None:
        cal = _calibration().calibration_id
        played = self._crossed(cal)
        judge, snapshot = _judge([*played, _gone(5, played[-1], 0.6, cal)])
        self.assertIsNone(judge.proposal_command(snapshot))
        self.assertIsNone(judge.unclear_command(snapshot), "0.6 s without the ball is not the end yet")

        judge.add(_gone(6, played[-1], 3.1, cal), RALLY)
        self.assertIsNone(judge.proposal_command(snapshot))
        command = judge.unclear_command(snapshot)
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command["type"], "point.unclear")
        self.assertEqual(command["payload"]["rally_id"], RALLY)
        self.assertEqual(command["expected_revision"], snapshot.revision)
        self.assertIsNone(judge.unclear_command(snapshot), "one question per rally")

    def test_serve_preparation_is_not_a_question(self) -> None:
        cal = _calibration().calibration_id
        held = [_at(1, 50, 24, cal), _at(2, 50, 26, cal), _at(3, 50, 28, cal)]
        judge, snapshot = _judge([*held, _gone(4, held[-1], 4.0, cal)])
        self.assertIsNone(judge.proposal_command(snapshot))
        self.assertIsNone(judge.unclear_command(snapshot))

    def test_bounce_and_long_flight_on_one_half_asks(self) -> None:
        # The far half is lost against the windows: no crossing, but a bounce and real travel.
        cal = _calibration().calibration_id
        seen = [_at(1, 50, 18, cal), _at(2, 50, 24, cal), _at(3, 50, 32, cal), _at(4, 50, 30, cal), _at(5, 50, 26, cal)]
        judge, snapshot = _judge([*seen, _gone(6, seen[-1], 3.1, cal)])
        self.assertIsNone(judge.proposal_command(snapshot))
        self.assertEqual(judge.unclear_command(snapshot)["type"], "point.unclear")

    def test_ball_tossed_away_before_the_serve_does_not_blind_the_rally(self) -> None:
        cal = _calibration().calibration_id
        judge, snapshot = _judge([])
        tossed = [_at(1, 50, 22, cal), _at(2, 50, 18, cal), _at(3, 50, 12, cal)]
        for sample in [*tossed, _gone(4, tossed[-1], 0.6, cal)]:
            judge.add(sample, RALLY)
            self.assertIsNone(judge.proposal_command(snapshot))
        base = 10 * _S
        seen = [TrackSample(s, base + s * 33_333_333, True, 50, y, "observed", 0.8, cal)
                for s, y in ((11, 18), (12, 24), (13, 32), (14, 30), (15, 26))]
        for sample in [*seen, _gone(16, seen[-1], 3.1, cal)]:
            judge.add(sample, RALLY)
            judge.proposal_command(snapshot)
        self.assertEqual(judge.unclear_command(snapshot)["type"], "point.unclear")

    def test_unsure_point_becomes_a_question(self) -> None:
        import dataclasses

        from tests.table_tennis.vision.test_events import _crossing

        cal = _calibration().calibration_id
        weak = [dataclasses.replace(s, confidence=0.05) if s.observation_kind == "observed" else s for s in _crossing(cal)]
        judge, snapshot = _judge(weak)
        self.assertIsNone(judge.proposal_command(snapshot), "0.05 is too unsure to name a winner")
        self.assertEqual(judge.unclear_command(snapshot)["type"], "point.unclear")
        self.assertIsNone(judge.unclear_command(snapshot), "one question per rally")

    def test_manual_scoring_and_other_rally_stay_silent(self) -> None:
        cal = _calibration().calibration_id
        played = self._crossed(cal)
        judge, snapshot = _judge([*played, _gone(5, played[-1], 3.1, cal)])
        manual = snapshot.model_copy(update={"scoring_mode": "manual"})
        self.assertIsNone(judge.unclear_command(manual))
        judge.proposal_command(snapshot)
        moved = snapshot.model_copy(update={"active_rally_id": OTHER_RALLY})
        self.assertIsNone(judge.unclear_command(moved))


if __name__ == "__main__":
    unittest.main()
