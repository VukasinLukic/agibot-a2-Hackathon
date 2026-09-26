"""Shared record of what fake adapters *would* have done (mock mode only)."""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import Optional

from table_tennis.contracts import DebugOutputs, FakeOutputRecord

from .ports import Clock, SystemClock


class FakeOutputLog:
    def __init__(self, log_path: Optional[str | Path] = None, clock: Optional[Clock] = None, keep: int = 200):
        self.clock = clock or SystemClock()
        self.display: Optional[FakeOutputRecord] = None
        self.speech: deque[FakeOutputRecord] = deque(maxlen=keep)
        self.gestures: deque[FakeOutputRecord] = deque(maxlen=keep)
        self._lock = threading.Lock()
        self.log_path = Path(log_path) if log_path else None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, text: str, *, match_id=None, event_id=None, revision=None) -> FakeOutputRecord:
        rec = FakeOutputRecord(
            kind=kind, match_id=match_id, event_id=event_id, revision=revision, text=text, at=self.clock.now()
        )
        with self._lock:
            if kind == "display":
                self.display = rec
            elif kind == "speech":
                self.speech.append(rec)
            else:
                self.gestures.append(rec)
            if self.log_path:
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"[SIMULATED {kind.upper()}] {rec.model_dump_json()}\n")
        return rec

    def view(self, match_id: Optional[str] = None, last: int = 20) -> DebugOutputs:
        with self._lock:
            speech = [r for r in self.speech if match_id is None or r.match_id == match_id][-last:]
            gestures = [r for r in self.gestures if match_id is None or r.match_id == match_id][-last:]
            display = self.display if (match_id is None or (self.display and self.display.match_id == match_id)) else None
        return DebugOutputs(simulated=True, display=display, speech=speech, gestures=gestures)
