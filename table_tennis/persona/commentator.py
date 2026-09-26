"""Commentator: confirmed event + snapshot -> one short line for the chosen persona.

Persona only changes wording. It has no score-write capability: it receives
facts (event, snapshot) and returns text. Names/roles are untrusted data and
are only ever substituted as values, never interpreted.

Line shape for a point: "Poen <ime>. <rezultat>. [servis/gem-poen] [komentar]".
The score comes first and always from the event payload, so listeners can tell
the point from the joke. Every choice (variant, whether to joke, the corporate
"favourite") is a stable hash of match/event ids: the same event always yields
the same line, with no hidden per-process state, so restarts and replays agree.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from table_tennis.contracts import MatchSnapshot
from table_tennis.core.rules import next_server

from .joke_bank import JokeBank
from .roles import ROLES, role_key
from .templates import (
    CORPORATE_LOSE_BY_ROLE,
    CORPORATE_WIN_BY_ROLE,
    CORPORATE,
    TEMPLATES,
    score_sentence,
)

# Corporate persona jokes on roughly one ordinary point in N; key moments always get one.
CORPORATE_JOKE_EVERY = 3
# Tie announcements start from this score (earlier ties are not interesting).
TIE_FROM = 5
# "Big lead" comment when the lead first reaches this many points.
BIG_LEAD = 5
# Winner at least this many ranks below the loser counts as an upset.
UPSET_RANK_GAP = 2

_RANK_BY_KEY = {key: rank for key, _, rank in ROLES}


def _stable(*parts: Any) -> int:
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def _name(snapshot: MatchSnapshot, player_id: Optional[str]) -> str:
    if not player_id:
        return ""
    return snapshot.player(player_id).display_name


def _other(player_id: str) -> str:
    return "p2" if player_id == "p1" else "p1"


def favorite_player(match_id: str) -> str:
    """Corporate persona's randomly chosen favourite for this match (words only)."""
    return "p1" if _stable(match_id, "favorite") % 2 == 0 else "p2"


def _rank(snapshot: MatchSnapshot, player_id: str) -> Optional[int]:
    player = snapshot.player(player_id)
    if player.role_rank is not None:
        return player.role_rank
    key = role_key(player.role_label)
    return _RANK_BY_KEY.get(key) if key else None


def _game_point_leader(a: int, b: int, target: int) -> Optional[str]:
    if a >= target - 1 and a - b >= 1:
        return "p1"
    if b >= target - 1 and b - a >= 1:
        return "p2"
    return None


class Commentator:
    def __init__(self, jokes: Optional[JokeBank] = None):
        # Personalised LLM lines for the corporate persona (off unless TT_LLM_JOKES=1).
        self.jokes = jokes if jokes is not None else JokeBank.from_env(favorite_of=favorite_player, rank_of=_rank)

    def line_for(self, event: Any, snapshot: MatchSnapshot, persona: Optional[str] = None) -> Optional[str]:
        persona = persona or snapshot.persona
        if persona not in TEMPLATES:
            persona = "regular"
        t = TEMPLATES[persona]
        et = event.type
        p = event.payload

        if persona == "corporate" and et in ("match.started", "persona.changed"):
            self.jokes.ensure(snapshot)  # background; points never wait for it

        # Points rotate through variants in order (no line twice in a row); other events hash.
        seq = None
        if et in ("point.confirmed", "score.corrected"):
            seq = p.new_score.p1 + p.new_score.p2

        def pick(slot: str, variants: Optional[list[str]] = None, **values: str) -> str:
            options = variants if variants is not None else t[slot]
            if seq is None:
                index = _stable(event.match_id, event.event_id, slot)
            else:
                index = _stable(event.match_id, slot) + seq // 2  # ties and server changes come every 2nd point
            return options[index % len(options)].format(**values)

        if et == "match.started":
            server = _name(snapshot, snapshot.server_id)
            p1, p2 = _name(snapshot, "p1"), _name(snapshot, "p2")
            if persona == "corporate":
                return pick("match.started", intro=self._corporate_intro(snapshot), server=server)
            return pick("match.started", p1=p1, p2=p2, server=server)

        if et == "point.confirmed":
            return self._point_line(event, snapshot, persona, pick)

        if et == "point.proposed":
            return pick("point.proposed", winner=_name(snapshot, p.proposal.winner_id))

        if et == "score.corrected":
            new = p.new_score
            score = score_sentence(new.p1, new.p2)
            if persona == "regular":
                score = score[0].lower() + score[1:]
            return pick("score.corrected", score=score)

        if et == "rally.let":
            return pick("rally.let")

        if et == "match.finished":
            fs = p.final_score
            if p.winner_id:
                head = pick("match.finished", winner=_name(snapshot, p.winner_id), loser=_name(snapshot, _other(p.winner_id)))
                # Winner's points first: "Pobednik je Marko. Jedanaest prema devet."
                return f"{head} {score_sentence(fs.get(p.winner_id), fs.get(_other(p.winner_id)))}"
            return f"{pick('match.finished.none')} {score_sentence(fs.p1, fs.p2)}"

        if et == "persona.changed":
            target = TEMPLATES.get(p.persona, TEMPLATES["regular"])
            return pick("persona.changed", target["persona.changed"])

        if et == "readiness.changed":
            # Only the robot's own arrival/failure is spoken; camera/operator changes stay silent.
            if p.component != "robot_ready":
                return None
            slot = f"robot.{p.reason}"
            return pick(slot) if slot in t else None

        return None

    # ------------------------------------------------------------------ points

    def _point_line(self, event: Any, snapshot: MatchSnapshot, persona: str, pick) -> str:
        p = event.payload
        prev, new = p.previous_score, p.new_score
        winner_id = p.winner_id
        loser_id = _other(winner_id)
        winner, loser = _name(snapshot, winner_id), _name(snapshot, loser_id)
        target = snapshot.config.target_points

        parts = [pick("point", winner=winner), score_sentence(new.p1, new.p2)]
        finished = snapshot.status == "finished" and snapshot.revision == event.revision
        if finished:
            # match.finished follows with the winner announcement; keep this short.
            return " ".join(parts)

        deuce_phase = min(new.p1, new.p2) >= target - 1
        first_deuce = new.p1 == new.p2 == target - 1
        leader = _game_point_leader(new.p1, new.p2, target)
        prev_leader = _game_point_leader(prev.p1, prev.p2, target)
        saved = prev_leader is not None and prev_leader == loser_id
        tie = new.p1 == new.p2 and new.p1 >= TIE_FROM and not first_deuce

        # Informational extras (both personas): deuce rule, game point, server change.
        if first_deuce:
            parts.append(pick("deuce"))
        elif leader:
            parts.append(pick("game_point", leader=_name(snapshot, leader)))

        if persona == "corporate":
            comment = None if leader else self._corporate_comment(event, snapshot, pick, winner_id, loser_id, saved, tie, first_deuce)
        else:
            comment = self._regular_comment(pick, saved, tie)

        before = next_server(prev.p1, prev.p2, snapshot.first_server_id)
        after = next_server(new.p1, new.p2, snapshot.first_server_id)
        if after and before != after and not deuce_phase:
            server = _name(snapshot, after)
            # One joke per point is enough: with a comment, the server line stays plain.
            parts.append(f"Servira {server}." if comment else pick("server_change", server=server))
        if comment:
            parts.append(comment)
        return " ".join(parts)

    @staticmethod
    def _regular_comment(pick, saved: bool, tie: bool) -> Optional[str]:
        # Friendly referee: only a short warm word at the moments that deserve it.
        if saved:
            return pick("game_point_saved")
        if tie:
            return pick("tie")
        return None

    def _corporate_comment(
        self, event: Any, snapshot: MatchSnapshot, pick, winner_id: str, loser_id: str,
        saved: bool, tie: bool, first_deuce: bool,
    ) -> Optional[str]:
        p = event.payload
        prev, new = p.previous_score, p.new_score
        winner, loser = _name(snapshot, winner_id), _name(snapshot, loser_id)

        def bank(slot: str) -> Optional[list[str]]:
            return self.jokes.lines(event.match_id, slot)

        # Key moments always get one line, in priority order.
        if first_deuce:
            return None  # the deuce line is already the joke
        if saved:
            return pick("game_point_saved", bank("game_point_saved"))
        if new.p1 + new.p2 == 1:
            return pick("first_point", bank("first_point"))
        w_rank, l_rank = _rank(snapshot, winner_id), _rank(snapshot, loser_id)
        w_now, l_now = new.get(winner_id), new.get(loser_id)
        if (
            w_rank is not None and l_rank is not None and l_rank - w_rank >= UPSET_RANK_GAP
            and w_now > l_now and prev.get(winner_id) <= prev.get(loser_id)
        ):
            # The more junior player just took the lead.
            return pick("upset", bank("upset"))
        if tie:
            return pick("tie", bank("tie"))
        lead_now = abs(new.p1 - new.p2)
        lead_before = abs(prev.p1 - prev.p2)
        if lead_now == BIG_LEAD and lead_before < BIG_LEAD:
            return pick("big_lead", bank(f"big_lead_{winner_id}"), leader=winner)

        # Ordinary point: joke only now and then, so the game keeps its rhythm.
        if _stable(event.match_id, event.event_id, "joke") % CORPORATE_JOKE_EVERY != 0:
            return None
        # Pools: flattery of the random favourite, and teasing by company role.
        # Personalised LLM lines (if ready) replace the handwritten ones.
        pools: list[tuple[str, list[str]]] = []
        if winner_id == favorite_player(event.match_id):
            pools.append(("favorite_win", bank("favorite_win") or CORPORATE["favorite_win"]))
        else:
            pools.append(("favorite_lose", bank("favorite_lose") or CORPORATE["favorite_lose"]))
        w_key = role_key(snapshot.player(winner_id).role_label)
        win_lines = bank(f"win_{winner_id}") or CORPORATE_WIN_BY_ROLE.get(w_key or "")
        if win_lines:
            pools.append(("win_by_role", win_lines))
        l_key = role_key(snapshot.player(loser_id).role_label)
        lose_lines = bank(f"lose_{loser_id}") or CORPORATE_LOSE_BY_ROLE.get(l_key or "")
        if lose_lines:
            pools.append(("lose_by_role", lose_lines))
        if len(pools) == 1:
            pools.append(("generic_win", CORPORATE["generic_win"]))
        slot, variants = pools[_stable(event.match_id, event.event_id, "pool") % len(pools)]
        return pick(slot, variants, winner=winner, loser=loser)

    @staticmethod
    def _corporate_intro(snapshot: MatchSnapshot) -> str:
        names = []
        for player in snapshot.players:
            if player.role_label:
                names.append(f"{player.display_name}, {player.role_label},")
            else:
                names.append(player.display_name)
        text = f"Igraju: {names[0]} i {names[1]}"
        return text.rstrip(",") + "."
