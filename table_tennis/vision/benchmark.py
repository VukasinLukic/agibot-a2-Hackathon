"""Score labeled rallies. The automatic flag stays off.

Tuning labels and final labels must be different rallies. The demo bar is
95% precision among automatic accepts and 80% coverage of simple rallies,
and only on a final set of at least 50 rallies from the real A2 table.
Meeting that bar does not turn automatic scoring on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from table_tennis.vision.capture import CaptureStats

AUTOMATIC_ENABLED = False
MIN_FINAL_RALLIES = 50
MIN_PRECISION = 0.95
MIN_SIMPLE_COVERAGE = 0.80
REQUIRED_TAGS = frozenset({"net", "occlusion", "fast_ball", "stop"})


@dataclass(frozen=True, slots=True)
class RallyLabel:
    rally_id: str
    split: str
    winner_id: str | None
    reason: str
    uncertain: bool
    simple: bool
    tags: tuple[str, ...]
    start_ns: int
    end_ns: int


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    source: str
    final_rallies: int
    precision: float | None
    simple_coverage: float | None
    abstention: float | None
    wrong_player: int
    duplicate_proposals: int
    latency_ms_p50: float | None
    latency_ms_p95: float | None
    capture_fps: float | None
    dropped_frames: int
    meets_demo_bar: bool
    automatic_enabled: bool


def evaluate(
    labels: Sequence[RallyLabel],
    decisions: Mapping[str, Sequence[dict[str, object] | None]],
    *,
    source: str = "synthetic",
    capture: CaptureStats | None = None,
    period_ns: int | None = None,
) -> BenchmarkReport:
    """Measure the final split. Thresholds are not fitted to those labels."""
    _require_split(labels)
    final = [label for label in labels if label.split == "final"]
    accepted = 0
    correct = 0
    simple = 0
    simple_hits = 0
    silent = 0
    wrong_player = 0
    duplicates = 0
    latencies: list[float] = []
    for label in final:
        commands = [item for item in decisions.get(label.rally_id, ()) if item is not None]
        if len(commands) > 1:
            duplicates += 1
        if not commands:
            silent += 1
            if label.simple:
                simple += 1
            continue
        payload = _payload(commands[0])
        accepted += 1
        if label.simple:
            simple += 1
            simple_hits += 1
        winner = payload.get("winner_id")
        if winner != label.winner_id:
            wrong_player += 1
        if (
            len(commands) == 1
            and not label.uncertain
            and winner == label.winner_id
            and payload.get("reason") == label.reason
        ):
            correct += 1
        latencies.append((label.end_ns - label.start_ns) / 1_000_000)
    precision = None if accepted == 0 else correct / accepted
    coverage = None if simple == 0 else simple_hits / simple
    abstention = None if not final else silent / len(final)
    tags = {tag for label in final for tag in label.tags}
    meets = (
        source == "a2"
        and len(final) >= MIN_FINAL_RALLIES
        and REQUIRED_TAGS <= tags
        and precision is not None
        and precision >= MIN_PRECISION
        and coverage is not None
        and coverage >= MIN_SIMPLE_COVERAGE
    )
    fps = None if period_ns is None or period_ns <= 0 else 1_000_000_000 / period_ns
    return BenchmarkReport(
        source=source,
        final_rallies=len(final),
        precision=precision,
        simple_coverage=coverage,
        abstention=abstention,
        wrong_player=wrong_player,
        duplicate_proposals=duplicates,
        latency_ms_p50=_percentile(latencies, 0.50),
        latency_ms_p95=_percentile(latencies, 0.95),
        capture_fps=fps,
        dropped_frames=0 if capture is None else capture.frames_dropped,
        meets_demo_bar=meets,
        automatic_enabled=AUTOMATIC_ENABLED,
    )


def _require_split(labels: Sequence[RallyLabel]) -> None:
    tune: set[str] = set()
    final: set[str] = set()
    for label in labels:
        if label.split not in {"tune", "final"}:
            raise ValueError("split must be 'tune' or 'final'")
        if label.end_ns < label.start_ns:
            raise ValueError("label end is before its start")
        bucket = tune if label.split == "tune" else final
        if label.rally_id in bucket or label.rally_id in tune or label.rally_id in final:
            raise ValueError("a rally cannot be in both splits")
        bucket.add(label.rally_id)


def _payload(command: dict[str, object]) -> dict[str, object]:
    payload = command.get("payload")
    if isinstance(payload, dict):
        return payload
    return command


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]
