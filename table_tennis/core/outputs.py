"""Post-commit side-effect dispatcher (outbox consumer).

Rules (contract v1, section 10):
  * display is idempotent: latest snapshot wins, older display jobs are superseded;
  * speech/gesture are keyed by (event_id, kind) - UNIQUE in the outbox - and
    are skipped when stale: the point was undone, a new rally already started,
    or the job is older than the TTL;
  * a job is marked in_progress before a physical/audible action; after a crash
    it becomes unknown_restart and is NOT retried (no exactly-once promise);
  * at startup pending speech/gesture jobs are skipped, never replayed;
  * adapter failures are recorded and never touch the score.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from .ports import Clock, GestureOutput, ScoreDisplay, SpeechOutput, SystemClock
from .service import RefereeService

log = logging.getLogger("table_tennis.outputs")


class OutputDispatcher:
    def __init__(
        self,
        service: RefereeService,
        display: ScoreDisplay,
        speech: SpeechOutput,
        gesture: GestureOutput,
        *,
        clock: Optional[Clock] = None,
        ttl_s: float = 20.0,
        background: bool = True,
    ):
        self.service = service
        self.store = service.store
        self.display = display
        self.speech = speech
        self.gesture = gesture
        self.clock = clock or SystemClock()
        self.ttl_s = ttl_s
        self.background = background
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._drain_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None
        # Per-output delivery status for health/UI (the score never depends on it).
        self._status_lock = threading.Lock()
        self._status: dict[str, dict] = {
            k: {"done": 0, "failed": 0, "skipped": 0, "last_error": None, "last_error_at": None, "last_ok_at": None}
            for k in ("display", "speech", "gesture")
        }
        service.add_commit_listener(self.kick)

    def _record(self, kind: str, outcome: str, error: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self._status_lock:
            st = self._status[kind]
            st[outcome] += 1
            if outcome == "done":
                st["last_ok_at"] = now
            elif outcome == "failed":
                st["last_error"] = (error or "")[:300]
                st["last_error_at"] = now

    def output_status(self) -> dict[str, dict]:
        """Snapshot of delivery counters; ``healthy`` is False while the latest attempt failed."""
        with self._status_lock:
            out = {}
            for kind, st in self._status.items():
                healthy = st["last_error_at"] is None or (st["last_ok_at"] or "") >= st["last_error_at"]
                out[kind] = dict(st, healthy=healthy)
            return out

    # ------------------------------------------------------------------ lifecycle

    def startup(self) -> dict[str, int]:
        """Recover after restart without replaying speech/gestures."""
        counts = self.store.outbox_recover_on_startup()
        match_id = self.service.latest_match_id()
        if match_id:
            try:
                self.display.render(self.service.get_snapshot(match_id))
            except Exception as exc:  # display failure never blocks startup
                self.last_error = f"display: {exc}"
        if self.background:
            self._thread = threading.Thread(target=self._run, name="tt-outputs", daemon=True)
            self._thread.start()
        return counts

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            self.display.close()
        except Exception:
            pass

    def kick(self, match_id: Optional[str] = None) -> None:
        if self.background:
            self._wake.set()
        else:
            self.drain()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.drain()
            except Exception:
                log.exception("output drain failed")

    # ------------------------------------------------------------------ processing

    def drain(self) -> int:
        processed = 0
        with self._drain_lock:
            while True:
                rows = self.store.outbox_pending(limit=100)
                if not rows:
                    return processed
                # display: only the newest job per match is rendered
                newest_display: dict[str, int] = {}
                for r in rows:
                    if r["kind"] == "display":
                        newest_display[r["match_id"]] = r["id"]
                for r in rows:
                    processed += 1
                    kind = r["kind"]
                    if kind == "display":
                        if newest_display.get(r["match_id"]) != r["id"]:
                            self.store.outbox_mark(r["id"], "superseded")
                            continue
                        self._do_display(r)
                    elif kind in ("speech", "gesture"):
                        self._do_transient(r)
                    else:
                        self.store.outbox_mark(r["id"], "failed", f"unknown kind {kind}")

    def _do_display(self, row) -> None:
        try:
            snap = self.service.get_snapshot(row["match_id"])
            self.display.render(snap)
            self.store.outbox_mark(row["id"], "done", f"rev {snap.revision}")
            self._record("display", "done")
        except Exception as exc:
            self.last_error = f"display: {exc}"
            self.store.outbox_mark(row["id"], "failed", str(exc)[:500])
            self._record("display", "failed", str(exc))

    def _stale_reason(self, row, event, current) -> Optional[str]:
        created = datetime.fromisoformat(row["created_at"])
        age = (self.clock.now() - created).total_seconds()
        if age > self.ttl_s:
            return "expired"
        if event.type == "point.confirmed" and not self.service.is_point_active(row["match_id"], event.event_id):
            return "undone"
        if current.revision > row["revision"] and current.status in ("rally", "pending_decision"):
            return "next_rally_started"
        return None

    def _do_transient(self, row) -> None:
        kind = row["kind"]
        event = self.store.get_event(row["event_id"])
        if event is None:
            self.store.outbox_mark(row["id"], "failed", "event missing")
            return
        current = self.service.get_snapshot(row["match_id"])
        reason = self._stale_reason(row, event, current)
        if reason:
            self.store.outbox_mark(row["id"], "skipped_stale", reason)
            self._record(kind, "skipped")
            return
        if event.type == "score.corrected":
            # undo: cancel queued items before announcing the correction
            try:
                self.gesture.cancel_pending(row["match_id"])
                self.speech.cancel_pending(row["match_id"])
            except Exception:
                log.exception("cancel_pending failed")
        snap = getattr(event.payload, "snapshot", None) or current
        self.store.outbox_mark(row["id"], "in_progress")
        try:
            if kind == "speech":
                self.speech.announce(event, snap)
            else:
                self.gesture.present_point(event, snap)
            self.store.outbox_mark(row["id"], "done")
            self._record(kind, "done")
        except Exception as exc:
            self.last_error = f"{kind}: {exc}"
            self.store.outbox_mark(row["id"], "failed", str(exc)[:500])
            self._record(kind, "failed", str(exc))
