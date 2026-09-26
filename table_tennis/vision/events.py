"""One point proposal per rally, built with the existing fixture command path.

A proposal is only a clear missed return: the ball was seen on both halves of
the table, then the track went missing on the far half. Predicted samples and
a missing sample by themselves are not a winner. The command is
``point.propose`` from ``stub.build_proposal``. This module does not score
and does not choose the server.

``MatchVisionProducer`` is the only live producer. Do not run it in the same
process as ``FixtureVisionProducer``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from table_tennis.vision.calibration import TABLE_LENGTH_MM, TableCalibration
from table_tennis.vision.track import TrackSample

_NET_BAND = 0.08
_CONFIDENCE = 0.8


class RallyJudge:
    def __init__(self, calibration: TableCalibration, *, new_id: Callable[[], str] | None = None) -> None:
        if not calibration.ready or calibration.calibration_id is None or calibration.homography is None:
            raise ValueError("rally judge needs a ready calibration")
        self._calibration = calibration
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._samples: list[TrackSample] = []
        self._rally_id: str | None = None
        self._command: dict[str, Any] | None = None
        self._closed = False

    def add(self, sample: TrackSample, rally_id: str | None = None) -> None:
        if sample.proves_bounce:
            raise ValueError("a track sample cannot prove a bounce")
        sample.as_observation()
        if rally_id is not None and rally_id != self._rally_id:
            self._samples.clear()
            self._command = None
            self._closed = False
            self._rally_id = rally_id
        self._samples.append(sample)

    def proposal_command(self, snapshot: Any) -> dict[str, Any] | None:
        """Return one validated point.propose, or nothing. A later call does not send a second decision."""
        if getattr(snapshot, "scoring_mode", "assisted") != "assisted":
            return None
        self._follow_rally(getattr(snapshot, "active_rally_id", None))
        if self._closed or self._command is not None:
            return None
        found = self._missed_return(snapshot)
        if found is None:
            return None
        winner_id, start_seq, end_seq = found
        from table_tennis.vision.stub import FROM_CONTEXT, build_proposal, propose_command

        proposal = build_proposal(
            snapshot,
            {
                "winner_id": winner_id,
                "confidence": _CONFIDENCE,
                "reason": "missed_return",
                "capture_start_seq": start_seq,
                "capture_end_seq": end_seq,
                "rally_id": FROM_CONTEXT,
                "calibration_id": FROM_CONTEXT,
                "assignment_version": FROM_CONTEXT,
            },
            proposal_id=self._new_id(),
        )
        self._command = propose_command(snapshot, proposal, command_id=self._new_id())
        return self._command

    def transport_retry(self) -> dict[str, Any] | None:
        """The same command bytes, including command_id and expected_revision."""
        return self._command

    def on_conflict(self, snapshot: Any) -> None:
        """Drop the conclusion. Do not rewrite expected_revision and send it again."""
        del snapshot
        self._closed = True
        self._command = None

    def _follow_rally(self, rally_id: str | None) -> None:
        if self._rally_id is None:
            self._rally_id = rally_id
            return
        if rally_id == self._rally_id:
            return
        self._rally_id = rally_id
        self._samples.clear()
        self._command = None
        self._closed = False

    def _missed_return(self, snapshot: Any) -> tuple[str, int, int] | None:
        if snapshot.active_rally_id is None or snapshot.active_proposal_id is not None:
            return None
        if snapshot.calibration_id != self._calibration.calibration_id:
            return None
        sides: list[tuple[str, int]] = []
        last_observed: int | None = None
        missing_seq: int | None = None
        for sample in self._samples:
            if sample.observation_kind == "observed" and sample.x_px is not None and sample.y_px is not None:
                side = self._side(sample.x_px, sample.y_px)
                if side is None:
                    continue
                last_observed = sample.frame_seq
                if not sides or sides[-1][0] != side:
                    sides.append((side, sample.frame_seq))
            elif (
                sample.observation_kind == "missing"
                and last_observed is not None
                and sample.frame_seq > last_observed
            ):
                missing_seq = sample.frame_seq
        if len(sides) < 2 or missing_seq is None:
            return None
        return _other_player(snapshot.court_end_by_player, sides[-1][0]), sides[0][1], missing_seq

    def _side(self, x_px: float, y_px: float) -> str | None:
        projected = self._calibration.project_to_table_plane(x_px, y_px)
        if not projected.inside_table:
            return None
        middle = TABLE_LENGTH_MM / 2.0
        band = TABLE_LENGTH_MM * _NET_BAND
        if projected.y_mm < middle - band:
            return "end_a"
        if projected.y_mm > middle + band:
            return "end_b"
        return None


class MatchVisionProducer:
    """Live producer: camera.ready.set, then at most one point.propose per rally.

    Frames come from an iterator. When ``capture.camera_missing`` is true the
    ready flag goes false and proposals stop. Manual scoring stays silent.
    """

    def __init__(
        self,
        frames: Any,
        tracker: Any,
        judge: RallyJudge,
        capture: Any = None,
        *,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        if not getattr(tracker, "table_limited", False):
            raise ValueError("live vision searches only inside the calibrated table")
        self._frames = frames
        self._tracker = tracker
        self._judge = judge
        self._capture = capture
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._closed = False
        self._camera_ready = False
        self.sent: list[dict[str, Any]] = []

    def run(self, sink: Callable[[dict[str, Any]], Any], context_provider: Callable[[], Any]) -> None:
        self._set_camera(sink, True, "raw fisheye receiving")
        for frame in self._frames:
            if self._closed or self._camera_lost():
                self._set_camera(sink, False, "camera_missing")
                return
            snapshot = context_provider()
            sample = self._tracker.update(frame)
            self._judge.add(sample, getattr(snapshot, "active_rally_id", None))
            if getattr(snapshot, "scoring_mode", "assisted") != "assisted":
                continue
            command = self._judge.proposal_command(snapshot)
            if command is None:
                continue
            sink(command)
            self.sent.append(command)
        if self._camera_lost():
            self._set_camera(sink, False, "camera_missing")

    def close(self) -> None:
        self._closed = True

    def _camera_lost(self) -> bool:
        return self._capture is not None and bool(getattr(self._capture, "camera_missing", False))

    def _set_camera(self, sink: Callable[[dict[str, Any]], Any], ready: bool, reason: str) -> None:
        if self._camera_ready == ready and self.sent:
            return
        from table_tennis.contracts import parse_command

        command = {
            "command_id": self._new_id(),
            "expected_revision": None,
            "type": "camera.ready.set",
            "payload": {"ready": ready, "reason": reason},
        }
        parse_command(command)
        sink(command)
        self.sent.append(command)
        self._camera_ready = ready


def _other_player(ends: Any, receiver_end: str) -> str:
    if ends.p1 == receiver_end:
        return "p2"
    if ends.p2 == receiver_end:
        return "p1"
    raise ValueError("receiver end is not assigned to a player")
