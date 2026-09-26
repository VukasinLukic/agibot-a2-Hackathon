"""Deterministic referee lines (Serbian, latin script). No cloud, no LLM.

The mandatory score sentence is always built from the backend snapshot and is
separate from the optional joke, so humour can never change a number.
"""

from __future__ import annotations

_NUM = [
    "nula", "jedan", "dva", "tri", "četiri", "pet", "šest", "sedam", "osam", "devet", "deset",
    "jedanaest", "dvanaest", "trinaest", "četrnaest", "petnaest", "šesnaest", "sedamnaest",
    "osamnaest", "devetnaest", "dvadeset",
]


def number_words(n: int) -> str:
    if 0 <= n < len(_NUM):
        return _NUM[n]
    if 20 < n < 30:
        return "dvadeset " + _NUM[n - 20]
    return str(n)


def score_sentence(a: int, b: int) -> str:
    """'Četiri prema tri.' - always from the authoritative snapshot."""
    text = f"{number_words(a)} prema {number_words(b)}."
    return text[0].upper() + text[1:]


REGULAR = {
    "match.started": "Počinje meč. Servira {server}.",
    "point.confirmed": "Poen {winner}.",
    "point.confirmed.deuce": "Poen {winner}.",
    "deuce": "Servis se menja posle svakog poena.",
    "game_point": "Gem-poen: {leader}.",
    "server_change": "Servis preuzima {server}.",
    "point.proposed": "Nisam siguran ko je osvojio poen. Predlog: {winner}. Molim potvrdu.",
    "score.corrected": "Ispravka.",
    "rally.let": "Ponavlja se razmena.",
    "match.finished": "Kraj meča. Pobednik je {winner}.",
    "match.finished.none": "Meč je završen.",
    "persona.changed": "Prelazim na regularni režim komentarisanja.",
}

CORPORATE = {
    "match.started": "Otvaram sednicu. Servis otvara {server}.",
    "point.confirmed": "{winner} osvaja poen. Ovaj potez ide u kvartalni izveštaj.",
    "point.confirmed.favored": "Poen {winner}. Vizionarski, kao i uvek.",
    "point.confirmed.underdog": "Poen {winner}. Nadam se da ovo neće uticati na sledeći performance review.",
    "deuce": "Potrebna je odluka upravnog odbora, i dva poena razlike.",
    "game_point": "{leader} ima gem-poen. Pripremam saopštenje za medije.",
    "server_change": "Servis preuzima {server}. Delegiranje uspešno.",
    "point.proposed": "Organizaciona šema mi je jasna, ova loptica nije. Predlog: {winner}. Molim potvrdu poena.",
    "score.corrected": "Revizija je završena. Zvanično:",
    "rally.let": "Ova razmena ide na ponovno razmatranje.",
    "match.finished": "Sednica je zaključena. Pobednik je {winner}. Čestitke idu kroz zvanične kanale.",
    "match.finished.none": "Sednica je zaključena bez odluke.",
    "persona.changed": "Korporativni režim aktiviran. Svi poeni su jednaki, neki su jednakiji.",
}

TEMPLATES = {"regular": REGULAR, "corporate": CORPORATE}
