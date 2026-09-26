"""SpeechOutput implementations for the persona branch (owner: person 4).

FakeSpeechOutput records the exact sentence it would say with event_id and
revision. LiveKitSpeechOutput is the real voice path: it posts the exact line
to the Supervisor's ``/api/conversation/command`` route, and the voice agent
speaks it verbatim with ``session.say`` (Soniox TTS, no LLM in between).

The same route carries the referee-mode switches for the voice agent
(``__REFEREE_ON__:<persona>`` / ``__REFEREE_OFF__``, see
``livekit-client/referee_mode.py``): while a match runs, free conversation
follows the referee persona and stays quiet during rallies.

Env (real mode only):
  TT_SUPERVISOR_URL    Supervisor base URL (default http://127.0.0.1:8070)
  TT_SUPERVISOR_TOKEN  optional bearer token for the Supervisor
  TT_SPEECH_TIMEOUT_S  HTTP timeout per line (default 6)
  TT_SPEECH_LIVE=1     speak for real even in mode=mock (with TT_ADAPTER_SPEECH=livekit),
                       so voice can be tried on the robot before screen/gesture/navigation are real
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
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


REFEREE_ON = "__REFEREE_ON__"
REFEREE_OFF = "__REFEREE_OFF__"


class SupervisorCommandTransport:
    """POST one line to the Supervisor, which forwards it to the voice agent.

    A 2xx answer means "published to the agent", not "finished speaking" (the
    route has no completion event). Failures raise, so the output dispatcher
    counts them in /health; the score itself is never affected.
    """

    def __init__(self, base_url: Optional[str] = None, *, token: Optional[str] = None, timeout_s: Optional[float] = None):
        self.url = (base_url or os.getenv("TT_SUPERVISOR_URL") or "http://127.0.0.1:8070").rstrip("/") + "/api/conversation/command"
        self.token = token if token is not None else (os.getenv("TT_SUPERVISOR_TOKEN") or None)
        self.timeout_s = timeout_s or float(os.getenv("TT_SPEECH_TIMEOUT_S", "6"))

    def __call__(self, text: str, meta: dict) -> None:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as res:
                res.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"supervisor speech HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"supervisor speech unreachable: {exc}") from exc
        log.info(
            "SPEECH sent in %.0f ms (match=%s revision=%s): %s",
            (time.monotonic() - started) * 1000, meta.get("match_id"), meta.get("revision"), text,
        )


class LiveKitSpeechOutput:
    """Real voice path through the Supervisor (dry-run in mock mode).

    Besides the referee line, it switches the voice agent into referee mode on
    match start / persona change and back out when the match ends.
    """

    def __init__(self, commentator: Optional[Commentator] = None, *, send: Optional[Callable[[str, dict], Any]] = None, dry_run: bool = True):
        if dry_run and os.getenv("TT_SPEECH_LIVE", "").strip().lower() in ("1", "true", "yes", "on"):
            dry_run = False
            log.warning("TT_SPEECH_LIVE=1: referee lines go to the real Supervisor voice in mock mode")
        if not dry_run and send is None:
            send = SupervisorCommandTransport()
        self.commentator = commentator or Commentator()
        self.dry_run = dry_run
        self._send = send
        self._seen: set[str] = set()
        self.sent: list[str] = []

    def _emit(self, text: str, meta: dict) -> None:
        self.sent.append(text)
        if self.dry_run or self._send is None:
            log.info("[DRY-RUN SPEECH] %s", text)
            return
        self._send(text, meta)

    def announce(self, event: Any, snapshot: MatchSnapshot) -> None:
        if event.event_id in self._seen:
            return  # dedup by event_id
        self._seen.add(event.event_id)
        meta = {"event_id": event.event_id, "revision": event.revision, "match_id": event.match_id}
        # Agent control first, so a question asked right after the greeting already gets the referee persona.
        if event.type in ("match.started", "persona.changed"):
            self._emit(f"{REFEREE_ON}:{snapshot.persona}", meta)
        text = self.commentator.line_for(event, snapshot)
        if text:
            self._emit(text, meta)
        if event.type == "match.finished":
            self._emit(REFEREE_OFF, meta)

    def cancel_pending(self, match_id: str) -> None:
        # The Supervisor route has no queue to cancel: each line is published
        # immediately and stale lines are already skipped by the dispatcher.
        log.info("SPEECH cancel pending for %s (nothing queued on the Supervisor side)", match_id)
