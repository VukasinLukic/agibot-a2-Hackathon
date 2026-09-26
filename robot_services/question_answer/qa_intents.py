from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass
from .tell_time import get_time
from .tell_weather import get_weather



DEFAULT_CITY = "Belgrade"
DEFAULT_REGION = "Belgrade"
DEFAULT_UNITS = "METRIC"

# Multi-word phrases only: Serbian "vreme" means both "time" and "weather",
# so single-word triggers would make the two intents ambiguous.
TIME_PHRASES_EN = (
    "what time is it",
    "what's the time",
    "what is the time",
    "current time",
    "tell me the time",
    "do you know the time",
)
TIME_PHRASES_SR = (
    "koliko je sati",
    "koji je sat",
    "koje je tacno vreme",
    "koliko sati je",
)

WEATHER_PHRASES_EN = (
    "what's the weather",
    "what is the weather",
    "weather like",
    "how's the weather",
    "temperature outside",
    "is it raining",
    "how hot is it",
    "how cold is it",
)
WEATHER_PHRASES_SR = (
    "kakvo je vreme",
    "kakvo vreme je",
    "vremenska prognoza",
    "prognoza",
    "koliko je stepeni",
    "temperatura napolju",
    "da li pada kisa",
)

_IMPERIAL_MARKERS = ("fahrenheit", "imperial")

_LOCATION_RE = re.compile(r"\b(?:in|for|u|za)\s+([a-z\s]+)$")
_UNIT_PHRASE_RE = re.compile(r"\b(?:in\s+)?(?:fahrenheit|celsius|imperial|metric)(?:\s+units?)?\b")
_TRAILING_FILLERS = (
    "right now",
    "please",
    "today",
    "now",
    "currently",
    "molim",
    "sada",
    "danas",
    "trenutno",
    "odmah",
)


def normalize_intent_text(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text).lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _strip_trailing_fillers(location: str) -> str:
    changed = True
    while changed:
        changed = False
        for filler in _TRAILING_FILLERS:
            if location == filler:
                return ""
            suffix = f" {filler}"
            if location.endswith(suffix):
                location = location[: -len(suffix)].strip()
                changed = True
    return location


def _extract_location(normalized: str) -> str | None:
    # Strip unit phrases first so "in Paris in fahrenheit" doesn't get captured
    # as one location when the sentence contains two "in" clauses.
    without_units = re.sub(r"\s+", " ", _UNIT_PHRASE_RE.sub(" ", normalized)).strip()
    match = _LOCATION_RE.search(without_units)
    if not match:
        return None
    location = _strip_trailing_fillers(match.group(1).strip())
    return location or None


def _match_phrase(normalized: str, phrases: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"(?:^|\s){re.escape(normalize_intent_text(phrase))}(?:$|\s)", normalized)
        for phrase in phrases
    )


@dataclass(frozen=True)
class QAAnswer:
    kind: str  # "time" or "weather"
    text_en: str
    text_sr: str
    display_primary: str = ""    # Big text for the head-screen flash, e.g. "10 30" or "22C".
    display_secondary: str = ""  # Smaller label above it, e.g. the city/region name.


def match_time_query(text: str) -> QAAnswer | None:
    normalized = normalize_intent_text(text)
    if not normalized or not _match_phrase(normalized, TIME_PHRASES_EN + TIME_PHRASES_SR):
        return None

    location = _extract_location(normalized)
    requested_city = location.title().replace(" ", "_") if location else None
    city = requested_city or DEFAULT_CITY
    result = get_time(city)

    if result is None and requested_city is not None:
        # Unknown city: fall back to the default city instead of saying nothing.
        result = get_time(DEFAULT_CITY)
        if result is None:
            return None
        hour, minute, _written = result
        spoken_requested = requested_city.replace("_", " ")
        return QAAnswer(
            kind="time",
            text_en=(
                f"I don't know a city called {spoken_requested}, so here's the "
                f"time in {DEFAULT_CITY} instead: {hour} {minute}."
            ),
            text_sr=(
                f"Ne poznajem grad {spoken_requested}, pa evo vremena u gradu "
                f"{DEFAULT_CITY}: {hour} {minute}."
            ),
            display_primary=f"{hour} {minute}",
            display_secondary=DEFAULT_CITY,
        )

    if result is None:
        return None

    hour, minute, _written = result
    spoken_city = city.replace("_", " ")
    return QAAnswer(
        kind="time",
        text_en=f"It is currently {hour} {minute} in {spoken_city}.",
        text_sr=f"Trenutno je {hour} {minute} u gradu {spoken_city}.",
        display_primary=f"{hour} {minute}",
        display_secondary=spoken_city,
    )


async def match_weather_query(text: str) -> QAAnswer | None:
    normalized = normalize_intent_text(text)
    if not normalized or not _match_phrase(normalized, WEATHER_PHRASES_EN + WEATHER_PHRASES_SR):
        return None

    location = _extract_location(normalized)
    region = location.title() if location else DEFAULT_REGION
    units = "IMPERIAL" if any(marker in normalized for marker in _IMPERIAL_MARKERS) else DEFAULT_UNITS

    result = await get_weather(region, units)
    if result is None:
        return None
    resolved_region, temperature = result
    unit_symbol = "°F" if units == "IMPERIAL" else "°C"
    return QAAnswer(
        kind="weather",
        text_en=f"It is currently {temperature}{unit_symbol} in {resolved_region}.",
        text_sr=f"Trenutno je {temperature}{unit_symbol} u gradu {resolved_region}.",
        display_primary=f"{temperature}{unit_symbol}",
        display_secondary=resolved_region,
    )


async def match_qa_intent(text: str) -> QAAnswer | None:
    """Try the time intent first, then weather; None if neither matched."""
    time_answer = match_time_query(text)
    if time_answer is not None:
        return time_answer
    return await match_weather_query(text)
