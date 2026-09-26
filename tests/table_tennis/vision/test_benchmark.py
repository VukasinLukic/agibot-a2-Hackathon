from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision.benchmark import AUTOMATIC_ENABLED, RallyLabel, evaluate
from table_tennis.vision.capture import CaptureStats
from table_tennis.vision.events import RallyJudge
from test_events import RALLY, _calibration, _sample, _snapshot


def _label(rally_id: str, split: str = "final", winner: str | None = "p1", **kwargs: object) -> RallyLabel:
    tags = kwargs.get("tags", ())
    return RallyLabel(
        rally_id=rally_id,
        split=split,
        winner_id=winner,
        reason="missed_return",
        uncertain=bool(kwargs.get("uncertain", False)),
        simple=bool(kwargs.get("simple", True)),
        tags=tuple(tags) if isinstance(tags, tuple) else (),
        start_ns=int(kwargs.get("start_ns", 0)),
        end_ns=int(kwargs.get("end_ns", 100_000_000)),
    )


def _hit(winner: str = "p1") -> dict[str, object]:
    return {"winner_id": winner, "reason": "missed_return"}


class BenchmarkTests(unittest.TestCase):
    def test_tune_and_final_stay_apart_and_the_flag_stays_off(self) -> None:
        labels = [
            _label("tune-1", split="tune", winner="p2"),
            _label("final-1", winner="p1", start_ns=0, end_ns=80_000_000),
            _label("final-2", winner="p2", simple=False, start_ns=0, end_ns=200_000_000),
        ]
        report = evaluate(
            labels,
            {"tune-1": [_hit("p1")], "final-1": [_hit("p1")], "final-2": []},
            capture=CaptureStats(frames_dropped=3),
            period_ns=50_000_000,
        )
        self.assertEqual(report.precision, 1.0)
        self.assertEqual(report.simple_coverage, 1.0)
        self.assertEqual(report.abstention, 0.5)
        self.assertEqual(report.wrong_player, 0)
        self.assertEqual(report.dropped_frames, 3)
        self.assertEqual(report.capture_fps, 20.0)
        self.assertEqual(report.latency_ms_p50, 80.0)
        self.assertFalse(report.meets_demo_bar)
        self.assertFalse(report.automatic_enabled)
        self.assertFalse(AUTOMATIC_ENABLED)
        with self.assertRaises(ValueError):
            evaluate([_label("same", split="tune"), _label("same", split="final")], {})

    def test_wrong_player_and_a_duplicate_are_not_precise(self) -> None:
        labels = [_label("r1", winner="p2"), _label("r2")]
        report = evaluate(
            labels,
            {"r1": [_hit("p1")], "r2": [_hit("p1"), _hit("p1")]},
        )
        self.assertEqual(report.wrong_player, 1)
        self.assertEqual(report.duplicate_proposals, 1)
        self.assertEqual(report.precision, 0.0)

    def test_synthetic_fifty_does_not_open_automatic_mode(self) -> None:
        labels = [_label(f"r{index}", tags=("net",) if index == 0 else ()) for index in range(50)]
        decisions = {label.rally_id: [_hit()] for label in labels}
        report = evaluate(labels, decisions, source="synthetic")
        self.assertEqual(report.final_rallies, 50)
        self.assertEqual(report.precision, 1.0)
        self.assertFalse(report.meets_demo_bar)
        self.assertFalse(report.automatic_enabled)

    def test_a2_bar_still_leaves_the_flag_off(self) -> None:
        tags = ("net", "occlusion", "fast_ball", "stop")
        labels = [
            _label(f"r{index}", tags=tags if index == 0 else ())
            for index in range(50)
        ]
        decisions = {label.rally_id: [_hit()] for label in labels}
        report = evaluate(labels, decisions, source="a2")
        self.assertTrue(report.meets_demo_bar)
        self.assertFalse(report.automatic_enabled)
        short = evaluate(labels[:49], {label.rally_id: [_hit()] for label in labels[:49]}, source="a2")
        self.assertFalse(short.meets_demo_bar)

    def test_judge_output_is_what_the_benchmark_counts(self) -> None:
        calibration = _calibration()
        cal = calibration.calibration_id or ""
        judge = RallyJudge(calibration)
        judge.add(_sample(2, "observed", 50, 24, cal))
        judge.add(_sample(4, "observed", 50, 56, cal))
        judge.add(_sample(5, "missing", None, None, cal))
        command = judge.proposal_command(_snapshot(cal))
        self.assertIsNotNone(command)
        report = evaluate([_label(RALLY)], {RALLY: [command]})
        self.assertEqual(report.precision, 1.0)
        self.assertEqual(report.wrong_player, 0)


if __name__ == "__main__":
    unittest.main()
