"""Arrival is not the same thing as an accepted navigation RPC.

A call may become ``ready`` only when the task id matches, the pose is fresh,
the robot is inside the waypoint tolerance, and it has stopped. Missing
telemetry stays at ``arrived`` with ``need_operator_confirmation``. The operator
then marks manual arrival. This module does not read the robot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

ArrivalState = Literal["ready", "arrived", "failed"]


@dataclass(frozen=True)
class ArrivalFacts:
    """What a navigator observed after the planner accepted the goal.

    ``None`` on a measurement means the telemetry is missing, not that the
    check passed. ``expected_task_id`` is the id stored when the mission
    started. ``0`` is never a real task id.
    """

    expected_task_id: Optional[str] = None
    observed_task_id: Optional[str] = None
    pose_age_ms: Optional[int] = None
    within_tolerance: Optional[bool] = None
    settled: Optional[bool] = None
    max_pose_age_ms: int = 5000


@dataclass(frozen=True)
class ArrivalVerdict:
    state: ArrivalState
    reason: str


def assess_arrival(facts: ArrivalFacts) -> ArrivalVerdict:
    expected = _task_id(facts.expected_task_id)
    observed = _task_id(facts.observed_task_id)
    if expected and observed and expected != observed:
        return ArrivalVerdict("failed", "native_task_mismatch")
    if (
        observed is None
        or facts.pose_age_ms is None
        or facts.within_tolerance is None
        or facts.settled is None
    ):
        return ArrivalVerdict("arrived", "need_operator_confirmation")
    if facts.pose_age_ms > facts.max_pose_age_ms:
        return ArrivalVerdict("arrived", "stale_pose")
    if not facts.within_tolerance:
        return ArrivalVerdict("arrived", "outside_tolerance")
    if not facts.settled:
        return ArrivalVerdict("arrived", "not_settled")
    return ArrivalVerdict("ready", "arrival_confirmed")


def _task_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "0":
        return None
    return text
