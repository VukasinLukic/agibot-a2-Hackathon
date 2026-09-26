"""Actor resolution. The server decides who is speaking; JSON cannot claim it.

local mode (mock default, loopback bind only): ``X-TT-Actor`` header,
    default ``operator``. Convenient for the simulator and fake vision.
token mode (required for any network bind): ``Authorization: Bearer <token>``;
    each configured token maps to exactly one actor. Reads (GET/SSE) also
    require a valid token. Tokens are never accepted in the URL.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Request

from table_tennis.config import Settings
from table_tennis.core.errors import UnauthorizedError

ACTORS = ("operator", "vision", "sim", "robot", "persona")


def resolve_actor(request: Request, settings: Settings) -> str:
    if settings.auth.mode == "local":
        actor = (request.headers.get("x-tt-actor") or "operator").strip().lower()
        if actor not in ACTORS:
            raise UnauthorizedError("unknown_actor", f"unknown actor {actor!r}")
        return actor
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("missing_token", "Authorization: Bearer <token> required")
    actor = match_token(token.strip(), settings)
    if actor is None:
        raise UnauthorizedError("invalid_token", "token not recognised")
    return actor


def match_token(token: str, settings: Settings) -> Optional[str]:
    found = None
    for actor, expected in settings.auth.tokens.model_dump().items():
        if expected and hmac.compare_digest(token.encode(), str(expected).encode()):
            found = actor
    return found
