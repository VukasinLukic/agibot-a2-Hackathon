from unittest.mock import patch

from robot_supervisor_v2.app import speech_config
from robot_supervisor_v2.app.speech_config import (
    DEFAULT_CUSTOM_TRANSFORMATIONS,
    normalize_text_for_tts,
)


def test_no_built_in_pronunciations_ship_in_either_locale() -> None:
    """Nothing is respelled unless an operator configured it in the Speech tab."""
    assert DEFAULT_CUSTOM_TRANSFORMATIONS == {}
    with patch.object(speech_config, "load_speech_config", return_value={}):
        spoken = "Comtrade igra 1000 Q drink"
        assert normalize_text_for_tts(spoken, locale="sr-RS") == spoken


def test_configured_pronunciations_apply_and_leave_numbers_alone() -> None:
    configured = {"custom_transformations": {"Comtrade": "Komtrejd"}}
    with patch.object(speech_config, "load_speech_config", return_value=configured):
        assert normalize_text_for_tts("Cena je 1000 evra.", locale="sr-RS") == "Cena je 1000 evra."
        assert normalize_text_for_tts("Comtrade 1000", locale="sr-RS") == "Komtrejd 1000"
