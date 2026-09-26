"""Voice agent referee mode (livekit-client/referee_mode.py): no LiveKit, no network."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "livekit-client"))

from referee_mode import RefereeMode, describe_match, parse_referee_command  # noqa: E402

SNAP = {
    "status": "between_rallies",
    "players": [
        {"id": "p1", "display_name": "Jelena", "role_label": "Direktor"},
        {"id": "p2", "display_name": "Stefan", "role_label": None},
    ],
    "score_by_player": {"p1": 4, "p2": 6},
    "server_id": "p1",
    "winner_id": None,
}


def test_parse_commands():
    assert parse_referee_command("__REFEREE_ON__:corporate") == (True, "corporate")
    assert parse_referee_command("__referee_on__:regular") == (True, "regular")
    assert parse_referee_command("__REFEREE_ON__") == (True, "regular")
    assert parse_referee_command("__REFEREE_ON__:hacker") == (True, "regular")
    assert parse_referee_command("__REFEREE_OFF__") == (False, None)
    assert parse_referee_command("Poen Ana. Jedan prema nula.") is None


def test_describe_match_uses_backend_facts_only():
    text = describe_match(SNAP)
    assert "Jelena 4, Stefan 6" in text and "Vodi: Stefan" in text and "Servira: Jelena" in text
    assert "Jelena je u firmi: Direktor" in text
    assert describe_match(None).startswith("Trenutno nema")


@pytest.mark.parametrize("persona,marker", [("regular", "TITAN SUDIJA"), ("corporate", "TITAN KORPORATIVNI SUDIJA")])
def test_instructions_load_persona_from_catalog(persona, marker):
    mode = RefereeMode()
    mode.set(True, persona)
    text = mode.instructions("Titan")
    assert marker in text and "get_table_tennis_match" in text and "{robot_name}" not in text


def test_silent_only_during_rally(monkeypatch):
    mode = RefereeMode()
    calls = {"n": 0}

    def fake_get(path):
        calls["n"] += 1
        return ["m1"] if path == "/matches" else dict(SNAP, status=status["value"])

    status = {"value": "rally"}
    monkeypatch.setattr(mode, "_get", fake_get)
    assert not mode.should_stay_silent()  # inactive: never silent
    mode.set(True, "regular")
    assert mode.should_stay_silent()
    status["value"] = "between_rallies"
    mode._cache = (0.0, None)
    assert not mode.should_stay_silent()
    mode.set(False, None)


def test_unreachable_backend_never_blocks(monkeypatch):
    mode = RefereeMode()
    mode.set(True, "corporate")

    def boom(path):
        raise OSError("down")

    monkeypatch.setattr(mode, "_get", boom)
    assert mode.current_match() is None
    assert not mode.should_stay_silent()
    mode.set(False, None)


def test_silence_check_reads_the_cache_not_the_network(monkeypatch):
    """With the poller running, a hung backend never delays a reply."""
    mode = RefereeMode()

    def slow_get(path):
        time.sleep(0.5)
        raise OSError("hung")

    monkeypatch.setattr(mode, "_get", slow_get)
    mode.set(True, "regular")
    try:
        mode._cache = (time.monotonic(), dict(SNAP, status="rally"))
        started = time.monotonic()
        assert mode.should_stay_silent()
        # a stale snapshot is ignored instead of refetched inline
        mode._cache = (time.monotonic() - mode.stale_s - 1, dict(SNAP, status="rally"))
        assert not mode.should_stay_silent()
        assert time.monotonic() - started < 0.05
    finally:
        mode.set(False, None)
    poller = mode._poller
    assert poller is not None
    poller.join(timeout=2)
    assert not poller.is_alive()


def test_quick_off_on_keeps_a_poller(monkeypatch):
    mode = RefereeMode()
    monkeypatch.setattr(mode, "_get", lambda path: [] if path == "/matches" else None)
    mode.set(True, "regular")
    mode.set(False, None)
    mode.set(True, "regular")
    try:
        assert mode._poller is not None and mode._poller.is_alive() and not mode._stop.is_set()
    finally:
        mode.set(False, None)
