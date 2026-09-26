"""Small transcript-level fallback for residual acoustic self-echo."""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import Any


_STOP_COMMANDS = {
    "cekaj",
    "cuti",
    "dosta",
    "pause",
    "sacekaj",
    "stani",
    "stop",
    "wait",
}


def normalize_echo_text(text: str) -> str:
    value = str(text).lower().translate(str.maketrans("čćžšđ", "cczsd"))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value)).strip()


def contains_stop_command(text: str) -> bool:
    return bool(set(normalize_echo_text(text).split()) & _STOP_COMMANDS)


def looks_like_self_echo(
    transcript: str,
    recently_spoken_text: str,
    *,
    similarity_threshold: float = 0.82,
) -> bool:
    """Match a substantial transcript against any part of recent TTS.

    Streaming TTS can be considerably ahead of audible playback, so an echo
    fragment is not necessarily near the end of the generated text.  Compare
    similarly sized windows as well as exact substrings.  The relaxed floor
    catches common STT distortions (for example ``pole-vaulter`` becoming
    ``ballpark``) without treating short backchannels as echo.
    """
    heard = normalize_echo_text(transcript)
    spoken = normalize_echo_text(recently_spoken_text)
    if len(heard) < 8 or len(spoken) < 8:
        return False
    if heard in spoken:
        return True

    heard_words = heard.split()
    spoken_words = spoken.split()
    if len(heard_words) < 4:
        return False

    best_ratio = 0.0
    min_size = max(1, len(heard_words) - 2)
    max_size = min(len(spoken_words), len(heard_words) + 3)
    for window_size in range(min_size, max_size + 1):
        for start in range(0, len(spoken_words) - window_size + 1):
            candidate = " ".join(spoken_words[start : start + window_size])
            best_ratio = max(
                best_ratio,
                SequenceMatcher(None, heard, candidate).ratio(),
            )
            if best_ratio >= similarity_threshold:
                return True

    relaxed_threshold = max(0.70, similarity_threshold - 0.12)
    return best_ratio >= relaxed_threshold


class SpeechEchoWindow:
    """Track actual agent playback so echo protection follows audible speech."""

    def __init__(self) -> None:
        self._session: Any | None = None
        self._handler: Any | None = None
        self._speaking = False
        self._last_speaking_end = 0.0

    def bind(self, session: Any) -> None:
        if self._session is session:
            return
        self.unbind()

        def _on_agent_state_changed(event: Any) -> None:
            state = getattr(event, "new_state", None)
            state = getattr(state, "value", state)
            speaking = state == "speaking"
            if self._speaking and not speaking:
                self._last_speaking_end = time.monotonic()
            self._speaking = speaking

        session.on("agent_state_changed", _on_agent_state_changed)
        self._session = session
        self._handler = _on_agent_state_changed

    def active(self, *, tail_seconds: float = 1.5) -> bool:
        return self._speaking or (
            self._last_speaking_end > 0.0
            and time.monotonic() - self._last_speaking_end <= tail_seconds
        )

    def unbind(self) -> None:
        session = self._session
        handler = self._handler
        self._session = None
        self._handler = None
        self._speaking = False
        self._last_speaking_end = 0.0
        if session is not None and handler is not None and hasattr(session, "off"):
            session.off("agent_state_changed", handler)
