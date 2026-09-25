import sys
from pathlib import Path


CLIENT_ROOT = Path(__file__).resolve().parents[1] / "livekit-client"
if str(CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CLIENT_ROOT))

from rag.grounding import build_rag_developer_context, panel_explanation_gesture


def test_nonempty_context_requires_direct_comtrade_grounding():
    message = build_rag_developer_context(
        "Veselin Jevrosimovic is the founder whose story opens the Hall of Fame."
    )
    assert "answer the visitor directly" in message
    assert "means Comtrade" in message
    assert "do not ask which company" in message
    assert "never mix in another panel" in message
    assert "do not invent an answer" in message


def test_empty_context_does_not_claim_knowledge():
    message = build_rag_developer_context("")
    assert "(empty)" in message
    assert "answer normally" in message
    assert "authoritative source" not in message


def test_retrieved_panel_is_last_for_recency_and_never_duplicated():
    panel = "[RETRIEVED PANEL FOR THIS TURN]\ntitle: Beginnings\nnarration: Our story starts here."
    message = build_rag_developer_context(panel)
    assert message.count("[RETRIEVED PANEL FOR THIS TURN]") == 1
    assert message.endswith(panel)
    assert "Never ask which company" in message
    assert "current-turn evidence only" in message
    assert "Every new topic may retrieve any other panel" in message
    assert "cover every distinct fact" in message
    assert "Never ask a follow-up question" in message
    assert "first audible speech" in message
    assert "Do not call trigger_gesture" in message
    assert "physical command, not a panel question" in message
    assert "Call trigger_gesture for the requested catalog gesture" in message
    assert "never guess the panel's position" in message


def test_panel_explanation_gesture_rotates_deterministically_by_sequence():
    expected = {
        1: "panel explanation",
        2: "panel explanation extended",
        3: "panel explanation long",
        4: "panel explanation",
    }

    for sequence, gesture in expected.items():
        panel = f"[RETRIEVED PANEL FOR THIS TURN]\nsequence: {sequence}\ntitle: Panel {sequence}"
        assert panel_explanation_gesture(panel) == gesture
