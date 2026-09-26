"""The vision process posts commands with its own token."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("numpy")

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision.live import (
    BackendUnavailable,
    _ClipSink,
    _SnapshotFeed,
    deliver_command,
    fetch_snapshot,
    post_command,
)

MATCH = "00000000-0000-4000-8000-0000000000a1"


def _snapshot() -> dict[str, object]:
    return {
        "match_id": MATCH,
        "revision": 4,
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
        "court_end_by_player": {"p1": "end_a", "p2": "end_b"},
        "robot_side_by_player": {"p1": "left", "p2": "right"},
        "calibration_id": "table-v1",
        "active_rally_id": "00000000-0000-4000-8000-0000000000b1",
        "active_proposal_id": None,
        "persona": "regular",
        "scoring_mode": "assisted",
        "ready": {"calibration_ready": True, "camera_ready": True},
        "updated_at": datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc).isoformat(),
    }


class LiveClientTests(unittest.TestCase):
    def test_commands_use_the_vision_token_and_keep_409(self) -> None:
        posted: dict[str, object] = {}
        snapshot = _snapshot()

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._send(200, snapshot)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                command = json.loads(self.rfile.read(length).decode("utf-8"))
                posted["auth"] = self.headers.get("Authorization")
                posted["actor"] = self.headers.get("X-TT-Actor")
                posted["path"] = self.path
                posted["type"] = command["type"]
                code = 409 if command["type"] == "point.propose" else 200
                self._send(code, {"detail": "conflict"} if code == 409 else {})

            def _send(self, code: int, payload: dict[str, object]) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            loaded = fetch_snapshot(base, "vision-token", MATCH)
            self.assertEqual(str(loaded.match_id), MATCH)
            ready = post_command(
                base,
                "vision-token",
                MATCH,
                {"type": "camera.ready.set", "command_id": "1", "expected_revision": None, "payload": {}},
            )
            refused = post_command(
                base,
                "vision-token",
                MATCH,
                {"type": "point.propose", "command_id": "2", "expected_revision": 4, "payload": {}},
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
        self.assertEqual(ready["status"], 200)
        self.assertEqual(refused["status"], 409)
        self.assertEqual(posted["auth"], "Bearer vision-token")
        self.assertEqual(posted["actor"], "vision")
        self.assertEqual(posted["path"], f"/api/table-tennis/matches/{MATCH}/commands")
        self.assertEqual(posted["type"], "point.propose")

    def test_a_closed_port_is_unavailable(self) -> None:
        with self.assertRaises(BackendUnavailable):
            fetch_snapshot("http://127.0.0.1:1", "vision-token", MATCH)

    def test_a_proposal_is_posted_twice_with_the_same_id(self) -> None:
        seen: list[str] = []

        def send(command: dict[str, object]) -> dict[str, object]:
            seen.append(str(command["command_id"]))
            if len(seen) == 1:
                raise BackendUnavailable("down")
            return {"status": 200, "body": {}}

        reply = deliver_command(send, {"type": "point.propose", "command_id": "same"})
        self.assertEqual(reply["status"], 200)
        self.assertEqual(seen, ["same", "same"])

    def test_a_rejected_command_is_logged(self) -> None:
        with self.assertLogs("table_tennis.vision.live", level="WARNING") as logged:
            deliver_command(lambda _: {"status": 403, "body": {"detail": "actor"}}, {"type": "point.propose"})
        self.assertIn("403", logged.output[0])

    def test_a_failed_recording_does_not_hang_on_close(self) -> None:
        from table_tennis.vision.image import BgrImage

        class _Frame:
            def __init__(self, index: int) -> None:
                self.capture_monotonic_ns = index * 33_333_333
                self.width = 2
                self.height = 2
                self.image = BgrImage(2, 2, bytes(12))

        with tempfile.TemporaryDirectory() as folder:
            sink = _ClipSink(folder)
            for index in range(40):
                sink.write(_Frame(index))
            started = time.monotonic()
            sink.close()
        self.assertLess(time.monotonic() - started, 3.0)

    def test_a_feed_that_never_connected_is_not_alive(self) -> None:
        feed = _SnapshotFeed("http://127.0.0.1:1", "vision-token", MATCH)
        try:
            self.assertFalse(feed.alive())
            self.assertIsNone(feed.latest)
        finally:
            feed.close()


if __name__ == "__main__":
    unittest.main()
