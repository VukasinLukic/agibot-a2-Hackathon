"""Commentator: confirmed event + snapshot -> one short line for the chosen persona.

Persona only changes wording. It has no score-write capability: it receives
facts (event, snapshot) and returns text. Names/roles are untrusted data and
are only ever substituted as values, never interpreted.
"""

from __future__ import annotations

from typing import Any, Optional

from table_tennis.contracts import MatchSnapshot
from table_tennis.core.rules import next_server

from .templates import TEMPLATES, score_sentence


def _name(snapshot: MatchSnapshot, player_id: Optional[str]) -> str:
    if not player_id:
        return ""
    return snapshot.player(player_id).display_name


class Commentator:
    def line_for(self, event: Any, snapshot: MatchSnapshot, persona: Optional[str] = None) -> Optional[str]:
        persona = persona or snapshot.persona
        t = TEMPLATES.get(persona, TEMPLATES["regular"])
        et = event.type
        p = event.payload

        if et == "match.started":
            return t["match.started"].format(server=_name(snapshot, snapshot.server_id))

        if et == "point.confirmed":
            new = p.new_score
            winner = _name(snapshot, p.winner_id)
            key = "point.confirmed"
            if persona == "corporate":
                key = self._corporate_key(snapshot, p.winner_id)
            parts = [t[key].format(winner=winner), score_sentence(new.p1, new.p2)]
            finished = snapshot.status == "finished" and snapshot.revision == event.revision
            if not finished:
                if new.p1 == new.p2 == snapshot.config.target_points - 1:
                    parts.append(t["deuce"])
                else:
                    leader = self._game_point_leader(new.p1, new.p2, snapshot)
                    if leader:
                        parts.append(t["game_point"].format(leader=_name(snapshot, leader)))
                prev = p.previous_score
                before = next_server(prev.p1, prev.p2, snapshot.first_server_id)
                after = next_server(new.p1, new.p2, snapshot.first_server_id)
                if after and before != after and not (min(new.p1, new.p2) >= snapshot.config.target_points - 1):
                    parts.append(t["server_change"].format(server=_name(snapshot, after)))
            return " ".join(parts)

        if et == "point.proposed":
            return t["point.proposed"].format(winner=_name(snapshot, p.proposal.winner_id))

        if et == "score.corrected":
            new = p.new_score
            if persona == "corporate":
                return f"{t['score.corrected']} {score_sentence(new.p1, new.p2)}"
            return f"{t['score.corrected']} Rezultat je {score_sentence(new.p1, new.p2).lower()}"

        if et == "rally.let":
            return t["rally.let"]

        if et == "match.finished":
            fs = p.final_score
            if p.winner_id:
                return f"{t['match.finished'].format(winner=_name(snapshot, p.winner_id))} {score_sentence(fs.p1, fs.p2)}"
            return f"{t['match.finished.none']} {score_sentence(fs.p1, fs.p2)}"

        if et == "persona.changed":
            return TEMPLATES.get(p.persona, TEMPLATES["regular"])["persona.changed"]

        return None

    @staticmethod
    def _game_point_leader(a: int, b: int, snapshot: MatchSnapshot) -> Optional[str]:
        target = snapshot.config.target_points
        if a >= target - 1 and a - b >= 1:
            return "p1"
        if b >= target - 1 and b - a >= 1:
            return "p2"
        return None

    @staticmethod
    def _corporate_key(snapshot: MatchSnapshot, winner_id: str) -> str:
        loser_id = "p2" if winner_id == "p1" else "p1"
        w = snapshot.player(winner_id).role_rank
        lo = snapshot.player(loser_id).role_rank
        if w is None or lo is None or w == lo:
            return "point.confirmed"
        return "point.confirmed.favored" if w > lo else "point.confirmed.underdog"
