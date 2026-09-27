"""One point proposal per rally, built with the existing fixture command path.

A proposal is one ending of the rally: a bad serve, a ball that does not
bounce on the far half, a second bounce, or a missed return. The half comes
from the bounce. The ball has to stay missing for half a second, and not over
the middle of the table. Predicted samples by themselves are not a point.
When a sound source is attached, the proposal waits until sound concludes and
names the same winner. A contact from before this rally is ignored. The
command is ``point.propose`` from ``stub.build_proposal``. This module does
not score and does not choose the server. ``AUTOMATIC_ENABLED`` is not changed
here.

``MatchVisionProducer`` is the only live producer. Do not run it in the same
process as ``FixtureVisionProducer``.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from table_tennis.vision.calibration import TableCalibration
from table_tennis.vision.point_logic import PointFold
from table_tennis.vision.rally_events import RallyEvent, RallyEventDetector
from table_tennis.vision.track import TrackSample

_LOG = logging.getLogger(__name__)
_READY_RETRY_S = 3.0
_NOTE_REPEAT_S = 5.0


# Below this the weakest ball sighting of the rally is too unsure to name a winner;
# the robot asks the players instead (a wrong name is worse in a demo than a question).
MIN_PROPOSAL_CONFIDENCE = 0.2


class RallyJudge:
    def __init__(self, calibration: TableCalibration, *, new_id: Callable[[], str] | None = None) -> None:
        if not calibration.ready or calibration.calibration_id is None or calibration.homography is None:
            raise ValueError("rally judge needs a ready calibration")
        self._calibration = calibration
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._samples: list[TrackSample] = []
        self._rally_id: str | None = None
        self._rally_started_ns: int | None = None
        self._command: dict[str, Any] | None = None
        self._closed = False
        self._sound_required = False
        self._sound_consulted = False
        self._sound: dict[str, Any] | None = None
        self._detector = RallyEventDetector(calibration.width, calibration)
        self._events: list[RallyEvent] = []
        self._fold: PointFold | None = None
        self._fold_key: tuple[str, str | None, str | None] | None = None
        self._folded = (0, 0)
        self._quiet: str | None = None
        self._noted_at = 0.0
        self._unclear_sent = False
        self._doubtful = False

    def add(self, sample: TrackSample, rally_id: str | None = None) -> None:
        if sample.proves_bounce:
            raise ValueError("a track sample cannot prove a bounce")
        sample.as_observation()
        if rally_id is not None and rally_id != self._rally_id:
            self._begin_rally(rally_id)
        elif self._rally_id is None:
            self._rally_id = rally_id
        if self._rally_started_ns is None:
            self._rally_started_ns = sample.capture_monotonic_ns
        self._samples.append(sample)
        self._events.extend(self._detector.add(sample))

    def require_sound(self) -> None:
        """Do not propose until ``hear`` has seen this rally's sound conclusion."""
        self._sound_required = True

    def hear(self, proposal: dict[str, Any] | None) -> None:
        """Remember the sound conclusion for this rally. Sound does not send it."""
        self._sound_consulted = True
        self._sound = proposal

    def proposal_command(self, snapshot: Any) -> dict[str, Any] | None:
        """Return one validated point.propose, or nothing. A later call does not send a second decision."""
        if getattr(snapshot, "scoring_mode", "assisted") != "assisted":
            self._note("scoring mode is not assisted")
            return None
        self._follow_rally(getattr(snapshot, "active_rally_id", None))
        if self._closed or self._command is not None:
            return None
        if not _snapshot_ready(snapshot):
            self._note("camera or calibration is not ready")
            return None
        found = self._conclusion(snapshot)
        if found is None:
            return None
        winner_id, reason, start_seq, end_seq = found
        if _observed_confidence(self._samples) < MIN_PROPOSAL_CONFIDENCE:
            self._note("proposal too unsure; asking the players")
            self._doubtful = True
            return None
        if self._sound_required and not self._sound_consulted:
            self._note("sound has not concluded")
            return None
        if _sound_stale(self._sound, self._rally_started_ns):
            self._note("sound contact is from before the rally")
            return None
        if self._sound_consulted and not _sound_agrees(self._sound, winner_id, reason):
            self._note("sound does not agree")
            self._closed = True
            return None
        from table_tennis.vision.stub import FROM_CONTEXT, build_proposal, propose_command

        proposal = build_proposal(
            snapshot,
            {
                "winner_id": winner_id,
                "confidence": _observed_confidence(self._samples),
                "reason": reason,
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

    def unclear_command(self, snapshot: Any) -> dict[str, Any] | None:
        """One ``point.unclear`` per rally: the rally ended and vision cannot name a winner.

        Call after ``proposal_command`` returned nothing for the same snapshot.
        It never changes the score; the robot asks the players and the operator awards.
        """
        if self._unclear_sent or self._closed or self._command is not None or self._fold is None:
            return None
        if getattr(snapshot, "scoring_mode", "assisted") != "assisted" or not _snapshot_ready(snapshot):
            return None
        if getattr(snapshot, "status", None) != "rally" or snapshot.active_proposal_id is not None:
            return None
        if snapshot.active_rally_id is None or snapshot.active_rally_id != self._rally_id or not self._samples:
            return None
        if not self._doubtful and not self._fold.ended_without_verdict(self._samples[-1].capture_monotonic_ns):
            return None
        from table_tennis.contracts import parse_command

        command = {
            "command_id": self._new_id(),
            "expected_revision": snapshot.revision,
            "type": "point.unclear",
            "payload": {"rally_id": snapshot.active_rally_id, "reason": "rally ended without a clear point"},
        }
        parse_command(command)
        self._unclear_sent = True
        _LOG.info("rally %s ended without a clear point; asking the players", snapshot.active_rally_id)
        return command

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
        self._begin_rally(rally_id)

    def _begin_rally(self, rally_id: str | None) -> None:
        self._rally_id = rally_id
        self._rally_started_ns = None
        self._samples.clear()
        self._command = None
        self._closed = False
        self._events.clear()
        self._detector.reset()
        self._fold = None
        self._fold_key = None
        self._folded = (0, 0)
        self._quiet = None
        self._noted_at = 0.0
        self._unclear_sent = False
        self._doubtful = False
        self._clear_sound()

    def _clear_sound(self) -> None:
        self._sound_consulted = False
        self._sound = None

    def _conclusion(self, snapshot: Any) -> tuple[str, str, int, int] | None:
        if getattr(snapshot, "status", "rally") != "rally":
            self._note("match is not in a rally")
            return None
        if snapshot.active_rally_id is None or snapshot.active_proposal_id is not None:
            self._note("rally is not open")
            return None
        if snapshot.calibration_id != self._calibration.calibration_id:
            self._note("calibration does not match the snapshot")
            return None
        ends = snapshot.court_end_by_player
        key = (str(getattr(snapshot, "server_id", "")), getattr(ends, "p1", None), getattr(ends, "p2", None))
        if self._fold is None or self._fold_key != key:
            self._fold = PointFold(key[0], ends, self._calibration)
            self._fold_key = key
            self._folded = (0, 0)
        self._fold.extend(self._samples[self._folded[0] :], self._events[self._folded[1] :])
        self._folded = (len(self._samples), len(self._events))
        found = self._fold.verdict()
        if found is None and self._fold.serve_aborted():
            # Nobody presses Servis again after a ball tossed away before the serve;
            # without this the open rally stays closed to vision until the next point.
            self._fold = PointFold(key[0], ends, self._calibration)  # _folded keeps the old samples out
            self._detector.reset()
        if found is None:
            return None
        return found.winner_id, found.reason, found.start_seq, found.end_seq

    def _note(self, reason: str) -> None:
        """Log why nothing was sent. The same reason repeats every few seconds, not every frame."""
        now = time.monotonic()
        if reason == self._quiet and now - self._noted_at < _NOTE_REPEAT_S:
            return
        self._quiet = reason
        self._noted_at = now
        _LOG.info("no proposal: %s", reason)


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
        sound: Callable[[], dict[str, Any] | None] | None = None,
        *,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        if not getattr(tracker, "table_limited", False):
            raise ValueError("live vision searches only inside the calibrated table")
        self._frames = frames
        self._tracker = tracker
        self._judge = judge
        self._capture = capture
        self._sound = sound
        if sound is not None:
            judge.require_sound()
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._closed = False
        self._camera_ready = False
        self._ready_check = 0.0
        self.sent: list[dict[str, Any]] = []

    def run(
        self,
        sink: Callable[[dict[str, Any]], Any],
        context_provider: Callable[[], Any],
        on_frame: Callable[[Any, Any], None] | None = None,
    ) -> None:
        rally_id = None
        try:
            self._set_camera(sink, True, "raw fisheye receiving")
            for frame in self._frames:
                if self._closed or self._camera_lost():
                    self._set_camera(sink, False, "camera_missing")
                    return
                snapshot = context_provider()
                if snapshot is not None:
                    rally_id = getattr(snapshot, "active_rally_id", None)
                self._match_camera(sink, snapshot)
                sample = self._tracker.update(frame)
                self._judge.add(sample, rally_id)
                if on_frame is not None:
                    on_frame(frame, sample)
                if snapshot is None or getattr(snapshot, "scoring_mode", "assisted") != "assisted":
                    continue
                if self._sound is not None:
                    heard = self._sound()
                    if heard is not None:
                        self._judge.hear(heard)
                command = self._judge.proposal_command(snapshot)
                if command is None:
                    command = self._judge.unclear_command(snapshot)
                if command is None:
                    continue
                reply = sink(command)
                self.sent.append(command)
                if _is_conflict(reply):
                    self._judge.on_conflict(snapshot)
            if self._camera_lost():
                self._set_camera(sink, False, "camera_missing")
        finally:
            try:
                self._set_camera(sink, False, "vision_stopped")
            except Exception:
                pass

    def close(self) -> None:
        self._closed = True

    def _match_camera(self, sink: Callable[[dict[str, Any]], Any], snapshot: Any) -> None:
        """Send ready again when the backend still says the camera is down."""
        if snapshot is None or self._camera_lost() or not self._camera_ready:
            return
        ready = getattr(getattr(snapshot, "ready", None), "camera_ready", True)
        if ready:
            return
        now = time.monotonic()
        if now - self._ready_check < _READY_RETRY_S:
            return
        self._ready_check = now
        self._camera_ready = False
        self._set_camera(sink, True, "raw fisheye receiving")

    def _camera_lost(self) -> bool:
        return self._capture is not None and bool(getattr(self._capture, "camera_missing", False))

    def _set_camera(self, sink: Callable[[dict[str, Any]], Any], ready: bool, reason: str) -> None:
        if self._camera_ready == ready:
            return
        from table_tennis.contracts import parse_command

        command = {
            "command_id": self._new_id(),
            "expected_revision": None,
            "type": "camera.ready.set",
            "payload": {"ready": ready, "reason": reason},
        }
        parse_command(command)
        self._ready_check = time.monotonic()
        sink(command)
        self.sent.append(command)
        self._camera_ready = ready


def _snapshot_ready(snapshot: Any) -> bool:
    ready = getattr(snapshot, "ready", None)
    return bool(getattr(ready, "camera_ready", False) and getattr(ready, "calibration_ready", False))


def _observed_confidence(samples: list[TrackSample]) -> float:
    values = [sample.confidence for sample in samples if sample.observation_kind == "observed"]
    if not values:
        return 0.0
    return min(max(min(values), 0.0), 1.0)


def _sound_stale(proposal: dict[str, Any] | None, rally_started_ns: int | None) -> bool:
    """A conclusion whose last contact is from before this rally does not count."""
    if not isinstance(proposal, dict) or rally_started_ns is None:
        return False
    contact = proposal.get("last_contact_ns")
    return type(contact) is int and contact < rally_started_ns


def _sound_agrees(proposal: dict[str, Any] | None, winner_id: str, reason: str) -> bool:
    if not isinstance(proposal, dict):
        return False
    return proposal.get("winner_id") == winner_id and proposal.get("reason") == reason


def _is_conflict(reply: Any) -> bool:
    if isinstance(reply, dict):
        return reply.get("status") == 409
    return getattr(reply, "status", None) == 409


def _other_player(ends: Any, receiver_end: str) -> str:
    if ends.p1 == receiver_end:
        return "p2"
    if ends.p2 == receiver_end:
        return "p1"
    raise ValueError("receiver end is not assigned to a player")
