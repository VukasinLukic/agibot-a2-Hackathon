from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("numpy")

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.contracts import MatchSnapshot
from table_tennis.core.ports import SequentialIdGenerator
from table_tennis.vision.calibration import CalibrationGate
from table_tennis.vision.events import RallyJudge
from table_tennis.vision.frame import ORIGIN_A2_FISHEYE, Frame
from table_tennis.vision.image import BgrImage
from table_tennis.vision.track import TrackSample

FIXTURE = Path(__file__).resolve().parents[3] / "table_tennis" / "vision" / "fixtures" / "missed_return.json"
RALLY = "00000000-0000-4000-8000-0000000000b1"
MATCH = "00000000-0000-4000-8000-0000000000a1"


def _calibration():
    gate = CalibrationGate(ids=SequentialIdGenerator(), margin_px=8)
    frame = Frame(1, 1_000_000, 100, 80, BgrImage(100, 80), "fisheye-left", ORIGIN_A2_FISHEYE)
    return gate.submit(frame, ((20, 16), (80, 16), (80, 64), (20, 64)), ((20, 40), (80, 40)))


def _sample(seq: int, kind: str, x: float | None, y: float | None, calibration_id: str) -> TrackSample:
    return TrackSample(
        frame_seq=seq,
        capture_monotonic_ns=seq * 1_000_000,
        detected=kind == "observed",
        x_px=x,
        y_px=y,
        observation_kind=kind,
        confidence=0.8 if kind == "observed" else 0.0,
        calibration_id=calibration_id,
    )


def _snapshot(calibration_id: str, *, p1_end: str = "end_a", p2_end: str = "end_b", rally: str | None = RALLY, proposal: str | None = None, revision: int = 4):
    return MatchSnapshot.model_validate(
        {
            "match_id": MATCH,
            "revision": revision,
            "status": "rally",
            "players": [
                {"id": "p1", "display_name": "Ana"},
                {"id": "p2", "display_name": "Marko"},
            ],
            "config": {},
            "score_by_player": {"p1": 0, "p2": 0},
            "first_server_id": "p1",
            "server_id": "p1",
            "winner_id": None,
            "assignment_version": 1,
            "court_end_by_player": {"p1": p1_end, "p2": p2_end},
            "robot_side_by_player": {"p1": "left", "p2": "right"},
            "calibration_id": calibration_id,
            "active_rally_id": rally,
            "active_proposal_id": proposal,
            "persona": "regular",
            "scoring_mode": "assisted",
            "ready": {"calibration_ready": True, "camera_ready": True},
            "updated_at": datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
        }
    )


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def __call__(self) -> str:
        self._n += 1
        return f"00000000-0000-4000-8000-{self._n:012d}"


class EventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calibration = _calibration()
        self.judge = RallyJudge(self.calibration, new_id=_Ids())

    def _feed_crossing(self) -> None:
        cal = self.calibration.calibration_id or ""
        self.judge.add(_sample(2, "observed", 50, 24, cal))
        self.judge.add(_sample(3, "predicted", 50, 56, cal))
        self.judge.add(_sample(4, "observed", 50, 56, cal))

    def test_predicted_or_missing_alone_does_not_propose(self) -> None:
        cal = self.calibration.calibration_id or ""
        self.judge.add(_sample(1, "predicted", 50, 24, cal))
        self.judge.add(_sample(2, "missing", None, None, cal))
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))

    def test_crossing_then_missing_proposes_the_other_end(self) -> None:
        cal = self.calibration.calibration_id or ""
        self._feed_crossing()
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))
        self.judge.add(_sample(5, "missing", None, None, cal))
        command = self.judge.proposal_command(_snapshot(cal))
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command["type"], "point.propose")
        self.assertEqual(command["expected_revision"], 4)
        self.assertNotIn("score", command["payload"])
        self.assertNotIn("server_id", command["payload"])
        self.assertEqual(command["payload"]["reason"], "missed_return")
        self.assertEqual(command["payload"]["winner_id"], "p1")
        self.assertEqual(command["payload"]["rally_id"], RALLY)
        self.assertEqual(command["payload"]["calibration_id"], cal)
        self.assertEqual(command["payload"]["confidence"], 0.8)
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))

    def test_same_pixels_follow_the_ends_not_image_x(self) -> None:
        cal = self.calibration.calibration_id or ""
        self._feed_crossing()
        self.judge.add(_sample(5, "missing", None, None, cal))
        command = self.judge.proposal_command(_snapshot(cal, p1_end="end_b", p2_end="end_a"))
        assert command is not None
        self.assertEqual(command["payload"]["winner_id"], "p2")

    def test_stale_calibration_and_conflict_do_not_rewrite_the_command(self) -> None:
        cal = self.calibration.calibration_id or ""
        self._feed_crossing()
        self.judge.add(_sample(5, "missing", None, None, cal))
        self.assertIsNone(self.judge.proposal_command(_snapshot("table-other-v2")))
        command = self.judge.proposal_command(_snapshot(cal))
        assert command is not None
        retry = self.judge.transport_retry()
        self.assertEqual(retry, command)
        self.judge.on_conflict(_snapshot(cal, revision=5))
        self.assertIsNone(self.judge.transport_retry())
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal, revision=5)))

    def test_valid_fixture_uses_the_contract_fields(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIn("not a calibrated probability", payload["note"])
        spec = payload["proposals"][0]
        self.assertEqual(spec["reason"], "missed_return")
        self.assertNotIn("server_id", spec)
        self.assertNotIn("score_by_player", spec)


if __name__ == "__main__":
    unittest.main()
