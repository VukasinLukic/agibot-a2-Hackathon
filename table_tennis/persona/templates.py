"""Deterministic referee lines (Serbian, latin script). No cloud, no LLM.

The mandatory score sentence is always built from the backend snapshot and is
separate from the optional comment, so humour can never change a number. Each
slot holds a list of variants; the commentator picks one with a stable hash of
the match/event ids, so the same event always gets the same line (replay-safe).

Placeholders are filled with ``str.format`` and plain values only: names and
roles are untrusted user data and are never interpreted.

Regular: a friendly, cheerful referee; short, neutral, equally warm to both.
The robot is called Titan and speaks of itself in the masculine; players are
addressed only with gender-neutral forms (present tense, no gendered adjectives),
because the app never knows or guesses their gender.
Corporate: a chosen fun mode that teases both players about their (voluntarily
entered) company role and flatters one randomly chosen "favourite" - in words
only, never in the score.
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


REGULAR: dict[str, list[str]] = {
    "match.started": [
        "Dobar dan! Ja sam Titan i danas sudim. Igraju {p1} i {p2}. Prvi servira {server}. Srećno!",
        "Zdravo svima! Titan je spreman. Na stolu su {p1} i {p2}, igra se do jedanaest. Servira {server}.",
    ],
    "point": ["Poen {winner}."],
    "server_change": ["Servira {server}.", "Na servisu je {server}."],
    "deuce": ["Sada treba dva poena razlike, a servis se menja posle svakog poena."],
    "tie": ["Izjednačeno!", "Potpuno izjednačeno, lepa borba."],
    "game_point": ["Gem-poen: {leader}.", "{leader} ima gem-poen."],
    "game_point_saved": ["Gem-poen odbranjen!", "Odlično odbranjeno!"],
    "point.proposed": [
        "Ovaj poen nisam dobro video. Mislim da je poen {winner}. Potvrdite, molim vas.",
        "Nisam siguran ko je osvojio poen. Predlog: {winner}. Molim potvrdu.",
    ],
    "point.proposed.sure": [
        "Poen {winner}. Potvrdite, molim vas.",
        "Video sam: poen {winner}. Molim potvrdu.",
    ],
    "point.unclear": [
        "Nisam video kraj poena. Ko je osvojio poen?",
        "Ovaj poen mi je promakao. Ko je dobio poen?",
    ],
    "score.corrected": ["Ispravka. Rezultat je {score}"],
    "rally.let": ["Ponavljamo poen.", "Let. Igramo ponovo."],
    "match.finished": [
        "Kraj meča! Pobednik je {winner}. Čestitam, i hvala oboma na lepoj igri!",
        "I to je to! Pobeđuje {winner}. Bravo, i hvala oboma na odličnom meču!",
    ],
    "match.finished.none": ["Meč je završen."],
    "persona.changed": ["Prelazim na regularni režim. Vraćamo se igri!"],
    # readiness.changed for robot_ready, by reason
    "robot.robot_arrived": ["Stigao sam do stola. Spreman sam!"],
    "robot.manual_arrival": ["Tu sam i spreman sam za meč."],
    "robot.robot_call_failed": ["Nisam uspeo da stignem do stola. Potrebna mi je pomoć operatera."],
    "robot.robot_call_cancelled": ["U redu, poziv je otkazan."],
}

CORPORATE: dict[str, list[str]] = {
    "match.started": [
        "Dobar dan, kolege. Ja sam Titan i danas vodim ovaj sastanak. {intro} Prvi servira {server}.",
        "Otvaram sastanak. Tačka jedan dnevnog reda: stoni tenis. {intro} Servira {server}.",
    ],
    "point": ["Poen {winner}."],
    "server_change": ["Servira {server}.", "Na servisu je {server}."],
    "deuce": [
        "Sastanak se produžava. Niko ne ide kući dok neko ne povede sa dva poena razlike.",
        "Kao pregovori o plati: dugo i neizvesno. Treba dva poena razlike.",
    ],
    "tie": [
        "Izjednačeno. Kao u svakom dobrom timu.",
        "Izjednačeno. Nema dogovora, biće još jedan sastanak.",
    ],
    "game_point": [
        "{leader} ima gem-poen. Već pišem mejl sa čestitkom.",
        "Gem-poen: {leader}. Rok se bliži.",
    ],
    "game_point_saved": [
        "Gem-poen odbranjen! Rok je produžen.",
        "Spaseno u poslednjem trenutku, kao svaki projekat.",
    ],
    "first_point": [
        "Prvi poen je tu. Radni dan je zvanično počeo.",
        "Prvi poen meča. Kafa može da sačeka.",
    ],
    "big_lead": [
        "{leader} vodi ubedljivo. Ovo već liči na unapređenje.",
        "{leader} ne popušta. Neko ovde ozbiljno radi prekovremeno.",
    ],
    "favorite_win": [
        "Nisam pristrasan, ali ovo je bio najlepši poen dana.",
        "Bravo! Ovo pamtim za godišnju ocenu.",
        "Tako se to radi.",
    ],
    "favorite_lose": [
        "{loser}, ne brini, i dalje navijam za tebe.",
        "Hm. Ovo nije bilo po planu.",
    ],
    "upset": [
        "Ovo će biti zanimljiv razgovor u liftu.",
        "Hijerarhija se malo ljulja.",
        "Pitanje za sve prisutne: ko je ovde šef?",
    ],
    "generic_win": [
        "Poen kao iz udžbenika.",
        "Lepo, nema šta.",
        "Ovo zaslužuje bonus.",
    ],
    "point.proposed": [
        "Ovaj poen nisam dobro video, a ne volim da nagađam. Mislim da je poen {winner}. Potvrdite, molim vas.",
        "Za ovaj poen treba mi potpis. Predlog: {winner}. Molim potvrdu.",
    ],
    "point.proposed.sure": [
        "Po mom izveštaju, poen {winner}. Molim potpis.",
        "Poen {winner}, uredno zabeleženo. Potvrdite, molim vas.",
    ],
    "point.unclear": [
        "Ovaj poen nije ušao u izveštaj. Ko je osvojio poen?",
        "Kamera je bila na pauzi za kafu. Ko je dobio poen?",
    ],
    "score.corrected": [
        "Ispravka iz računovodstva. Zvanično: {score}",
        "Greška u izveštaju je ispravljena. Zvanično: {score}",
    ],
    "rally.let": [
        "Ponavljamo poen. Nije greška, nego iteracija.",
        "Let. Ovaj poen ide na doradu.",
    ],
    "match.finished": [
        "Kraj sastanka. Pobednik je {winner}. Čestitam!",
        "I to je to. Pobeđuje {winner}. Bonus je u obradi, rok isplate nepoznat.",
        "Pobeđuje {winner}. Po staroj kancelarijskoj tradiciji, {loser} časti kafu.",
    ],
    "match.finished.none": ["Sastanak je završen bez odluke."],
    "persona.changed": ["Uključujem korporativni režim. Svi poeni su jednaki, ali neki su jednakiji."],
    "robot.robot_arrived": ["Stigao sam na sastanak. Na vreme, za promenu."],
    "robot.manual_arrival": ["Tu sam. Neko me je doneo na sastanak, baš lepo."],
    "robot.robot_call_failed": ["Zaglavio sam se negde između stolova. Potrebna mi je pomoć operatera."],
    "robot.robot_call_cancelled": ["Sastanak je otkazan. Vraćam se u kancelariju."],
}

# Role-specific teasing (keys from roles.ROLES). Friendly office humour only.
CORPORATE_WIN_BY_ROLE: dict[str, list[str]] = {
    "intern": [
        "Pripravnik, a igra kao da ima ugovor na neodređeno.",
        "Ovako se dobija stalni posao.",
    ],
    "junior": [
        "Junior, a igra kao senior. Neko ovde uskoro traži povišicu.",
        "Mladost i znanje, dobra kombinacija.",
    ],
    "senior": [
        "Iskustvo se vidi.",
        "Rutina. Ovo se radilo i u prošloj firmi.",
    ],
    "lead": [
        "Tim može da bude miran, vođa je u formi.",
        "Poen je lični, zasluge su timske.",
    ],
    "manager": [
        "Plan ispunjen, tabela zelena.",
        "Ovo ide na sledeći sastanak kao primer dobre prakse.",
    ],
    "director": [
        "Direktorski potez. Bez mnogo priče.",
        "Strategija radi.",
    ],
    "ceo": [
        "Firma je u sigurnim rukama.",
        "Akcije rastu.",
    ],
}

CORPORATE_LOSE_BY_ROLE: dict[str, list[str]] = {
    "intern": ["{loser}, ne brini, praksa služi da se uči."],
    "junior": ["{loser}, i ovo je iskustvo. Doduše, neplaćeno."],
    "senior": ["{loser}, ovo ćemo nazvati tehničkim dugom."],
    "lead": ["{loser}, o ovome ćemo na retrospektivi."],
    "manager": ["{loser}, ovo ne ulazi u izveštaj. Obećavam."],
    "director": ["{loser}, nema veze, tim je kriv."],
    "ceo": ["{loser}, samo da podsetim, ja sam samo robot. Nemojte me otpustiti."],
}

TEMPLATES = {"regular": REGULAR, "corporate": CORPORATE}
