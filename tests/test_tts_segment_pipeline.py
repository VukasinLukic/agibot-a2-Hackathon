"""Guards the spoken-text pipeline: segment buffering, number expansion, pronunciations."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = REPO_ROOT / "livekit-client"
if str(CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CLIENT_ROOT))

from livekit_config.tts_normalizer import normalize_tts_text
from rag.backchannel import is_backchannel
from robot_supervisor_v2.app import speech_config
from robot_supervisor_v2.app.speech_config import normalize_text_for_tts


# --- B: numbers and times -------------------------------------------------

def test_agenda_times_are_spoken_as_words() -> None:
    assert normalize_tts_text("Panel je u 15:00.", "sr") == "Panel je u petnaest časova."
    assert normalize_tts_text("Počinje u 9:30.", "sr") == "Počinje u devet i trideset."


def test_years_are_spoken_as_ordinals() -> None:
    assert "dve hiljade dvadeset šesta" in normalize_tts_text("Finance Day 2026.", "sr")


def test_non_serbian_language_keeps_digits() -> None:
    assert normalize_tts_text("The panel starts at 15:00.", "en") == "The panel starts at 15:00."


def test_markdown_is_stripped_in_every_language() -> None:
    assert normalize_tts_text("**bold** and `code`", "en") == "bold and code"


# --- A: segment buffering -------------------------------------------------

def _split_all(chunks: list[str]) -> str:
    """Drive the real buffering helper the way tts_node does."""
    from agent_main import _split_spoken_segment

    carry = ""
    out: list[str] = []
    for chunk in chunks:
        carry += chunk
        while True:
            emit, carry = _split_spoken_segment(carry)
            if not emit:
                break
            out.append(emit)
    if carry:
        emit, _ = _split_spoken_segment(carry, force=True)
        out.append(emit)
    return "".join(out)


def test_buffering_is_lossless() -> None:
    chunks = ["Dobro", "došli ", "na Fin", "ance Day. ", "Panel je u 15:00, ", "a pauza kasnije."]
    assert _split_all(chunks) == "".join(chunks)


def test_replacement_survives_a_word_split_across_chunks() -> None:
    from agent_main import _split_spoken_segment

    configured = {"english_transformations": {"Comtrade": "Komtrejd"}}
    chunks = ["Dobrodošli u ", "Com", "trade", ", drago mi je što ste ovde danas."]

    with patch.dict(
        os.environ,
        {"VOICE_CANARY_ENABLED": "true", "VOICE_LANGUAGE": "en", "VOICE_LOCALE": "en-US"},
        clear=False,
    ), patch.object(speech_config, "load_speech_config", return_value=configured):
        carry = ""
        spoken: list[str] = []
        for chunk in chunks:
            carry += chunk
            while True:
                emit, carry = _split_spoken_segment(carry)
                if not emit:
                    break
                spoken.append(normalize_text_for_tts(emit, locale="en-US"))
        if carry:
            emit, _ = _split_spoken_segment(carry, force=True)
            spoken.append(normalize_text_for_tts(emit, locale="en-US"))

    assert "Komtrejd" in "".join(spoken)
    assert "Comtrade" not in "".join(spoken)


# --- C: backchannel gate --------------------------------------------------

def test_acknowledgements_skip_retrieval() -> None:
    for query in ("ok", "Hvala!", "aha", "Stop.", "molim te stani", "super", "Da"):
        assert is_backchannel(query), query


def test_real_questions_still_retrieve() -> None:
    for query in ("Kada počinje prvi panel?", "Hvala, a kada je pauza?", "Ko su panelisti"):
        assert not is_backchannel(query), query
