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
from table_tennis.vision.benchmark import AUTOMATIC_ENABLED
from table_tennis.vision.calibration import CalibrationGate
from table_tennis.vision.events import MatchVisionProducer, RallyJudge
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


def _sample(
    seq: int,
    kind: str,
    x: float | None,
    y: float | None,
    calibration_id: str,
    confidence: float | None = None,
) -> TrackSample:
    if confidence is None:
        confidence = 0.8 if kind == "observed" else 0.0
    return TrackSample(
        frame_seq=seq,
        capture_monotonic_ns=seq * 1_000_000,
        detected=kind == "observed",
        x_px=x,
        y_px=y,
        observation_kind=kind,
        confidence=confidence,
        calibration_id=calibration_id,
    )


_PERIOD = 33_333_333
_GONE = 500_000_000


def _at(seq: int, x: float, y: float, cal: str, confidence: float = 0.8) -> TrackSample:
    return TrackSample(seq, seq * _PERIOD, True, x, y, "observed", confidence, cal)


def _crossing(cal: str) -> list[TrackSample]:
    """Legal serve (bounce on end A, then end B), then the ball leaves past end B and stays gone."""
    played = [
        *_bounce(1, 22, 30, 24, cal),
        *_bounce(4, 50, 58, 52, cal),
        _at(7, 50, 72, cal),
    ]
    played.append(
        TrackSample(8, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal)
    )
    return played


def _bounce(seq: int, y0: float, y1: float, y2: float, cal: str, confidence: float = 0.8) -> list[TrackSample]:
    return [_at(seq, 50, y0, cal, confidence), _at(seq + 1, 50, y1, cal, confidence), _at(seq + 2, 50, y2, cal, confidence)]


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


class _LostCamera:
    camera_missing = True


class _ScriptedTracker:
    table_limited = True

    def __init__(self, samples: list[TrackSample]) -> None:
        self._samples = iter(samples)

    def update(self, frame: object) -> TrackSample:
        del frame
        return next(self._samples)


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
        for sample in _crossing(cal):
            self.judge.add(sample)

    def test_predicted_or_missing_alone_does_not_propose(self) -> None:
        cal = self.calibration.calibration_id or ""
        self.judge.add(_sample(1, "predicted", 50, 24, cal))
        self.judge.add(_sample(2, "missing", None, None, cal))
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))

    def test_crossing_then_missing_proposes_the_other_end(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = _crossing(cal)
        for sample in played[:-1]:
            self.judge.add(sample)
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))
        self.judge.add(played[-1])
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
        command = self.judge.proposal_command(_snapshot(cal, p1_end="end_b", p2_end="end_a"))
        assert command is not None
        self.assertEqual(command["payload"]["winner_id"], "p2")

    def test_stale_calibration_and_conflict_do_not_rewrite_the_command(self) -> None:
        cal = self.calibration.calibration_id or ""
        self._feed_crossing()
        self.assertIsNone(self.judge.proposal_command(_snapshot("table-other-v2")))
        command = self.judge.proposal_command(_snapshot(cal))
        assert command is not None
        retry = self.judge.transport_retry()
        self.assertEqual(retry, command)
        self.judge.on_conflict(_snapshot(cal, revision=5))
        self.assertIsNone(self.judge.transport_retry())
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal, revision=5)))

    def test_a_new_rally_keeps_the_samples_added_for_it(self) -> None:
        cal = self.calibration.calibration_id or ""
        other = "00000000-0000-4000-8000-0000000000c1"
        self.judge.add(_sample(1, "observed", 50, 24, cal), rally_id=RALLY)
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))
        for sample in _crossing(cal):
            self.judge.add(sample, rally_id=other)
        command = self.judge.proposal_command(_snapshot(cal, rally=other))
        assert command is not None
        self.assertEqual(command["payload"]["rally_id"], other)
        self.assertEqual(command["payload"]["winner_id"], "p1")

    def test_manual_scoring_stays_silent(self) -> None:
        cal = self.calibration.calibration_id or ""
        self._feed_crossing()
        snapshot = _snapshot(cal)
        manual = snapshot.model_copy(update={"scoring_mode": "manual"})
        self.assertIsNone(self.judge.proposal_command(manual))

    def test_live_producer_sets_camera_ready_before_a_proposal(self) -> None:
        cal = self.calibration.calibration_id or ""
        samples = _crossing(cal)
        sent: list[dict] = []
        producer = MatchVisionProducer(
            [object()] * len(samples),
            _ScriptedTracker(samples),
            self.judge,
            new_id=_Ids(),
        )
        producer.run(sent.append, lambda: _snapshot(cal))
        self.assertEqual(sent[0]["type"], "camera.ready.set")
        self.assertTrue(sent[0]["payload"]["ready"])
        self.assertEqual(sent[1]["type"], "point.propose")
        manual = _snapshot(cal).model_copy(update={"scoring_mode": "manual"})
        quiet: list[dict] = []
        MatchVisionProducer([object()], _ScriptedTracker(samples[:1]), RallyJudge(self.calibration, new_id=_Ids())).run(
            quiet.append,
            lambda: manual,
        )
        self.assertEqual([item["type"] for item in quiet], ["camera.ready.set", "camera.ready.set"])
        self.assertFalse(quiet[1]["payload"]["ready"])
        self.assertEqual(quiet[1]["payload"]["reason"], "vision_stopped")

    def test_ready_is_not_sent_again_right_after_the_first_one(self) -> None:
        cal = self.calibration.calibration_id or ""
        samples = _crossing(cal)
        dark = _snapshot(cal).model_copy(update={"ready": {"calibration_ready": True, "camera_ready": False}})
        sent: list[dict] = []
        MatchVisionProducer(
            [object()] * len(samples),
            _ScriptedTracker(samples),
            RallyJudge(self.calibration, new_id=_Ids()),
            new_id=_Ids(),
        ).run(sent.append, lambda: dark)
        ready = [item["payload"]["ready"] for item in sent if item["type"] == "camera.ready.set"]
        self.assertEqual(ready, [True, False])

    def test_a_missing_camera_drops_the_ready_flag(self) -> None:
        sent: list[dict] = []
        MatchVisionProducer([], _ScriptedTracker([]), self.judge, capture=_LostCamera()).run(
            sent.append,
            lambda: _snapshot(self.calibration.calibration_id or ""),
        )
        self.assertEqual([item["payload"]["ready"] for item in sent], [True, False])
        self.assertEqual(sent[1]["payload"]["reason"], "camera_missing")

    def test_sound_must_agree_and_a_conflict_drops_the_proposal(self) -> None:
        self.assertFalse(AUTOMATIC_ENABLED)
        cal = self.calibration.calibration_id or ""
        self._feed_crossing()
        self.judge.hear({"winner_id": "p2", "reason": "missed_return"})
        with self.assertLogs("table_tennis.vision.events", level="INFO") as logged:
            self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))
        self.assertEqual(logged.output, ["INFO:table_tennis.vision.events:no proposal: sound does not agree"])
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))

        agreed = RallyJudge(self.calibration, new_id=_Ids())
        agreed.add(_sample(2, "observed", 50, 24, cal))
        agreed.add(_sample(4, "observed", 50, 56, cal))
        agreed.add(_sample(5, "missing", None, None, cal))
        agreed.hear(None)
        self.assertIsNone(agreed.proposal_command(_snapshot(cal)))

        sent: list[dict] = []

        def sink(command: dict) -> dict:
            sent.append(command)
            return {"status": 409}

        played = _crossing(cal)
        producer = MatchVisionProducer(
            [object()] * len(played),
            _ScriptedTracker(played),
            RallyJudge(self.calibration, new_id=_Ids()),
            sound=lambda: {"winner_id": "p1", "reason": "missed_return"},
            new_id=_Ids(),
        )
        producer.run(sink, lambda: _snapshot(cal))
        proposals = [item for item in sent if item["type"] == "point.propose"]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["payload"]["winner_id"], "p1")
        self.assertIsNone(producer._judge.transport_retry())
        dark = _snapshot(cal).model_copy(update={"ready": {"calibration_ready": True, "camera_ready": False}})
        quiet = RallyJudge(self.calibration, new_id=_Ids())
        quiet.add(_sample(2, "observed", 50, 24, cal))
        quiet.add(_sample(4, "observed", 50, 56, cal))
        quiet.add(_sample(5, "missing", None, None, cal))
        self.assertIsNone(quiet.proposal_command(dark))

    def test_a_ball_that_only_flies_over_the_far_half_is_not_a_point(self) -> None:
        cal = self.calibration.calibration_id or ""
        self.judge.add(_sample(2, "observed", 50, 24, cal))
        self.judge.add(_sample(4, "observed", 50, 56, cal))
        self.judge.add(_sample(7, "missing", None, None, cal))
        self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))

    def test_sound_that_has_not_concluded_blocks_the_proposal(self) -> None:
        cal = self.calibration.calibration_id or ""
        samples = _crossing(cal)
        sent: list[dict] = []
        with self.assertLogs("table_tennis.vision.events", level="INFO") as logged:
            MatchVisionProducer(
                [object()] * len(samples),
                _ScriptedTracker(samples),
                RallyJudge(self.calibration, new_id=_Ids()),
                sound=lambda: None,
                new_id=_Ids(),
            ).run(sent.append, lambda: _snapshot(cal))
        self.assertEqual(
            [line for line in logged.output if "sound has not concluded" in line],
            ["INFO:table_tennis.vision.events:no proposal: sound has not concluded"],
        )
        self.assertEqual(sent[0]["type"], "camera.ready.set")
        self.assertTrue(sent[0]["payload"]["ready"])
        self.assertEqual(sent[-1]["payload"]["reason"], "vision_stopped")
        self.assertFalse(any(item["type"] == "point.propose" for item in sent))

    def test_a_contact_from_before_the_rally_is_ignored(self) -> None:
        cal = self.calibration.calibration_id or ""
        self._feed_crossing()
        self.judge.hear({"winner_id": "p1", "reason": "missed_return", "last_contact_ns": 0})
        with self.assertLogs("table_tennis.vision.events", level="INFO") as logged:
            self.assertIsNone(self.judge.proposal_command(_snapshot(cal)))
        self.assertIn("sound contact is from before the rally", logged.output[0])
        self.judge.hear({"winner_id": "p1", "reason": "missed_return", "last_contact_ns": 10**12})
        command = self.judge.proposal_command(_snapshot(cal))
        assert command is not None
        self.assertEqual(command["payload"]["winner_id"], "p1")

    def test_confidence_is_the_weakest_observation(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [
            *_bounce(1, 22, 30, 24, cal, confidence=0.9),
            *_bounce(4, 50, 58, 52, cal, confidence=0.4),
            _at(7, 50, 72, cal, confidence=0.7),
        ]
        played.append(TrackSample(8, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        for sample in played:
            self.judge.add(sample)
        command = self.judge.proposal_command(_snapshot(cal))
        assert command is not None
        self.assertEqual(command["payload"]["confidence"], 0.4)

    def _play(self, samples: list[TrackSample]) -> dict | None:
        for sample in samples:
            self.judge.add(sample)
        return self.judge.proposal_command(_snapshot(self.calibration.calibration_id or ""))

    def test_a_second_bounce_on_the_same_half_is_a_double_bounce(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [
            *_bounce(1, 22, 30, 24, cal),
            *_bounce(4, 50, 58, 52, cal),
            *_bounce(8, 50, 58, 52, cal),
        ]
        command = self._play(played)
        assert command is not None
        self.assertEqual(command["payload"]["reason"], "double_bounce")
        self.assertEqual(command["payload"]["winner_id"], "p1")

    def test_a_serve_that_bounces_on_the_receiver_first_is_not_called(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [*_bounce(1, 50, 58, 52, cal), _at(4, 50, 72, cal)]
        played.append(TrackSample(5, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        self.assertIsNone(self._play(played))

    def test_a_serve_that_stays_on_the_server_side_is_a_fault(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [*_bounce(1, 22, 30, 24, cal), _at(4, 50, 8, cal)]
        played.append(TrackSample(5, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        command = self._play(played)
        assert command is not None
        self.assertEqual(command["payload"]["reason"], "service_fault")
        self.assertEqual(command["payload"]["winner_id"], "p2")

    def test_an_occlusion_does_not_cancel_the_later_point(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [*_bounce(1, 22, 30, 24, cal), *_bounce(4, 50, 58, 52, cal), _at(8, 50, 40, cal)]
        played.append(TrackSample(9, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        played.append(_at(10, 50, 72, cal))
        played.append(TrackSample(11, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        command = self._play(played)
        assert command is not None
        self.assertEqual(command["payload"]["reason"], "missed_return")
        self.assertEqual(command["payload"]["winner_id"], "p1")

    def test_bounces_after_the_ball_left_are_not_a_new_point(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [*_bounce(1, 50, 58, 52, cal), _at(4, 50, 72, cal)]
        played.append(TrackSample(5, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        played.extend(_bounce(30, 50, 58, 52, cal))
        played.extend(_bounce(34, 50, 58, 52, cal))
        self.assertIsNone(self._play(played))

    def test_a_ball_gone_near_an_end_closes_the_rally(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [*_bounce(1, 22, 30, 24, cal), *_bounce(4, 50, 58, 52, cal), _at(8, 50, 60, cal)]
        played.append(TrackSample(9, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        played.extend(_bounce(30, 50, 58, 52, cal))
        played.extend(_bounce(34, 50, 58, 52, cal))
        self.assertIsNone(self._play(played))

    def test_a_long_gap_over_the_middle_closes_the_rally(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [*_bounce(1, 22, 30, 24, cal), *_bounce(4, 50, 58, 52, cal), _at(8, 50, 40, cal)]
        played.append(TrackSample(9, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        late = played[-2].capture_monotonic_ns + 3_000_000_000
        for index, y in enumerate((50, 58, 52, 50, 58, 52)):
            played.append(TrackSample(20 + index, late + index * _PERIOD, True, 50.0, float(y), "observed", 0.8, cal))
        self.assertIsNone(self._play(played))

    def test_frame_by_frame_gives_the_same_point_as_all_at_once(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [
            *_bounce(1, 22, 30, 24, cal),
            *_bounce(4, 50, 58, 52, cal),
            _at(8, 50, 48, cal),
            _at(9, 30, 46, cal),
            _at(10, 55, 40, cal),
            _at(11, 58, 24, cal),
            _at(12, 60, 8, cal),
        ]
        played.append(TrackSample(13, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        command = None
        for sample in played:
            self.judge.add(sample)
            command = command or self.judge.proposal_command(_snapshot(cal))
        assert command is not None
        self.assertEqual(command["payload"]["reason"], "out_after_hit")
        self.assertEqual(command["payload"]["winner_id"], "p1")

    def test_a_hidden_return_is_not_an_out(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [*_bounce(1, 22, 30, 24, cal), *_bounce(4, 50, 58, 52, cal), *_bounce(8, 22, 30, 24, cal), _at(11, 50, 8, cal)]
        played.append(TrackSample(12, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        command = self._play(played)
        assert command is not None
        self.assertEqual(command["payload"]["reason"], "missed_return")
        self.assertEqual(command["payload"]["winner_id"], "p2")

    def test_a_return_that_leaves_past_the_other_end_is_out(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [
            *_bounce(1, 22, 30, 24, cal),
            *_bounce(4, 50, 58, 52, cal),
            _at(8, 50, 48, cal),
            _at(9, 30, 46, cal),
            _at(10, 55, 40, cal),
            _at(11, 58, 24, cal),
            _at(12, 60, 8, cal),
        ]
        played.append(TrackSample(13, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        command = self._play(played)
        assert command is not None
        self.assertEqual(command["payload"]["reason"], "out_after_hit")
        self.assertEqual(command["payload"]["winner_id"], "p1")

    def test_disappearing_over_the_middle_is_not_a_point(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = [*_bounce(1, 22, 30, 24, cal), *_bounce(4, 50, 58, 52, cal), _at(8, 50, 40, cal)]
        played.append(TrackSample(9, played[-1].capture_monotonic_ns + _GONE, False, None, None, "missing", 0.0, cal))
        self.assertIsNone(self._play(played))

    def test_a_short_gap_does_not_end_the_rally(self) -> None:
        cal = self.calibration.calibration_id or ""
        played = _crossing(cal)
        short = TrackSample(8, played[-2].capture_monotonic_ns + 100_000_000, False, None, None, "missing", 0.0, cal)
        self.assertIsNone(self._play(played[:-1] + [short]))

    def test_valid_fixture_uses_the_contract_fields(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIn("not a calibrated probability", payload["note"])
        spec = payload["proposals"][0]
        self.assertEqual(spec["reason"], "missed_return")
        self.assertNotIn("server_id", spec)
        self.assertNotIn("score_by_player", spec)


if __name__ == "__main__":
    unittest.main()
