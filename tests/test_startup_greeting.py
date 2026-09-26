import asyncio
import sys
from pathlib import Path


CLIENT_ROOT = Path(__file__).resolve().parents[1] / "livekit-client"
if str(CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CLIENT_ROOT))

from startup_greeting import speak_startup_greeting


class FakeInput:
    def __init__(self, events):
        self.events = events

    def set_audio_enabled(self, enabled):
        self.events.append(("input", enabled))


class FakeSession:
    def __init__(self):
        self.events = []
        self.input = FakeInput(self.events)

    async def say(self, *, text, allow_interruptions):
        self.events.append(("say", text, allow_interruptions))


def test_capture_is_closed_only_around_startup_greeting():
    session = FakeSession()
    asyncio.run(
        speak_startup_greeting(
            session,
            "Hello from TITAN",
            capture_gate_enabled=True,
            tail_ms=0,
        )
    )
    assert session.events == [
        ("input", False),
        ("say", "Hello from TITAN", False),
        ("input", True),
    ]


def test_default_off_path_keeps_interruptible_greeting_and_does_not_touch_input():
    session = FakeSession()
    asyncio.run(
        speak_startup_greeting(
            session,
            "Legacy hello",
            capture_gate_enabled=False,
        )
    )
    assert session.events == [("say", "Legacy hello", True)]
