"""Real voice transport (Supervisor route) and LLM joke bank, without network or LLM."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from table_tennis.persona.commentator import Commentator, _rank, favorite_player
from table_tennis.persona.joke_bank import JokeBank, clean_line
from table_tennis.persona.speech import REFEREE_OFF, REFEREE_ON, LiveKitSpeechOutput, SupervisorCommandTransport
from table_tennis.persona.templates import score_sentence
from table_tennis.sim.driver import InProcessDriver
from table_tennis.sim.fixtures import make_runtime

PLAYERS = [
    {"id": "p1", "display_name": "Jelena", "role_label": "Direktor", "role_rank": 6},
    {"id": "p2", "display_name": "Stefan", "role_label": "Pripravnik", "role_rank": 1},
]
GAME = ["p1", "p2"] * 5 + ["p2", "p2", "p1", "p2", "p2", "p2", "p2", "p2"]


@pytest.fixture
def runtime(tmp_path):
    rt, ids = make_runtime(str(tmp_path))
    yield rt, ids
    rt.stop()


def play(runtime, speech, persona="corporate"):
    rt, ids = runtime
    rt.dispatcher.speech = speech
    d = InProcessDriver(rt, ids=ids)
    d.create(players=PLAYERS, scoring_mode="manual", persona=persona)
    d.ok("robot.ready.set", {"ready": True, "reason": "manual_arrival"}, expected_revision=None)
    d.ok("match.start")
    for w in GAME:
        d.point(w)
        if d.snapshot()["status"] == "finished":
            break
    d.settle()
    return d


# ------------------------------------------------------------------ voice


class _Recorder(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Recorder.received.append({"path": self.path, "json": json.loads(body), "auth": self.headers.get("Authorization")})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"success"}')

    def log_message(self, *args):
        pass


@pytest.fixture
def supervisor():
    _Recorder.received = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", _Recorder.received
    server.shutdown()


def test_real_voice_posts_exact_lines_and_referee_switches(runtime, supervisor, tmp_path):
    url, received = supervisor
    transport = SupervisorCommandTransport(url, token="t0k", timeout_s=3)
    no_llm = JokeBank(None, tmp_path, favorite_of=favorite_player, rank_of=_rank)
    speech = LiveKitSpeechOutput(Commentator(jokes=no_llm), send=transport, dry_run=False)
    play(runtime, speech)
    texts = [r["json"]["text"] for r in received]
    assert all(r["path"] == "/api/conversation/command" and r["auth"] == "Bearer t0k" for r in received)
    on = texts.index(f"{REFEREE_ON}:corporate")
    assert "Titan" in texts[on + 1]  # referee mode switches on right before the greeting
    assert texts[-1] == REFEREE_OFF
    assert any(t.startswith("Poen Jelena. " + score_sentence(1, 0)) for t in texts)
    assert texts == speech.sent


def test_unreachable_supervisor_raises_so_health_counts_it():
    transport = SupervisorCommandTransport("http://127.0.0.1:9", timeout_s=0.5)
    with pytest.raises(RuntimeError, match="unreachable|HTTP"):
        transport("Poen Ana.", {})


def test_dry_run_sends_nothing(runtime, supervisor):
    url, received = supervisor
    speech = LiveKitSpeechOutput(dry_run=True, send=SupervisorCommandTransport(url))
    play(runtime, speech, persona="regular")
    assert received == []
    assert f"{REFEREE_ON}:regular" in speech.sent


# ------------------------------------------------------------------ joke bank


class FakeChat:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def json_reply(self, system, user):
        self.calls += 1
        assert "Jelena" in user and "Direktor" in user and "Stefan" in user
        return self.reply


GOOD = {
    "favorite_win": ["Jelena, ovo ide pravo u godišnju ocenu."],
    "favorite_lose": ["Jelena, ništa strašno, sastanak se nastavlja."],
    "win_p1": ["Direktorski udarac, bez pitanja."],
    "win_p2": ["Stefan, pripravnik koji ne čeka odobrenje."],
    "lose_p1": ["Jelena, i direktori ponekad čekaju na odgovor."],
    "lose_p2": ["Stefan, i to je deo prakse."],
    "big_lead_p2": ["Stefan preuzima firmu, polako ali sigurno."],
    "upset": ["Stefan upravo traži sastanak kod direktora."],
    "tie": ["Nerešeno, kao svaki sastanak petkom."],
    "first_point": ["Radni dan je počeo."],
    # rejected: numbers / score-like / braces
    "game_point_saved": ["Spaseno 3 puta!", "Deset prema devet, sjajno.", "{winner} je car."],
}


def test_clean_line_rejects_numbers_and_scores():
    assert clean_line("Jelena, ovo ide u godišnju ocenu.") == "Jelena, ovo ide u godišnju ocenu."
    for bad in ("Vodi sa 3 poena", "Pet prema četiri", "Tri poena razlike", "{loser}", "", None, "x" * 200):
        assert clean_line(bad) is None


def test_personalised_lines_are_used_and_score_stays_first(runtime, tmp_path):
    chat = FakeChat(GOOD)
    jokes = JokeBank(chat, tmp_path / "jokes", favorite_of=favorite_player, rank_of=_rank, background=False)
    speech = LiveKitSpeechOutput(Commentator(jokes=jokes), dry_run=True)
    d = play(runtime, speech)
    assert chat.calls == 1
    assert (tmp_path / "jokes" / f"{d.match_id}.json").exists()
    personalised = {line for lines in GOOD.values() for line in lines if clean_line(line)}
    spoken = speech.sent
    assert any(any(p in s for p in personalised) for s in spoken)
    for s in spoken:
        assert "Spaseno 3" not in s and "{winner}" not in s
        if s.startswith("Poen "):
            # "Poen <ime>. <rezultat>." always comes before any joke
            assert " prema " in s.split(".")[1] + s.split(".")[2]


def test_llm_failure_falls_back_to_handwritten(runtime, tmp_path):
    class Broken:
        def json_reply(self, *a):
            raise TimeoutError("slow")

    jokes = JokeBank(Broken(), tmp_path / "jokes", favorite_of=favorite_player, rank_of=_rank, background=False)
    speech = LiveKitSpeechOutput(Commentator(jokes=jokes), dry_run=True)
    d = play(runtime, speech)
    assert d.snapshot()["status"] == "finished"
    assert len(speech.sent) > 5


def test_regular_persona_never_calls_llm(runtime, tmp_path):
    chat = FakeChat(GOOD)
    jokes = JokeBank(chat, tmp_path / "jokes", favorite_of=favorite_player, rank_of=_rank, background=False)
    play(runtime, LiveKitSpeechOutput(Commentator(jokes=jokes), dry_run=True), persona="regular")
    assert chat.calls == 0


def test_feature_is_off_by_default(monkeypatch):
    monkeypatch.delenv("TT_LLM_JOKES", raising=False)
    assert not JokeBank.from_env(favorite_of=favorite_player, rank_of=_rank).enabled
