"""SpeechOutput implementations for the persona branch (owner: person 4).

FakeSpeechOutput records the exact sentence it would say with event_id and
revision. LiveKitSpeechOutput is the marked place for the real voice path
(robot_supervisor_v2 agent command stream); it is dry-run until wired.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from table_tennis.contracts import MatchSnapshot
from table_tennis.core.fake_log import FakeOutputLog

from .commentator import Commentator

log = logging.getLogger("table_tennis.persona")


class FakeSpeechOutput:
    def __init__(self, log_: FakeOutputLog, commentator: Optional[Commentator] = None):
        self.log = log_
        self.commentator = commentator or Commentator()
        self.spoken: list[dict[str, Any]] = []
        self._seen: set[str] = set()
        self.cancelled: list[str] = []

    def announce(self, event: Any, snapshot: MatchSnapshot) -> None:
        if event.event_id in self._seen:
            return  # dedup by event_id
        self._seen.add(event.event_id)
        text = self.commentator.line_for(event, snapshot)
        if not text:
            return
        self.spoken.append({"event_id": event.event_id, "revision": event.revision, "text": text})
        self.log.record("speech", text, match_id=event.match_id, event_id=event.event_id, revision=event.revision)

    def cancel_pending(self, match_id: str) -> None:
        self.cancelled.append(match_id)


class LiveKitSpeechOutput:
    """Scaffold for the real voice path. Dry-run until person 4 wires it.

    REAL: send ``text`` through robot_supervisor_v2.app.utils.agent_commands
    (build_command_payload / send_agent_command) with event_id + revision so the
    agent can drop stale lines; measure latency (it opens a room per call).
    Imports happen lazily inside ``send`` so mock mode never loads LiveKit.
    """

    def __init__(self, commentator: Optional[Commentator] = None, *, send: Optional[Callable[[str, dict], Any]] = None, dry_run: bool = True):
        if not dry_run and send is None:
            raise RuntimeError("LiveKitSpeechOutput: real send() not implemented yet (person 4); keep dry_run=True")
        self.commentator = commentator or Commentator()
        self.dry_run = dry_run
        self._send = send
        self.sent: list[str] = []

    def announce(self, event: Any, snapshot: MatchSnapshot) -> None:
        text = self.commentator.line_for(event, snapshot)
        if not text:
            return
        self.sent.append(text)
        if self.dry_run or self._send is None:
            log.info("[DRY-RUN SPEECH] %s", text)
            return
        self._send(text, {"event_id": event.event_id, "revision": event.revision, "match_id": event.match_id})

    def cancel_pending(self, match_id: str) -> None:
        # REAL: tell the agent to drop queued referee lines for this match.
        log.info("[DRY-RUN SPEECH] cancel pending for %s", match_id)
