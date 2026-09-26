import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


CLIENT_ROOT = Path(__file__).resolve().parents[1] / "livekit-client"
if str(CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CLIENT_ROOT))

from self_echo_guard import SpeechEchoWindow, contains_stop_command, looks_like_self_echo


class _Session:
    def __init__(self) -> None:
        self.handlers = {}

    def on(self, event, handler) -> None:
        self.handlers[event] = handler

    def off(self, event, handler) -> None:
        if self.handlers.get(event) is handler:
            del self.handlers[event]


class SelfEchoGuardTests(unittest.TestCase):
    def test_exact_and_partial_tts_echo_are_rejected(self) -> None:
        spoken = "Hello, I am TITAN. Welcome to Comtrade System Integration."
        self.assertTrue(looks_like_self_echo("Hello, I am TITAN", spoken))
        self.assertTrue(looks_like_self_echo(spoken, spoken))

    def test_different_human_interruption_passes(self) -> None:
        spoken = "Welcome to Comtrade System Integration. How can I help you?"
        self.assertFalse(
            looks_like_self_echo("Stop and tell me where the meeting room is", spoken)
        )

    def test_short_backchannel_is_not_treated_as_echo(self) -> None:
        self.assertFalse(looks_like_self_echo("Dobro", "Dobro dosli u Comtrade"))

    def test_matches_echo_from_middle_of_streamed_tts(self) -> None:
        spoken = (
            "Our story starts not with a computer but with an athlete. "
            "Veselin was a pole-vaulter and champion of the former Yugoslav "
            "University League. This period shaped everything that followed."
        )
        self.assertTrue(
            looks_like_self_echo("Our story doesn't start with a computer", spoken)
        )
        self.assertTrue(looks_like_self_echo("Was a ballpark, a champion", spoken))

    def test_unrelated_full_question_is_not_echo(self) -> None:
        spoken = "Veselin was a pole-vaulter and champion before studying in America."
        self.assertFalse(looks_like_self_echo("Who founded the Comtrade company", spoken))

    def test_stop_commands_bypass_echo_policy(self) -> None:
        self.assertTrue(contains_stop_command("TITAN, stop speaking"))
        self.assertTrue(contains_stop_command("Stani, molim te"))

    def test_speech_window_covers_playback_and_short_tail(self) -> None:
        session = _Session()
        window = SpeechEchoWindow()
        with patch("self_echo_guard.time.monotonic", side_effect=[10.0, 10.5, 12.0]):
            window.bind(session)
            session.handlers["agent_state_changed"](SimpleNamespace(new_state="speaking"))
            self.assertTrue(window.active())
            session.handlers["agent_state_changed"](SimpleNamespace(new_state="listening"))
            self.assertTrue(window.active(tail_seconds=1.0))
            self.assertFalse(window.active(tail_seconds=1.0))


if __name__ == "__main__":
    unittest.main()
