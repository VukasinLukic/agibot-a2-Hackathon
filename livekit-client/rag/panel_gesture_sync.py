"""Synchronize one pending panel gesture with LiveKit audible speech start."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class PanelGestureSync:
    """Dispatch the latest queued panel gesture once the agent starts speaking."""

    def __init__(self) -> None:
        self._pending: str | None = None
        self._session: Any | None = None
        self._handler: Callable[[Any], None] | None = None

    @property
    def pending(self) -> str | None:
        return self._pending

    def queue(self, gesture: str) -> None:
        self._pending = gesture.strip() or None

    def clear(self) -> None:
        self._pending = None

    def bind(self, session: Any, dispatch: Callable[[str], None]) -> None:
        if self._session is session:
            return
        self.unbind()

        def _on_agent_state_changed(event: Any) -> None:
            if getattr(event, "new_state", None) != "speaking":
                return
            gesture = self._pending
            self._pending = None
            if gesture:
                dispatch(gesture)

        session.on("agent_state_changed", _on_agent_state_changed)
        self._session = session
        self._handler = _on_agent_state_changed

    def unbind(self) -> None:
        session = self._session
        handler = self._handler
        self._session = None
        self._handler = None
        if session is not None and handler is not None and hasattr(session, "off"):
            session.off("agent_state_changed", handler)

