"""Persistent scoreboard session for the A2 head screen.

The stock face API flashes a clip and then returns to the default face. A
referee score has to stay up until the next confirmed change, so this session
owns one reusable slot and one worker. The slot is taken when the session is
created, not when a point arrives.

``accept`` records the snapshot the match state already committed.
``shown`` becomes true only after playback. Those two are not the same event.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from table_tennis.contracts import MatchSnapshot

from .scoreboard import scoreboard_lines

# Same reusable emoticon slot screen_manip already registers once.
SLOT_ID = "emoticon_ct_message"


@dataclass(frozen=True)
class ScreenFrame:
    match_id: str
    revision: int
    primary: str
    secondary: str
    slot_id: str


@dataclass(frozen=True)
class ScreenAck:
    match_id: str
    revision: int
    accepted: bool
    shown: bool
    reason: Optional[str] = None


class ScreenSlotBusy(RuntimeError):
    """A second screen worker must not play the same slot."""


class SlotLease:
    """One reusable head-screen slot. A private lease is one simulated robot."""

    def __init__(self) -> None:
        self._owner: Optional["ScoreboardSession"] = None
        self._guard = threading.Lock()

    def acquire(self, session: "ScoreboardSession") -> None:
        with self._guard:
            if self._owner is not None and self._owner is not session:
                raise ScreenSlotBusy(f"screen slot {SLOT_ID} is already held")
            self._owner = session

    def release(self, session: "ScoreboardSession") -> None:
        with self._guard:
            if self._owner is session:
                self._owner = None


class ScoreboardSession:
    """Latest revision wins. The default face returns only on release."""

    def __init__(self, play: Callable[[ScreenFrame], None], lease: Optional[SlotLease] = None):
        self._play = play
        self._lease = lease or SlotLease()
        self.slot_id = SLOT_ID
        self.provision_count = 0
        self.face = "default"
        self.restores = 0
        self.held: Optional[MatchSnapshot] = None
        self.acks: list[ScreenAck] = []
        self._queue: list[MatchSnapshot] = []
        self._watermark: dict[str, int] = {}
        self._shown: dict[str, int] = {}
        self._playing = False
        self._released = False
        self._leased = False
        self._lock = threading.Lock()
        self._lease.acquire(self)
        self._leased = True
        self.provision_count = 1

    @property
    def leased(self) -> bool:
        return self._leased and not self._released

    def shown_revision(self, match_id: str) -> Optional[int]:
        return self._shown.get(match_id)

    def accept(self, snapshot: MatchSnapshot) -> ScreenAck:
        """Remember this snapshot. Does not mean the screen has played it."""
        with self._lock:
            if not self.leased:
                return self._ack(snapshot, accepted=False, shown=False, reason="slot_busy")
            if snapshot.status == "setup":
                self._reset_match(snapshot.match_id)
            mark = self._watermark.get(snapshot.match_id, -1)
            if snapshot.revision < mark:
                return self._ack(snapshot, accepted=False, shown=False, reason="stale")
            if snapshot.revision == self._shown.get(snapshot.match_id):
                return self._ack(snapshot, accepted=True, shown=False, reason="duplicate")
            self._watermark[snapshot.match_id] = snapshot.revision
            self._drop_older(snapshot.match_id, snapshot.revision)
            self._queue.append(snapshot)
            return self._ack(snapshot, accepted=True, shown=False, reason="queued")

    def drop_pending(self, match_id: str) -> int:
        """Undo: throw away frames that have not played yet."""
        with self._lock:
            return self._drop_older(match_id, None)

    def pump(self) -> Optional[ScreenAck]:
        """Play the newest queued frame. Older ones for that match are discarded first."""
        with self._lock:
            if not self.leased or self._playing or not self._queue:
                return None
            chosen = self._queue[-1]
            pending = self._queue
            self._queue = []
            for item in pending:
                if item is chosen:
                    continue
                if item.match_id == chosen.match_id or item.revision < self._watermark.get(item.match_id, -1):
                    self._ack(item, accepted=True, shown=False, reason="stale_before_playback")
                else:
                    self._queue.append(item)
            if chosen.revision < self._watermark.get(chosen.match_id, -1):
                return self._ack(chosen, accepted=True, shown=False, reason="stale_before_playback")
            if chosen.revision == self._shown.get(chosen.match_id):
                return self._ack(chosen, accepted=True, shown=False, reason="duplicate")
            frame = self._frame(chosen)
            self._playing = True
        try:
            self._play(frame)
        except Exception:
            with self._lock:
                self._playing = False
                self._ack(chosen, accepted=True, shown=False, reason="render_failed")
            raise
        with self._lock:
            self._playing = False
            self._shown[chosen.match_id] = chosen.revision
            self.held = chosen
            self.face = "scoreboard"
            return self._ack(chosen, accepted=True, shown=True, reason=None)

    def render(self, snapshot: MatchSnapshot) -> ScreenAck:
        """Accept the committed snapshot, then let the single worker play the latest."""
        ack = self.accept(snapshot)
        if not ack.accepted or ack.reason == "duplicate":
            return ack
        played = self.pump()
        return played if played is not None else ack

    def release(self) -> bool:
        """Intentional exit. This is the only path back to the default face."""
        with self._lock:
            if self._released or not self._leased:
                return False
            self._queue.clear()
            self.face = "default"
            self.held = None
            self.restores += 1
            self._released = True
            self._lease.release(self)
            return True

    def _reset_match(self, match_id: str) -> None:
        self._watermark.pop(match_id, None)
        self._shown.pop(match_id, None)
        self._queue = [item for item in self._queue if item.match_id != match_id]

    def _drop_older(self, match_id: str, keep_revision: Optional[int]) -> int:
        kept: list[MatchSnapshot] = []
        dropped = 0
        for item in self._queue:
            stale = item.match_id == match_id and (keep_revision is None or item.revision < keep_revision)
            if stale:
                self._ack(item, accepted=True, shown=False, reason="stale_before_playback")
                dropped += 1
            else:
                kept.append(item)
        self._queue = kept
        return dropped

    def _frame(self, snapshot: MatchSnapshot) -> ScreenFrame:
        primary, secondary = scoreboard_lines(snapshot)
        return ScreenFrame(
            match_id=snapshot.match_id,
            revision=snapshot.revision,
            primary=primary,
            secondary=secondary,
            slot_id=self.slot_id,
        )

    def _ack(self, snapshot: MatchSnapshot, *, accepted: bool, shown: bool, reason: Optional[str]) -> ScreenAck:
        ack = ScreenAck(
            match_id=snapshot.match_id,
            revision=snapshot.revision,
            accepted=accepted,
            shown=shown,
            reason=reason,
        )
        self.acks.append(ack)
        return ack
