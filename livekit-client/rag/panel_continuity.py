"""Resolve genuine guided-tour follow-ups against the last retrieved panel."""

from __future__ import annotations

import re
from dataclasses import dataclass


_PANEL_MARKER_RE = re.compile(r"^\[(?:RETRIEVED PANEL FOR THIS TURN|ACTIVE PANEL)\]")
_SEQUENCE_RE = re.compile(r"(?m)^sequence:\s*(\d{1,2})\s*$")
_NEXT = {"next", "next panel", "move on", "continue", "go forward"}
_PREVIOUS = {"previous", "previous panel", "go back", "back"}
_NEXT_RE = re.compile(
    r"\b(?:next\s+(?:panel|slide|exhibit|one)|(?:panel|slide)\s+after\s+this|"
    r"move\s+to\s+the\s+next|following\s+(?:panel|slide|exhibit))\b"
)
_PREVIOUS_RE = re.compile(
    r"\b(?:previous\s+(?:panel|slide|exhibit|one)|(?:panel|slide)\s+before\s+this|"
    r"move\s+to\s+the\s+previous)\b"
)
_SAME_PANEL = {
    "yes",
    "yeah",
    "yep",
    "sure",
    "please",
    "go ahead",
    "tell me more",
    "more",
    "details",
    "more details",
    "go deeper",
    "check it",
    "this one",
    "this panel",
    "this slide",
    "that one",
    "that panel",
    "that slide",
}
_NO_RAG = {
    "ok",
    "okay",
    "cancel",
    "never mind",
    "nevermind",
    "be quiet",
    "silence",
}
_STOP_RE = re.compile(r"^(?:(?:please\s+)?stop\s*)+$")
_CONTEXTUAL_FOLLOWUP_RE = re.compile(
    r"^(?:in\s+which\s+(?:countries|country|year|years|city|cities)|"
    r"when\s+(?:was|did)\s+(?:that|it)|who\s+(?:was|were|did)\s+(?:that|it|they)|"
    r"where\s+(?:was|were|did)\s+(?:that|it|they)|why\s+(?:was|were|did)\s+(?:that|it|they)|"
    r"did\s+you\s+forget\s+(?:a|any|some)?\s*(?:country|countries|one|anything)|"
    r"what\s+(?:else|about\s+that)|how\s+(?:many|much)\b)",
)

HALL_OF_FAME_PERSONA = "hall_of_fame_tour"
PANEL_COUNT = 40


def panel_sequence_from_context(context: str) -> int | None:
    """Extract a valid sequence from a current-turn or legacy panel block."""
    if not _PANEL_MARKER_RE.match((context or "").lstrip()):
        return None
    match = _SEQUENCE_RE.search(context)
    if not match:
        return None
    sequence = int(match.group(1))
    return sequence if 1 <= sequence <= PANEL_COUNT else None


def _normalized_query(query: str) -> str:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", query.lower())
    return " ".join(normalized.split())


def _resolve_from_sequence(query: str, sequence: int) -> tuple[str, bool]:
    normalized = _normalized_query(query)
    if normalized in _NEXT or _NEXT_RE.search(normalized):
        return f"panel {min(PANEL_COUNT, sequence + 1)}", False
    if normalized in _PREVIOUS or _PREVIOUS_RE.search(normalized):
        return f"panel {max(1, sequence - 1)}", False
    if normalized in {"last panel", "the last panel", "final panel", "the final panel"}:
        return f"panel {PANEL_COUNT}", False
    if normalized in _SAME_PANEL:
        return query, True
    if _CONTEXTUAL_FOLLOWUP_RE.search(normalized):
        return query, True
    return query, False


@dataclass
class PanelTourState:
    """Last-result cache for true follow-ups; new topics always run retrieval."""

    persona: str = ""
    current_sequence: int | None = None
    last_context: str = ""

    @property
    def enabled(self) -> bool:
        return self.persona == HALL_OF_FAME_PERSONA

    def set_persona(self, persona: str | None) -> None:
        normalized = (persona or "").strip()
        if normalized == self.persona:
            return
        self.persona = normalized
        self.current_sequence = None
        self.last_context = ""

    def resolve(self, query: str) -> tuple[str, bool]:
        if not self.enabled:
            return query, False

        normalized = _normalized_query(query)
        if normalized in _NO_RAG or _STOP_RE.fullmatch(normalized):
            # Empty search query means: do not retrieve and do not move the
            # confirmed panel counter for a conversational control/filler turn.
            return "", False

        # Before any panel has been confirmed, "next" starts the tour at one.
        sequence = self.current_sequence if self.current_sequence is not None else 0
        resolved, reuse = _resolve_from_sequence(query, sequence)
        if reuse and not self.last_context:
            return query, False
        return resolved, reuse

    def remember_context(self, context: str) -> bool:
        """Remember a valid retrieved panel and return whether its sequence changed."""
        if not self.enabled:
            return False
        sequence = panel_sequence_from_context(context)
        if sequence is None:
            return False
        changed = sequence != self.current_sequence
        self.current_sequence = sequence
        self.last_context = context.strip()
        return changed


def resolve_panel_followup(query: str, last_context: str) -> tuple[str, bool]:
    """Return (search query, reuse_last_context) for a tour follow-up."""
    sequence = panel_sequence_from_context(last_context)
    if sequence is None:
        return query, False
    return _resolve_from_sequence(query, sequence)
