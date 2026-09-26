"""Table tennis referee mode for the voice agent (TitanSudija, owner: person 4).

While a match runs, the table tennis backend switches the agent into referee
mode with a control command on the normal command stream:

    __REFEREE_ON__:regular | __REFEREE_ON__:corporate   (match start / persona change)
    __REFEREE_OFF__                                     (match end)

In referee mode the agent:
  * talks in the TitanSudija persona (``titan_sudija`` / ``titan_korporativni_sudija``
    from ``livekit_config/prompts/personas.yaml``, the same catalog the
    Supervisor's persona manager edits);
  * reads the live score only through the ``get_table_tennis_match`` tool,
    which is read-only (the persona can never change the score);
  * stays silent while a rally is being played, so it never talks over the game;
  * does not trigger gestures on its own (the referee gestures belong to the
    robot adapter).

The backend is reached over plain HTTP on the same machine; a slow or missing
backend only means "no match info", never a blocked conversation.

Env:
  TT_API_URL          table tennis backend (default http://127.0.0.1:8099)
  TT_PERSONA_TOKEN    bearer token when the backend runs in token auth mode
  TT_REFEREE_TIMEOUT_S  per-request timeout (default 0.8)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger("referee-mode")

REFEREE_ON = "__REFEREE_ON__"
REFEREE_OFF = "__REFEREE_OFF__"
PERSONA_SLUGS = {"regular": "titan_sudija", "corporate": "titan_korporativni_sudija"}
PERSONAS_FILE = Path(__file__).resolve().parents[1] / "livekit_config" / "prompts" / "personas.yaml"
_STATUS_TEXT = {
    "setup": "priprema, meč još nije počeo",
    "between_rallies": "pauza između poena",
    "rally": "poen je u toku",
    "pending_decision": "čeka se potvrda poena",
    "paused": "meč je pauziran",
    "finished": "meč je završen",
}


def parse_referee_command(text: str) -> Optional[tuple[bool, Optional[str]]]:
    """'__REFEREE_ON__:corporate' -> (True, 'corporate'); '__REFEREE_OFF__' -> (False, None); else None."""
    raw = text.strip()
    if raw.upper() == REFEREE_OFF:
        return False, None
    if raw.upper().startswith(REFEREE_ON):
        persona = raw[len(REFEREE_ON):].lstrip(":").strip().lower() or "regular"
        return True, persona if persona in PERSONA_SLUGS else "regular"
    return None


def _persona_text(slug: str, robot_name: str) -> str:
    try:
        import yaml

        data = yaml.safe_load(PERSONAS_FILE.read_text(encoding="utf-8")) or {}
        entry = data.get(slug)
        text = entry.get("prompt_text") if isinstance(entry, dict) else entry
        if isinstance(text, str) and text.strip():
            return text.replace("{robot_name}", robot_name).strip()
    except Exception as exc:  # a broken catalog must not break the agent
        logger.warning("referee persona %s not loaded: %s", slug, exc)
    return f"Ti si {robot_name}, robot sudija za stoni tenis. Kratko, fino i pravedno."


class RefereeMode:
    def __init__(self) -> None:
        self.active = False
        self.persona = "regular"
        self.api = (os.getenv("TT_API_URL") or "http://127.0.0.1:8099").rstrip("/") + "/api/table-tennis"
        self.token = os.getenv("TT_PERSONA_TOKEN") or None
        self.timeout_s = float(os.getenv("TT_REFEREE_TIMEOUT_S", "0.8"))
        self._cache: tuple[float, Optional[dict]] = (0.0, None)

    def set(self, on: bool, persona: Optional[str]) -> None:
        self.active = on
        if persona:
            self.persona = persona
        self._cache = (0.0, None)
        logger.info("REFEREE_MODE %s persona=%s", "ON" if on else "OFF", self.persona)

    # ------------------------------------------------------------ prompt

    def instructions(self, robot_name: str = "Titan") -> str:
        persona = _persona_text(PERSONA_SLUGS[self.persona], robot_name)
        return (
            "REFEREE MODE (stoni tenis je u toku; ova pravila imaju prednost nad ostalim ulogama):\n"
            f"{persona}\n"
            "- Za rezultat, servis, vođstvo ili imena igrača uvek prvo pozovi `get_table_tennis_match`.\n"
            "- Ne pozivaj `trigger_gesture`, kviz, anketu ni prepoznavanje lica dok traje meč.\n"
            "- Odgovaraj u jednoj do dve kratke rečenice."
        )

    # ------------------------------------------------------------ backend

    def _get(self, path: str):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            headers["X-TT-Actor"] = "persona"
        req = urllib.request.Request(self.api + path, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout_s) as res:
            return json.loads(res.read().decode("utf-8"))

    def current_match(self, max_age_s: float = 1.0) -> Optional[dict]:
        """Latest match snapshot (cached briefly); None when the backend is unreachable."""
        at, snap = self._cache
        if time.monotonic() - at < max_age_s:
            return snap
        try:
            ids = self._get("/matches")
            snap = self._get(f"/matches/{ids[-1]}") if ids else None
        except Exception as exc:
            logger.warning("REFEREE_MODE backend unreachable: %s", exc)
            snap = None
        self._cache = (time.monotonic(), snap)
        return snap

    def should_stay_silent(self) -> bool:
        """True while a rally is being played (never talk over the game)."""
        if not self.active:
            return False
        snap = self.current_match()
        return bool(snap and snap.get("status") == "rally")


def describe_match(snap: Optional[dict]) -> str:
    """Plain facts for the LLM. The score here is the only score it may use."""
    if not snap:
        return "Trenutno nema dostupnih podataka o meču."
    names = {p["id"]: p["display_name"] for p in snap.get("players", [])}
    roles = {p["id"]: p.get("role_label") for p in snap.get("players", [])}
    score = snap.get("score_by_player", {})
    p1, p2 = names.get("p1", "Igrač 1"), names.get("p2", "Igrač 2")
    s1, s2 = score.get("p1", 0), score.get("p2", 0)
    lines = [
        f"Stanje: {_STATUS_TEXT.get(snap.get('status'), snap.get('status'))}.",
        f"Rezultat: {p1} {s1}, {p2} {s2}.",
    ]
    if s1 != s2:
        lines.append(f"Vodi: {p1 if s1 > s2 else p2}.")
    if snap.get("server_id") and snap.get("status") != "finished":
        lines.append(f"Servira: {names.get(snap['server_id'], snap['server_id'])}.")
    if snap.get("winner_id"):
        lines.append(f"Pobednik: {names.get(snap['winner_id'], snap['winner_id'])}.")
    for pid, name in ((("p1", p1), ("p2", p2))):
        if roles.get(pid):
            lines.append(f"{name} je u firmi: {roles[pid]}.")
    lines.append("Igra se jedan gem do jedanaest, uz dva poena razlike.")
    return " ".join(lines)
