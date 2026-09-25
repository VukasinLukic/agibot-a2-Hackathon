import sys
from pathlib import Path


CLIENT_ROOT = Path(__file__).resolve().parents[1] / "livekit-client"
if str(CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CLIENT_ROOT))

from rag.panel_continuity import PanelTourState, resolve_panel_followup


PANEL_TWO = "[RETRIEVED PANEL FOR THIS TURN]\nsequence: 2\ntitle: First Sparks"


def test_short_affirmation_reuses_the_active_panel_without_search():
    for query in ("Yes.", "Check it...", "Tell me more", "Go deeper"):
        resolved, reuse = resolve_panel_followup(query, PANEL_TWO)
        assert resolved == query
        assert reuse is True


def test_next_and_previous_become_deterministic_panel_queries():
    assert resolve_panel_followup("next", PANEL_TWO) == ("panel 3", False)
    assert resolve_panel_followup("Tell me about the next panel.", PANEL_TWO) == (
        "panel 3",
        False,
    )
    assert resolve_panel_followup("Take me to the next exhibit", PANEL_TWO) == (
        "panel 3",
        False,
    )
    assert resolve_panel_followup("Go to the next slide", PANEL_TWO) == (
        "panel 3",
        False,
    )
    assert resolve_panel_followup("go back", PANEL_TWO) == ("panel 1", False)
    assert resolve_panel_followup("Tell me about the previous panel", PANEL_TWO) == (
        "panel 1",
        False,
    )
    assert resolve_panel_followup("Previous slide", PANEL_TWO) == ("panel 1", False)
    assert resolve_panel_followup("last panel", PANEL_TWO) == ("panel 40", False)


def test_specific_question_still_uses_normal_retrieval():
    query = "What business connections did he build?"
    assert resolve_panel_followup(query, PANEL_TWO) == (query, False)
    assert resolve_panel_followup("yes", "unrelated context") == ("yes", False)


def test_contextual_fragment_reuses_last_result_without_locking_new_topics():
    assert resolve_panel_followup("In which countries?", PANEL_TWO) == (
        "In which countries?", True
    )
    assert resolve_panel_followup("Did you forget some country, maybe?", PANEL_TWO) == (
        "Did you forget some country, maybe?", True
    )
    gaming = "When did Comtrade enter the global gaming market?"
    assert resolve_panel_followup(gaming, PANEL_TWO) == (gaming, False)


def test_hall_persona_keeps_an_explicit_confirmed_panel_counter():
    state = PanelTourState(persona="hall_of_fame_tour")

    assert state.resolve("next one") == ("panel 1", False)
    assert state.remember_context("[RETRIEVED PANEL FOR THIS TURN]\nsequence: 1\ntitle: Beginnings")
    assert state.resolve("next one") == ("panel 2", False)
    assert state.remember_context(PANEL_TWO)
    assert not state.remember_context(PANEL_TWO)
    assert state.resolve("previous") == ("panel 1", False)
    assert state.remember_context("[RETRIEVED PANEL FOR THIS TURN]\nsequence: 1\ntitle: Beginnings")
    assert state.resolve("next") == ("panel 2", False)


def test_counter_moves_only_after_valid_rag_context_and_resets_with_persona():
    state = PanelTourState(persona="hall_of_fame_tour")
    assert state.remember_context(PANEL_TWO)

    assert not state.remember_context("")
    assert state.current_sequence == 2
    state.set_persona("comtrade_host")
    assert state.current_sequence is None
    assert state.last_context == ""
    assert state.resolve("next one") == ("next one", False)
    state.set_persona("hall_of_fame_tour")
    assert state.resolve("next one") == ("panel 1", False)


def test_controls_and_fillers_never_search_or_change_the_active_panel():
    state = PanelTourState(persona="hall_of_fame_tour")
    assert state.remember_context(PANEL_TWO)

    for query in ("Stop.", "Stop, stop, stop.", "Please stop", "Cancel", "Okay"):
        assert state.resolve(query) == ("", False)
        assert state.current_sequence == 2
    for query in ("This one", "This panel", "That slide"):
        assert state.resolve(query) == (query, True)
        assert state.current_sequence == 2
