"""Explainable ready / busy / failed verdict before any A2 motion.

The inputs are the same facts ``a2_nav.preflight`` and ``a2_nav.doctor`` already
read (emergency stop, walking action, localization, working map, fresh pose).
This module does not import ``robot_services`` and does not open a network
connection. A later explicit real-mode reader may fill ``NavFacts`` from
``a2_nav``; mock mode never constructs that reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# Same tokens as a2_nav.WALK_ACTIONS. Kept here so mock tests do not import the
# navigation client. In any other MC action the planner can report RUNNING while
# the legs never step.
_WALK_ACTION_TOKENS = ("LOCOMOTION", "NAVIGATION")

ReadinessState = Literal["ready", "busy", "failed"]


def action_can_walk(action: str) -> bool:
    return any(token in action for token in _WALK_ACTION_TOKENS)


@dataclass(frozen=True)
class NavFacts:
    """One snapshot of navigation preflight. Defaults describe a free, walk-ready robot."""

    emergency_stop: bool = False
    call_active: bool = False
    work_enabled: bool = True
    collision: bool = False
    mc_action: str = "McAction_RL_LOCOMOTION_DEFAULT"
    localization_running: bool = True
    map_id: Optional[str | int] = "1"
    pose_age_ms: Optional[int] = 0
    max_pose_age_ms: int = 5000


@dataclass(frozen=True)
class NavReadiness:
    """Navigation verdict. Not the match ``Readiness`` flags in the shared contract."""

    state: ReadinessState
    reason: Optional[str] = None


def _map_missing(map_id: Optional[str | int]) -> bool:
    if map_id is None:
        return True
    text = str(map_id).strip()
    if not text:
        return True
    try:
        return int(text) == 0
    except ValueError:
        return False


def assess(facts: NavFacts) -> NavReadiness:
    """First matching condition wins, in the same order a preflight would stop.

    An asserted E-stop is reported before a busy call: the robot cannot be
    treated as merely occupied while the stop is held.
    """

    if facts.emergency_stop:
        return NavReadiness("failed", "emergency_stop")
    if facts.call_active:
        return NavReadiness("busy", "robot_busy")
    if not facts.work_enabled:
        return NavReadiness("failed", "not_enabled")
    if facts.collision:
        return NavReadiness("failed", "collision")
    if not action_can_walk(facts.mc_action):
        return NavReadiness("failed", "cannot_walk")
    if not facts.localization_running:
        return NavReadiness("failed", "localization_off")
    if _map_missing(facts.map_id):
        return NavReadiness("failed", "no_map")
    if facts.pose_age_ms is None or facts.pose_age_ms > facts.max_pose_age_ms:
        return NavReadiness("failed", "stale_pose")
    return NavReadiness("ready")
