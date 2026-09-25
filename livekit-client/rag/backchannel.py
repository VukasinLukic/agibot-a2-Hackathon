"""Recognize turns that carry no question, so retrieval can be skipped.

Acknowledgements ("ok", "hvala", "aha") and stop commands arrive as ordinary user
turns. Retrieving for them costs a full embed + vector search and can only add
irrelevant context to the next answer.

This used to live behind the Hall of Fame tour state, which meant it stopped
applying the moment another persona was selected.
"""

from __future__ import annotations

import re
import unicodedata


_ACKNOWLEDGEMENTS = {
    # Serbian
    "ok", "oke", "okej", "u redu", "vazi", "dobro", "da", "ne", "aha", "aham",
    "hvala", "hvala ti", "hvala vam", "hvala lepo", "puno hvala", "super",
    "hvala puno", "hvala ti puno", "hvala vam puno", "hvala najlepse",
    "hvala puno", "hvala ti puno", "hvala vam puno", "hvala najlepse",
    "odlicno", "bravo", "razumem", "jasno", "tako je", "naravno", "dosta",
    "zdravo", "cao", "pozdrav", "prijatno", "dovidjenja", "dovidenja",
    # English
    "okay", "alright", "right", "yes", "no", "yeah", "yep", "sure", "thanks",
    "thank you", "thank you very much", "great", "nice", "cool", "got it",
    "i see", "understood", "of course", "enough", "hello", "hi", "hey",
    "bye", "goodbye", "good day",
}

_STOP_RE = re.compile(
    r"^(?:(?:please\s+|molim\s+te\s+|molim\s+vas\s+)?"
    r"(?:stop|stani|prestani|cuti|tisina|be\s+quiet|silence|cancel|"
    r"never\s*mind|nema\s+veze)\s*)+$"
)


def _normalized(query: str) -> str:
    """Casefold and strip diacritics so 'hvala' and 'Hvala!' compare equal."""
    folded = unicodedata.normalize("NFKD", query.casefold())
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    # NFKD leaves đ/Đ intact; they carry a stroke, not a combining mark.
    stripped = stripped.replace("đ", "dj").replace("ð", "dj")
    return " ".join(re.sub(r"[^\w ]+", " ", stripped).split())


def is_backchannel(query: str) -> bool:
    """True when the turn is an acknowledgement or a stop command, not a question."""
    normalized = _normalized(query)
    if not normalized:
        return True
    if "?" in query:
        return False
    return normalized in _ACKNOWLEDGEMENTS or bool(_STOP_RE.fullmatch(normalized))
