"""One point proposal per rally, built with the existing fixture command path.

A proposal is only a clear missed return: the ball was seen on both halves of
the table, then the track went missing on the far half. Predicted samples and
a missing sample by themselves are not a winner. The command is
``point.propose`` from ``stub.build_proposal``. This module does not score
and does not choose the server.
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

    def add(self, sample: TrackSample) -> None:
        if sample.proves_bounce:
            raise ValueError("a track sample cannot prove a bounce")
        self._samples.append(sample)

    def proposal_command(self, snapshot: Any) -> dict[str, Any] | None:
        """Return one validated point.propose, or nothing. A later call does not send a second decision."""
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


def _other_player(ends: Any, receiver_end: str) -> str:
    if ends.p1 == receiver_end:
        return "p2"
    if ends.p2 == receiver_end:
        return "p1"
    raise ValueError("receiver end is not assigned to a player")
