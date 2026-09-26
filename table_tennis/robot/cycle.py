"""Referee cycle shared by the fake and A2 adapters.

The match score stays in the backend. A missing screen or gesture is reported
and is not presented as done. Navigation can be replaced by an explicit manual
arrival. An offline robot keeps the software demo on the fake adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FULL_CYCLE = (
    "call",
    "arrival_confirmed",
    "operator_ready",
    "greeting",
    "match_start",
    "score_display",
    "point_gesture",
    "speech",
    "ready_for_rally",
    "finished",
    "screen_release",
)


@dataclass(frozen=True)
class CycleInput:
    robot_online: bool = True
    nav_available: bool = True
    screen_available: bool = True
    gesture_available: bool = True
    score: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class CycleReport:
    steps: tuple[str, ...]
    score: tuple[int, int]
    score_kept: bool = True
    simulated: bool = False
    unavailable: tuple[str, ...] = field(default_factory=tuple)


def run_cycle(spec: CycleInput) -> CycleReport:
    steps: list[str] = []
    unavailable: list[str] = []
    if not spec.robot_online:
        steps.append("fake_adapter")
    if spec.nav_available and spec.robot_online:
        steps.extend(("call", "arrival_confirmed"))
    else:
        steps.append("manual_arrival")
        if spec.robot_online:
            unavailable.append("navigation")
    steps.append("operator_ready")
    if spec.gesture_available:
        steps.append("greeting")
    else:
        unavailable.append("gesture")
    steps.append("match_start")
    if spec.screen_available:
        steps.append("score_display")
    else:
        steps.extend(("screen_unavailable", "web_scoreboard"))
        unavailable.append("screen")
    if spec.gesture_available:
        steps.append("point_gesture")
    else:
        steps.append("gesture_failed")
    steps.extend(("speech", "ready_for_rally", "finished"))
    if spec.screen_available:
        steps.append("screen_release")
    return CycleReport(
        steps=tuple(steps),
        score=spec.score,
        score_kept=True,
        simulated=not spec.robot_online,
        unavailable=tuple(unavailable),
    )
