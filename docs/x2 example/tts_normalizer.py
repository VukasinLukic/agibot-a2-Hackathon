"""Small TTS text normalizer for spoken Serbian/English output."""

from __future__ import annotations

import re
import unicodedata

from livekit_config.language_pack import load_abbreviations, load_units


_SR_ONES = {
    0: "nula",
    1: "jedan",
    2: "dva",
    3: "tri",
    4: "četiri",
    5: "pet",
    6: "šest",
    7: "sedam",
    8: "osam",
    9: "devet",
}
_SR_TEENS = {
    10: "deset",
    11: "jedanaest",
    12: "dvanaest",
    13: "trinaest",
    14: "četrnaest",
    15: "petnaest",
    16: "šesnaest",
    17: "sedamnaest",
    18: "osamnaest",
    19: "devetnaest",
}
_SR_TENS = {
    2: "dvadeset",
    3: "trideset",
    4: "četrdeset",
    5: "pedeset",
    6: "šezdeset",
    7: "sedamdeset",
    8: "osamdeset",
    9: "devedeset",
}
_SR_HUNDREDS = {
    1: "sto",
    2: "dvesta",
    3: "trista",
    4: "četiristo",
    5: "petsto",
    6: "šeststo",
    7: "sedamsto",
    8: "osamsto",
    9: "devetsto",
}
_SR_ORDINALS = {
    1: "prvi",
    2: "drugi",
    3: "treći",
    4: "četvrti",
    5: "peti",
    6: "šesti",
    7: "sedmi",
    8: "osmi",
    9: "deveti",
    10: "deseti",
}
_SR_ORDINAL_FEMININE_ENDINGS = {
    1: "prva", 2: "druga", 3: "treća", 4: "četvrta", 5: "peta",
    6: "šesta", 7: "sedma", 8: "osma", 9: "deveta", 10: "deseta",
    11: "jedanaesta", 12: "dvanaesta", 13: "trinaesta", 14: "četrnaesta",
    15: "petnaesta", 16: "šesnaesta", 17: "sedamnaesta",
    18: "osamnaesta", 19: "devetnaesta",
}

_DOMAIN_RE = re.compile(
    r"\b([\w-]{2,})\.(com|rs|si|net|org|eu|io|hr|ba)\b(/[\w/\-_.~%?=&#]*)?",
    re.IGNORECASE,
)
_CAPS_WHITELIST = {
    "AI",
    "API",
    "B2B",
    "CEO",
    "CPU",
    "CRM",
    "ERP",
    "GPU",
    "GB",
    "GHz",
    "ID",
    "IP",
    "IT",
    "KB",
    "LLM",
    "MB",
    "MHz",
    "PDF",
    "RSD",
    "SMS",
    "TB",
    "TV",
    "URL",
    "USD",
    "EUR",
}
_CAPS_RE = re.compile(r"\b([A-ZČĆŽŠĐ]{2,}(?:\s+[A-ZČĆŽŠĐ]{2,})*)\b")
_ABBREVIATIONS = tuple(
    (re.compile(rf"\b{pattern}", re.IGNORECASE), replacement)
    for pattern, replacement in load_abbreviations("sr")
)
_UNITS = load_units("sr")


def _sr_cardinal(n: int) -> str:
    if n < 0:
        return "minus " + _sr_cardinal(-n)
    if n in _SR_ONES:
        return _SR_ONES[n]
    if n in _SR_TEENS:
        return _SR_TEENS[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _SR_TENS[tens] if ones == 0 else f"{_SR_TENS[tens]} {_SR_ONES[ones]}"
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        base = _SR_HUNDREDS[hundreds]
        return base if rest == 0 else f"{base} {_sr_cardinal(rest)}"
    if n < 10000:
        thousands, rest = divmod(n, 1000)
        if thousands == 1:
            base = "hiljadu"
        elif thousands == 2:
            base = "dve hiljade"
        elif thousands in (3, 4):
            base = f"{_sr_cardinal(thousands)} hiljade"
        else:
            base = f"{_sr_cardinal(thousands)} hiljada"
        return base if rest == 0 else f"{base} {_sr_cardinal(rest)}"
    return " ".join(_SR_ONES[int(digit)] for digit in str(n))


def _sr_feminine_ordinal(n: int) -> str:
    if n in _SR_ORDINAL_FEMININE_ENDINGS:
        return _SR_ORDINAL_FEMININE_ENDINGS[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return (
            f"{_SR_TENS[tens]}a"
            if ones == 0
            else f"{_SR_TENS[tens]} {_SR_ORDINAL_FEMININE_ENDINGS[ones]}"
        )
    return f"{_sr_cardinal(n)}a"


def _sr_year_ordinal(year: int) -> str:
    prefix, ending = divmod(year, 100)
    if ending == 0:
        return _sr_feminine_ordinal(year)
    return f"{_sr_cardinal(prefix * 100)} {_sr_feminine_ordinal(ending)}"


# Matches a clock time, but not a digit group that merely contains a colon
# (ratios) or a decimal that continues past the minutes.
_TIME_RE = re.compile(r"(?<![\d:.,])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])(?![.,]\d)")


def _sr_hour_unit(hour: int) -> str:
    """Serbian noun that agrees with a whole-hour count (15 -> 'časova')."""

    if hour % 100 in range(11, 15):
        return "časova"
    last = hour % 10
    if last == 1:
        return "sat"
    if last in (2, 3, 4):
        return "sata"
    return "časova"


def _sr_time(match: re.Match[str]) -> str:
    hour = int(match.group(1))
    minute = int(match.group(2))
    if minute == 0:
        return f"{_sr_cardinal(hour)} {_sr_hour_unit(hour)}"
    return f"{_sr_cardinal(hour)} i {_sr_cardinal(minute)}"


def _expand_domain(match: re.Match[str]) -> str:
    host = match.group(1)
    tld = match.group(2)
    path = match.group(3)
    spoken = f"{host} tačka {tld}"
    if path:
        path_text = re.sub(r"[/_\-]", " ", path.lstrip("/")).strip()
        if path_text:
            spoken = f"{spoken} {path_text}"
    return spoken


def _lower_caps(match: re.Match[str]) -> str:
    token = match.group(1)
    words = token.split()
    if all(word in _CAPS_WHITELIST for word in words):
        return token
    return token.lower()


def _amount_replacement(unit: str):
    def replace(match: re.Match[str]) -> str:
        raw = match.group(1).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return f"{_sr_cardinal(int(float(raw)))} {unit}"
        except ValueError:
            return match.group(0)

    return replace


def _sr_decimal(match: re.Match[str]) -> str:
    return (
        f"{_sr_cardinal(int(match.group(1)))} zarez "
        + " ".join(_SR_ONES[int(digit)] for digit in match.group(2))
    )


def normalize_tts_text(text: str, language: str = "sr") -> str:
    if not text:
        return text

    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\u00a0\u202f\u2009\u2002\u2003\u2007\u2008\u200a\u3000]", " ", text)
    text = re.sub(r"\*{1,2}|_{1,2}|`", "", text)

    if not language.lower().startswith("sr"):
        return re.sub(r" {2,}", " ", text).strip()

    text = _TIME_RE.sub(_sr_time, text)
    text = _DOMAIN_RE.sub(_expand_domain, text)
    text = _CAPS_RE.sub(_lower_caps, text)

    text = re.sub(r"\b(\d+)\s*%", lambda m: f"{_sr_cardinal(int(m.group(1)))} posto", text)
    text = re.sub(
        r"(\d+)\s*°\s*C\b",
        lambda m: f"{_sr_cardinal(int(m.group(1)))} stepeni Celzijusa",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b((?:1[0-9]{3}|20[0-9]{2}))\.(?!\d)",
        lambda m: _sr_year_ordinal(int(m.group(1))),
        text,
    )
    text = re.sub(
        r"\b(\d{1,2})\.\s+(?=[^\d])",
        lambda m: f"{_SR_ORDINALS.get(int(m.group(1)), _sr_cardinal(int(m.group(1))))} ",
        text,
    )

    for symbol, unit in (("EUR", "evra"), ("€", "evra"), ("RSD", "dinara"), ("USD", "dolara")):
        text = re.sub(rf"(\d[\d\s.,]*)\s*{re.escape(symbol)}\b", _amount_replacement(unit), text, flags=re.IGNORECASE)
        text = re.sub(rf"{re.escape(symbol)}\s*(\d[\d\s.,]*)", _amount_replacement(unit), text, flags=re.IGNORECASE)

    for token, unit in _UNITS:
        text = re.sub(rf"\b(\d+),(\d+)\s*{re.escape(token)}\b", lambda m, u=unit: f"{_sr_decimal(m)} {u}", text, flags=re.IGNORECASE)
        text = re.sub(rf"(\d[\d\s]*)\s*{re.escape(token)}\b", _amount_replacement(unit), text, flags=re.IGNORECASE)

    text = re.sub(r"\b(\d+),(\d+)\b", _sr_decimal, text)

    text = re.sub(
        r"(?<![A-Za-zČĆŽŠĐčćžšđ\d])(\d+(?:[ \-]\d+)*)(?![A-Za-zČĆŽŠĐčćžšđ\d])",
        lambda m: " ".join(_SR_ONES[int(d)] for d in re.sub(r"[ \-]", "", m.group(1)))
        if len(re.sub(r"[ \-]", "", m.group(1))) >= 5
        else m.group(0),
        text,
    )
    text = re.sub(r"(?<![\d,])\b\d{1,4}\b(?![\d,])", lambda m: _sr_cardinal(int(m.group(0))), text)

    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)

    text = re.sub(r"(?<=\w)\s*/\s*(?=\w)", " na ", text)
    return re.sub(r" {2,}", " ", text).strip()
