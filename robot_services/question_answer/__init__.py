"""Deterministic question-answering services (time, weather) for Robot Supervisor."""

from __future__ import annotations

from .qa_intents import QAAnswer, match_qa_intent, match_time_query, match_weather_query
from .tell_time import get_time
from .tell_weather import get_weather

__all__ = [
    "QAAnswer",
    "get_time",
    "get_weather",
    "match_qa_intent",
    "match_time_query",
    "match_weather_query",
]
