"""Opt-in per-turn latency metrics without transcript or biometric payloads."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics


logger = logging.getLogger("voice-turn-metrics")


def _relative(timestamp: float, origin: float) -> float:
    return round(max(0.0, timestamp - origin), 6)


@dataclass
class _Turn:
    turn_id: str
    user_speech_end: float
    values: dict[str, Any] = field(default_factory=dict)
    agent_was_speaking: bool = False
    barge_in_started: float | None = None


class VoiceMetricsCollector:
    """Aggregate LiveKit events into one privacy-safe record per spoken turn."""

    def __init__(self, session: Any, emit: Callable[[dict[str, Any]], None] | None = None):
        self._session = session
        self._emit = emit or self._log_record
        self._turn: _Turn | None = None
        self._agent_state = "initializing"
        self._connection_seen = False
        session.on("user_state_changed", self._on_user_state_changed)
        session.on("user_input_transcribed", self._on_user_input_transcribed)
        session.on("metrics_collected", self._on_metrics_collected)
        session.on("agent_state_changed", self._on_agent_state_changed)
        session.on("agent_false_interruption", self._on_false_interruption)
        session.on("user_turn_exceeded", self._on_missed_endpoint)

    @staticmethod
    def _log_record(record: dict[str, Any]) -> None:
        logger.info(json.dumps(record, sort_keys=True, separators=(",", ":")))

    def _new_turn(self, ended_at: float) -> _Turn:
        if self._turn is not None:
            self._finish("superseded")
        self._turn = _Turn(
            turn_id=uuid.uuid4().hex,
            user_speech_end=ended_at,
            values={
                "stt_final": None,
                "llm_ttft": None,
                "llm_done": None,
                "tts_connect": None,
                "tts_first_byte": None,
                "first_audible_frame": None,
                "false_endpoints": 0,
                "missed_endpoints": 0,
                "barge_in_stop_latency": None,
                "tts_connection_reused": None,
                # The current bridge does not expose cumulative counters over
                # LiveKit. Keep these explicitly unknown instead of reporting
                # false zeroes; bridge instrumentation is a separate buffer task.
                "audio_underruns": None,
                "queue_drops": None,
                "max_queue_depth": None,
                "reconnects": 0,
            },
        )
        return self._turn

    def _on_user_state_changed(self, event: Any) -> None:
        now = float(getattr(event, "created_at", time.time()))
        if getattr(event, "old_state", None) == "speaking" and getattr(event, "new_state", None) == "listening":
            self._new_turn(now)
        elif getattr(event, "new_state", None) == "speaking" and self._agent_state == "speaking":
            if self._turn is not None:
                self._turn.barge_in_started = now

    def _on_user_input_transcribed(self, event: Any) -> None:
        if self._turn is not None and bool(getattr(event, "is_final", False)):
            self._turn.values["stt_final"] = _relative(
                float(getattr(event, "created_at", time.time())), self._turn.user_speech_end
            )

    def _on_metrics_collected(self, event: Any) -> None:
        turn = self._turn
        if turn is None:
            return
        metric = getattr(event, "metrics", event)
        if isinstance(metric, EOUMetrics):
            turn.values["stt_final"] = round(max(0.0, metric.transcription_delay), 6)
            turn.values["end_of_utterance"] = round(max(0.0, metric.end_of_utterance_delay), 6)
        elif isinstance(metric, STTMetrics):
            turn.values["stt_connect"] = round(max(0.0, metric.acquire_time), 6)
            turn.values["stt_connection_reused"] = bool(metric.connection_reused)
        elif isinstance(metric, LLMMetrics):
            turn.values["llm_ttft"] = _relative(metric.timestamp + metric.ttft, turn.user_speech_end)
            turn.values["llm_done"] = _relative(metric.timestamp + metric.duration, turn.user_speech_end)
        elif isinstance(metric, TTSMetrics):
            turn.values["tts_connect"] = round(max(0.0, metric.acquire_time), 6)
            turn.values["tts_first_byte"] = _relative(
                metric.timestamp + metric.acquire_time + metric.ttfb, turn.user_speech_end
            )
            turn.values["tts_connection_reused"] = bool(metric.connection_reused)
            if self._connection_seen and not metric.connection_reused:
                turn.values["reconnects"] += 1
            self._connection_seen = True

    def _on_agent_state_changed(self, event: Any) -> None:
        old_state = getattr(event, "old_state", self._agent_state)
        new_state = getattr(event, "new_state", self._agent_state)
        now = float(getattr(event, "created_at", time.time()))
        self._agent_state = new_state
        turn = self._turn
        if turn is None:
            return
        if new_state == "speaking" and turn.values["first_audible_frame"] is None:
            turn.agent_was_speaking = True
            turn.values["first_audible_frame"] = _relative(now, turn.user_speech_end)
        if new_state != "speaking" and turn.barge_in_started is not None:
            turn.values["barge_in_stop_latency"] = _relative(now, turn.barge_in_started)
        if old_state == "speaking" and new_state == "listening" and turn.agent_was_speaking:
            self._finish("completed")

    def _on_false_interruption(self, _event: Any) -> None:
        if self._turn is not None:
            self._turn.values["false_endpoints"] += 1

    def _on_missed_endpoint(self, _event: Any) -> None:
        if self._turn is not None:
            self._turn.values["missed_endpoints"] += 1

    def _finish(self, outcome: str) -> None:
        turn = self._turn
        if turn is None:
            return
        record = {
            "event": "voice_turn_metrics",
            "turn_id": turn.turn_id,
            "outcome": outcome,
            **turn.values,
        }
        self._turn = None
        self._emit(record)


def bind_voice_metrics(session: Any, *, enabled: bool) -> VoiceMetricsCollector | None:
    """Return immediately without listeners or awaits when metrics are disabled."""
    if not enabled:
        return None
    return VoiceMetricsCollector(session)
