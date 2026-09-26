"""The vision process posts commands with its own token."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("numpy")

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision.live import fetch_snapshot, post_command

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
        self.assertEqual(posted["path"], f"/api/table-tennis/matches/{MATCH}/commands")
        self.assertEqual(posted["type"], "point.propose")


if __name__ == "__main__":
    unittest.main()
