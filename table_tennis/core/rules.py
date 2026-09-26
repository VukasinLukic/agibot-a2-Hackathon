"""Pure table tennis scoring rules for one singles game (contract v1, section 7)."""

from __future__ import annotations

from typing import Optional

from table_tennis.contracts.primitives import PLAYER_IDS


def other_player(player_id: str) -> str:
    return "p2" if player_id == "p1" else "p1"


def game_winner(p1: int, p2: int, target_points: int = 11, win_by: int = 2) -> Optional[str]:
    """Winner id when the game is over, else None. 11:10 is not over; 12:10 is."""
    if p1 < 0 or p2 < 0:
        raise ValueError("score cannot be negative")
    if max(p1, p2) >= target_points and abs(p1 - p2) >= win_by:
        return "p1" if p1 > p2 else "p2"
    return None


def next_server(p1: int, p2: int, first_server_id: str, target_points: int = 11, win_by: int = 2) -> Optional[str]:
    """Server of the next rally, or None when the game is finished.

    Before deuce (target-1 : target-1, i.e. 10:10) the service alternates every
    two points; from deuce on it alternates after every point.
    """
    if first_server_id not in PLAYER_IDS:
        raise ValueError(f"unknown player {first_server_id!r}")
    if game_winner(p1, p2, target_points, win_by) is not None:
        return None
    total = p1 + p2
    deuce_total = 2 * (target_points - 1)
    if min(p1, p2) >= target_points - 1:
        first_keeps = (total - deuce_total) % 2 == 0
    else:
        first_keeps = (total // 2) % 2 == 0
    return first_server_id if first_keeps else other_player(first_server_id)
