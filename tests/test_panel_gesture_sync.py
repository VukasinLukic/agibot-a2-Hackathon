import sys
from pathlib import Path
from types import SimpleNamespace


CLIENT_ROOT = Path(__file__).resolve().parents[1] / "livekit-client"
if str(CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CLIENT_ROOT))

from rag.panel_gesture_sync import PanelGestureSync


class FakeSession:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def off(self, event, handler):
        if self.handlers.get(event) is handler:
            del self.handlers[event]

    def emit_state(self, state):
        self.handlers["agent_state_changed"](SimpleNamespace(new_state=state))


def test_pending_gesture_dispatches_once_at_speaking_not_thinking():
    session = FakeSession()
    dispatched = []
    sync = PanelGestureSync()
    sync.bind(session, dispatched.append)
    sync.queue("panel explanation")

    session.emit_state("thinking")
    assert dispatched == []
    assert sync.pending == "panel explanation"
    session.emit_state("speaking")
    assert dispatched == ["panel explanation"]
    assert sync.pending is None
    session.emit_state("speaking")
    assert dispatched == ["panel explanation"]


def test_new_turn_can_replace_or_clear_a_stale_pending_gesture():
    session = FakeSession()
    dispatched = []
    sync = PanelGestureSync()
    sync.bind(session, dispatched.append)

    sync.queue("panel explanation")
    sync.queue("panel explanation long")
    session.emit_state("speaking")
    assert dispatched == ["panel explanation long"]

    sync.queue("panel explanation extended")
    sync.clear()
    session.emit_state("speaking")
    assert dispatched == ["panel explanation long"]
