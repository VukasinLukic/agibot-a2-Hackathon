"""SQLite event store: command + events + snapshot + outbox in ONE transaction.

Deployment model (documented, enforced where cheap):
  * one API process is the single writer; RefereeService serialises commands
    per match with an in-process lock;
  * the database adds real guards so a second writer fails instead of
    corrupting: ``BEGIN IMMEDIATE``, a compare-and-set on the match revision,
    UNIQUE(match_id, revision) and UNIQUE(match_id, rally_id) for decisions.
Running several uvicorn workers against one file is NOT supported.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from table_tennis.contracts import EVENT_ADAPTER, MatchSnapshot
from table_tennis.core.errors import ConflictError

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commands(
    command_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    match_id TEXT,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    match_id TEXT,
    revision INTEGER,
    type TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_match ON events(match_id, cursor);
CREATE TABLE IF NOT EXISTS matches(
    match_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    state_json TEXT NOT NULL,
    last_cursor INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_revisions(
    match_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    command_id TEXT NOT NULL,
    PRIMARY KEY(match_id, revision)
);
CREATE TABLE IF NOT EXISTS rally_decisions(
    match_id TEXT NOT NULL,
    rally_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY(match_id, rally_id)
);
CREATE TABLE IF NOT EXISTS outbox(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT,
    revision INTEGER,
    event_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(event_id, kind)
);
CREATE TABLE IF NOT EXISTS robot_calls(
    call_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    json TEXT NOT NULL,
    terminal INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""

SCHEMA_VERSION_KEY = "store_schema_version"
STORE_SCHEMA_VERSION = "1"


class SqliteEventStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)", (SCHEMA_VERSION_KEY, STORE_SCHEMA_VERSION)
            )

    # ------------------------------------------------------------------ helpers

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _now(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ commands

    def get_command(self, command_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()

    # ------------------------------------------------------------------ transaction

    def append_transaction(
        self,
        *,
        scope: str,
        command_id: str,
        payload_hash: str,
        match_id: str,
        expected_previous_revision: int,
        events: Iterable[Any],
        snapshot: MatchSnapshot,
        state_json: str,
        response_json: str,
        outbox_kinds: Iterable[tuple[str, str]] = (),
        rally_decision: Optional[tuple[str, str]] = None,
    ) -> list[int]:
        """Atomically persist a command outcome. Returns event cursors in order.

        ``outbox_kinds`` is a list of (event_id, kind) side-effect jobs.
        Raises ConflictError if another writer advanced the match meanwhile or
        the rally already has a decision.
        """
        events = list(events)
        now = self._now()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                row = cur.execute("SELECT revision FROM matches WHERE match_id=?", (match_id,)).fetchone()
                current = row["revision"] if row else 0
                if current != expected_previous_revision:
                    raise ConflictError(
                        "stale_revision",
                        "match advanced concurrently (another writer)",
                        current_revision=current,
                    )
                cur.execute(
                    "INSERT INTO commands(command_id, scope, payload_hash, match_id, response_json, created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (command_id, scope, payload_hash, match_id, response_json, now),
                )
                cursors: list[int] = []
                for e in events:
                    cur.execute(
                        "INSERT INTO events(event_id, match_id, revision, type, json) VALUES(?,?,?,?,?)",
                        (e.event_id, e.match_id, e.revision, e.type, e.model_dump_json()),
                    )
                    cursors.append(int(cur.lastrowid))
                if events:
                    cur.execute(
                        "INSERT INTO match_revisions(match_id, revision, command_id) VALUES(?,?,?)",
                        (match_id, snapshot.revision, command_id),
                    )
                if rally_decision is not None:
                    try:
                        cur.execute(
                            "INSERT INTO rally_decisions(match_id, rally_id, event_id) VALUES(?,?,?)",
                            (match_id, rally_decision[0], rally_decision[1]),
                        )
                    except sqlite3.IntegrityError:
                        raise ConflictError(
                            "rally_already_decided",
                            "this rally already has a final decision",
                            current_revision=current,
                        )
                last_cursor = cursors[-1] if cursors else None
                if row is None:
                    cur.execute(
                        "INSERT INTO matches(match_id, revision, snapshot_json, state_json, last_cursor, created_at, updated_at)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (match_id, snapshot.revision, snapshot.model_dump_json(), state_json, last_cursor or 0, now, now),
                    )
                elif events:
                    cur.execute(
                        "UPDATE matches SET revision=?, snapshot_json=?, state_json=?, last_cursor=?, updated_at=?"
                        " WHERE match_id=? AND revision=?",
                        (
                            snapshot.revision,
                            snapshot.model_dump_json(),
                            state_json,
                            last_cursor,
                            now,
                            match_id,
                            expected_previous_revision,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise ConflictError("stale_revision", "match advanced concurrently", current_revision=current)
                for event_id, kind in outbox_kinds:
                    cur.execute(
                        "INSERT INTO outbox(match_id, revision, event_id, kind, status, created_at, updated_at)"
                        " VALUES(?,?,?,?, 'pending', ?, ?)",
                        (match_id, snapshot.revision, event_id, kind, now, now),
                    )
                cur.execute("COMMIT")
                return cursors
            except BaseException:
                cur.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------------ reads

    def list_matches(self) -> list[str]:
        with self._lock:
            return [r["match_id"] for r in self._conn.execute("SELECT match_id FROM matches ORDER BY created_at")]

    def load_match(self, match_id: str) -> Optional[tuple[MatchSnapshot, str, int]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM matches WHERE match_id=?", (match_id,)).fetchone()
        if row is None:
            return None
        return MatchSnapshot.model_validate_json(row["snapshot_json"]), row["state_json"], int(row["last_cursor"])

    def events_for_match(self, match_id: str) -> list[Any]:
        return [e for _, e in self.read_after(match_id, 0, limit=1_000_000)]

    def read_after(self, match_id: str, cursor: int, limit: int = 1000) -> list[tuple[int, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT cursor, json FROM events WHERE match_id=? AND cursor>? ORDER BY cursor LIMIT ?",
                (match_id, cursor, limit),
            ).fetchall()
        return [(int(r["cursor"]), EVENT_ADAPTER.validate_json(r["json"])) for r in rows]

    def cursor_of(self, event_id: str) -> Optional[tuple[Optional[str], int]]:
        with self._lock:
            row = self._conn.execute("SELECT match_id, cursor FROM events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return None
        return row["match_id"], int(row["cursor"])

    def latest_cursor(self, match_id: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT last_cursor FROM matches WHERE match_id=?", (match_id,)).fetchone()
        return int(row["last_cursor"]) if row else 0

    def get_event(self, event_id: str) -> Optional[Any]:
        with self._lock:
            row = self._conn.execute("SELECT json FROM events WHERE event_id=?", (event_id,)).fetchone()
        return EVENT_ADAPTER.validate_json(row["json"]) if row else None

    # ------------------------------------------------------------------ outbox

    def outbox_pending(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM outbox WHERE status='pending' ORDER BY id LIMIT ?", (limit,)
            ).fetchall()

    def outbox_mark(self, row_id: int, status: str, detail: Optional[str] = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET status=?, detail=?, updated_at=? WHERE id=?", (status, detail, self._now(), row_id)
            )

    def outbox_rows(self, match_id: Optional[str] = None) -> list[sqlite3.Row]:
        with self._lock:
            if match_id:
                return self._conn.execute("SELECT * FROM outbox WHERE match_id=? ORDER BY id", (match_id,)).fetchall()
            return self._conn.execute("SELECT * FROM outbox ORDER BY id").fetchall()

    def outbox_recover_on_startup(self) -> dict[str, int]:
        """Never replay physical/audible side effects after a restart.

        pending speech/gesture -> skipped_restart; in_progress -> unknown_restart
        (outcome unknown: not retried, no exactly-once promise); pending display
        rows -> superseded (the dispatcher renders the current snapshot once).
        """
        with self._lock:
            now = self._now()
            c1 = self._conn.execute(
                "UPDATE outbox SET status='skipped_restart', updated_at=? WHERE status='pending' AND kind IN ('speech','gesture')",
                (now,),
            ).rowcount
            c2 = self._conn.execute(
                "UPDATE outbox SET status='unknown_restart', updated_at=? WHERE status='in_progress'", (now,)
            ).rowcount
            c3 = self._conn.execute(
                "UPDATE outbox SET status='superseded', updated_at=? WHERE status='pending' AND kind='display'", (now,)
            ).rowcount
        return {"skipped": c1, "unknown": c2, "superseded": c3}

    # ------------------------------------------------------------------ robot calls

    def robot_call_by_command(self, command_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM robot_calls WHERE command_id=?", (command_id,)).fetchone()

    def robot_call(self, call_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM robot_calls WHERE call_id=?", (call_id,)).fetchone()

    def robot_calls_active(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM robot_calls WHERE terminal=0").fetchall()

    def save_robot_call(self, call_id: str, command_id: str, payload_hash: str, call_json: str, terminal: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO robot_calls(call_id, command_id, payload_hash, json, terminal, updated_at) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(call_id) DO UPDATE SET json=excluded.json, terminal=excluded.terminal, updated_at=excluded.updated_at",
                (call_id, command_id, payload_hash, call_json, int(terminal), self._now()),
            )

    def record_scoped_command(self, command_id: str, scope: str, payload_hash: str, response_json: str) -> None:
        """Idempotency record for non-match scopes (robot call cancel)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO commands(command_id, scope, payload_hash, match_id, response_json, created_at) VALUES(?,?,?,?,?,?)",
                (command_id, scope, payload_hash, None, response_json, self._now()),
            )


def canonical_hash(data: Any) -> str:
    import hashlib

    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
