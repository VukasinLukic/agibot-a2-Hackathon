"""Scoreboard text for the A2 head screen (pure, shared by fake and real display).

The existing A2 renderer accepts ~24 chars (primary) / 32 chars (secondary) and
strips most non-ASCII, so names are transliterated here (č->c, đ->dj, ...).
Full names stay in the UI and in speech.
"""

from __future__ import annotations

from table_tennis.contracts import MatchSnapshot

_TRANSLIT = str.maketrans(
    {
        "č": "c", "ć": "c", "š": "s", "ž": "z", "đ": "dj",
        "Č": "C", "Ć": "C", "Š": "S", "Ž": "Z", "Đ": "Dj",
    }
)

PRIMARY_MAX = 24
SECONDARY_MAX = 32


def screen_name(name: str, max_len: int) -> str:
    text = name.translate(_TRANSLIT)
    text = "".join(ch for ch in text if ch.isascii() and (ch.isalnum() or ch in " .-"))
    text = text.strip().upper() or "?"
    return text[:max_len]


def scoreboard_lines(snapshot: MatchSnapshot) -> tuple[str, str]:
    s = snapshot.score_by_player
    score = f"{s.p1} : {s.p2}"
    room = max(1, (PRIMARY_MAX - len(score) - 2) // 2)
    n1 = screen_name(snapshot.player("p1").display_name, room)
    n2 = screen_name(snapshot.player("p2").display_name, room)
    top = f"{n1} {score} {n2}"[:PRIMARY_MAX]
    if snapshot.status == "finished":
        if snapshot.winner_id:
            bottom = "POBEDA " + screen_name(snapshot.player(snapshot.winner_id).display_name, SECONDARY_MAX - 7)
        else:
            bottom = "KRAJ"
    elif snapshot.status == "paused":
        bottom = "PAUZA"
    elif snapshot.server_id:
        bottom = "SERVIS " + screen_name(snapshot.player(snapshot.server_id).display_name, SECONDARY_MAX - 7)
    else:
        bottom = ""
    return top, bottom[:SECONDARY_MAX]
