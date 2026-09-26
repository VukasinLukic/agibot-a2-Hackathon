from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LOG = logging.getLogger("detection_dispatch")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class DetectionDispatchClient:
    """
    Dispatches a conversation when the detector locks onto a person.

    Important behavior:
    - Dispatches only through the existing supervisor endpoint.
    - Never wraps/ends conversations.
    - Rearms only after the detector loses the lock.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        supervisor_url: str,
        room: Optional[str] = None,
        agent_implementation: Optional[str] = None,
        cooldown_seconds: float = 30.0,
        timeout_seconds: float = 3.0,
    ) -> None:
        self.enabled = enabled
        self.supervisor_url = supervisor_url.rstrip("/")
        self.room = room or None
        self.agent_implementation = agent_implementation or None
        self.cooldown_seconds = cooldown_seconds
        self.timeout_seconds = timeout_seconds

        self._handled_track_id: Optional[int] = None
        self._last_dispatch_at = 0.0

    @classmethod
    def from_env(cls) -> "DetectionDispatchClient":
        return cls(
            enabled=_env_bool("DETECTION_DISPATCH_ENABLED", False),
            supervisor_url=os.getenv("DETECTION_DISPATCH_SUPERVISOR_URL", "http://127.0.0.1:8080"),
            room=os.getenv("DETECTION_DISPATCH_ROOM") or None,
            agent_implementation=os.getenv("DETECTION_DISPATCH_AGENT_IMPLEMENTATION") or None,
            cooldown_seconds=float(os.getenv("DETECTION_DISPATCH_COOLDOWN_SECONDS", "30")),
            timeout_seconds=float(os.getenv("DETECTION_DISPATCH_TIMEOUT_SECONDS", "3")),
        )

    async def handle_lock(self, locked_track_id: Optional[int]) -> None:
        if not self.enabled:
            return

        # Rearm only after the locked person is lost.
        if locked_track_id is None:
            self._handled_track_id = None
            return

        # Already handled this lock.
        if self._handled_track_id == locked_track_id:
            return

        self._handled_track_id = locked_track_id

        now = time.monotonic()
        if now - self._last_dispatch_at < self.cooldown_seconds:
            LOG.info("Skipping dispatch; cooldown active")
            return

        state = await self._get_conversation_state()
        if state != "idle":
            LOG.info("Skipping dispatch; conversation state is %s", state)
            return

        await self._dispatch()
        self._last_dispatch_at = now
        LOG.info("Conversation dispatched for detected person track_id=%s", locked_track_id)

    async def _get_conversation_state(self) -> str:
        payload = await asyncio.to_thread(
            self._request_json,
            "GET",
            "/api/conversation/status",
            None,
        )
        return str(payload.get("state", "unknown"))

    async def _dispatch(self) -> dict:
        params = {}
        if self.room:
            params["room"] = self.room
        if self.agent_implementation:
            params["agent_implementation"] = self.agent_implementation

        return await asyncio.to_thread(
            self._request_json,
            "POST",
            "/api/conversation/dispatch",
            params,
        )

    def _request_json(self, method: str, path: str, params: Optional[dict]) -> dict:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.supervisor_url}{path}{query}"

        data = b"" if method.upper() == "POST" else None
        request = Request(url, data=data, method=method.upper())

        with urlopen(request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8")

        return json.loads(body) if body else {}
