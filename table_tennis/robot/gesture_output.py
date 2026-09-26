"""Point and greeting gestures for the A2 referee.

The backend already emits ``point.confirmed`` and ``match.finished``. This
session turns those into one short catalog gesture. ``accepted`` means the
gesture is queued. ``completed`` means playback finished. A name in the
catalog is not a firmware id; those are checked on the robot.

``winner_id`` is resolved through the snapshot's ``robot_side_by_player`` at
playback, so a side swap is honored. Handshake is not a grasp; the greeting
is ``wave``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from table_tennis.contracts import MatchSnapshot

# Hints the existing catalog resolves by English name. Numeric ids stay on the robot.
GESTURE_HINTS = {
    "point left": "Direction_point to the left",
    "point right": "Direction_point to the right",
    "wave": "Wave hand_right hand",
    "nod thanks": "Nod head",
}

_BLOCKED_STATUS = frozenset({"rally", "pending_decision"})
_MOVING = frozenset({"requested", "validating", "moving", "arrived", "cancel_requested"})
UNPLAYED = frozenset({"navigating", "active_rally"})


class GestureNotPlayed(RuntimeError):
    """The body did not play this gesture. The match score is unchanged."""


@dataclass
class GestureAck:
    match_id: str
    event_id: str
    name: str
    accepted: bool
    completed: bool
    reason: Optional[str] = None
    winner_id: Optional[str] = None
    robot_side: Optional[str] = None


@dataclass
class GestureJob:
    match_id: str
    event_id: str
    revision: int
    name: str
    winner_id: Optional[str]
    robot_side: Optional[str]
    assignment_version: int
    accepted_at: float
    started: bool = False
    score: tuple[int, int] = (0, 0)


class MotionCoordinator:
    """One body: walking suppresses gestures; arrival waves once."""

    def __init__(self) -> None:
        self.session: Optional[GestureSession] = None

    def bind(self, session: GestureSession) -> None:
        self.session = session

    def note_navigation(self, active: bool) -> None:
        if self.session is not None:
            self.session.set_navigating(active)

    def note_ready(self, match_id: Optional[str]) -> None:
        self.note_navigation(False)
        if self.session is not None and match_id:
            self.session.greet(match_id)

    def note_call_state(self, state: str, match_id: Optional[str]) -> None:
        if state in _MOVING:
            self.note_navigation(True)
        elif state == "ready":
            self.note_ready(match_id)
        elif state in {"failed", "cancelled", "busy"}:
            self.note_navigation(False)


class GestureSession:
    def __init__(
        self,
        play: Callable[[GestureJob], None],
        neutral: Callable[[], None],
        *,
        ttl_s: float = 20.0,
        now: Optional[Callable[[], float]] = None,
    ):
        self._play = play
        self._neutral = neutral
        self.ttl_s = ttl_s
        self._now = now or time.monotonic
        self.navigating = False
        self.neutral_releases = 0
        self.acks: list[GestureAck] = []
        self._queue: list[GestureJob] = []
        self._seen: set[tuple[str, str]] = set()
        self._greeted: set[str] = set()
        self._snapshot: Optional[MatchSnapshot] = None
        self._playing = False

    def set_navigating(self, active: bool) -> None:
        self.navigating = active

    def note_snapshot(self, snapshot: MatchSnapshot) -> None:
        self._snapshot = snapshot

    def accept(self, event, snapshot: MatchSnapshot) -> GestureAck:
        self._snapshot = snapshot
        name, winner, side = _resolve(event, snapshot)
        if name is None:
            return self._ack(event, "", False, False, "ignored", winner, side)
        if snapshot.status in _BLOCKED_STATUS:
            return self._ack(event, name, False, False, "active_rally", winner, side)
        if self.navigating:
            return self._ack(event, name, False, False, "navigating", winner, side)
        key = (event.event_id, name)
        if key in self._seen:
            return self._ack(event, name, True, False, "duplicate", winner, side)
        self._drop_older(event.match_id, event.revision)
        self._seen.add(key)
        score = snapshot.score_by_player
        self._queue.append(
            GestureJob(
                match_id=event.match_id,
                event_id=event.event_id,
                revision=event.revision or 0,
                name=name,
                winner_id=winner,
                robot_side=side,
                assignment_version=snapshot.assignment_version,
                accepted_at=self._now(),
                score=(score.p1, score.p2),
            )
        )
        return self._ack(event, name, True, False, "queued", winner, side)

    def cancel_pending(self, match_id: str) -> int:
        """Undo drops gestures that have not started. A started one is left alone."""
        kept: list[GestureJob] = []
        dropped = 0
        for job in self._queue:
            if job.match_id == match_id and not job.started:
                dropped += 1
                self.acks.append(
                    GestureAck(job.match_id, job.event_id, job.name, True, False, "cancelled", job.winner_id, job.robot_side)
                )
            else:
                kept.append(job)
        self._queue = kept
        return dropped

    def pump(self) -> Optional[GestureAck]:
        if self.navigating or self._playing or not self._queue:
            return None
        chosen = self._queue[-1]
        pending = self._queue
        self._queue = []
        for job in pending:
            if job is chosen or job.started:
                continue
            if job.match_id == chosen.match_id:
                self.acks.append(
                    GestureAck(job.match_id, job.event_id, job.name, True, False, "stale_before_playback", job.winner_id, job.robot_side)
                )
            else:
                self._queue.append(job)
        self._retarget(chosen)
        if self._expired(chosen):
            return self._ack_job(chosen, True, False, "expired")
        snap = self._snapshot
        if snap is not None and snap.status in _BLOCKED_STATUS and snap.revision > chosen.revision:
            return self._ack_job(chosen, True, False, "active_rally")
        chosen.started = True
        self._playing = True
        try:
            self._play(chosen)
        except Exception:
            self._playing = False
            self._ack_job(chosen, True, False, "playback_failed")
            raise
        self._playing = False
        self._neutral()
        self.neutral_releases += 1
        return self._ack_job(chosen, True, True, None)

    def present(self, event, snapshot: MatchSnapshot) -> GestureAck:
        ack = self.accept(event, snapshot)
        if not ack.accepted or ack.reason == "duplicate":
            return ack
        played = self.pump()
        return played if played is not None else ack

    def greet(self, match_id: str) -> GestureAck:
        """One wave after arrival. A second call does not wave again."""
        if match_id in self._greeted:
            return GestureAck(match_id, "greeting", "wave", True, False, "duplicate")
        if self.navigating:
            return GestureAck(match_id, "greeting", "wave", False, False, "navigating")
        self._greeted.add(match_id)
        job = GestureJob(
            match_id=match_id,
            event_id="greeting",
            revision=0,
            name="wave",
            winner_id=None,
            robot_side=None,
            assignment_version=0,
            accepted_at=self._now(),
            started=True,
        )
        self._play(job)
        self._neutral()
        self.neutral_releases += 1
        ack = GestureAck(match_id, "greeting", "wave", True, True, None)
        self.acks.append(ack)
        return ack

    def _drop_older(self, match_id: str, revision: int) -> None:
        kept: list[GestureJob] = []
        for job in self._queue:
            if job.match_id == match_id and not job.started and job.revision < revision:
                self.acks.append(
                    GestureAck(job.match_id, job.event_id, job.name, True, False, "stale_before_playback", job.winner_id, job.robot_side)
                )
            else:
                kept.append(job)
        self._queue = kept

    def _retarget(self, job: GestureJob) -> None:
        snap = self._snapshot
        if snap is None or not job.winner_id or not job.name.startswith("point"):
            return
        side = getattr(snap.robot_side_by_player, job.winner_id)
        job.robot_side = side
        job.name = f"point {side}"
        job.assignment_version = snap.assignment_version

    def _expired(self, job: GestureJob) -> bool:
        return (self._now() - job.accepted_at) > self.ttl_s

    def _ack(self, event, name: str, accepted: bool, completed: bool, reason: Optional[str], winner, side) -> GestureAck:
        ack = GestureAck(
            match_id=getattr(event, "match_id", "") or "",
            event_id=getattr(event, "event_id", "") or "",
            name=name,
            accepted=accepted,
            completed=completed,
            reason=reason,
            winner_id=winner,
            robot_side=side,
        )
        self.acks.append(ack)
        return ack

    def _ack_job(self, job: GestureJob, accepted: bool, completed: bool, reason: Optional[str]) -> GestureAck:
        ack = GestureAck(job.match_id, job.event_id, job.name, accepted, completed, reason, job.winner_id, job.robot_side)
        self.acks.append(ack)
        return ack


def _resolve(event, snapshot: MatchSnapshot) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if event.type == "point.confirmed":
        winner = event.payload.winner_id
        side = getattr(snapshot.robot_side_by_player, winner)
        return f"point {side}", winner, side
    if event.type == "match.finished":
        winner = getattr(event.payload, "winner_id", None)
        side = getattr(snapshot.robot_side_by_player, winner) if winner else None
        return "nod thanks", winner, side
    return None, None, None
