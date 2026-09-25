from __future__ import annotations

import asyncio
import logging
from typing import Dict, Iterable, List, Optional, Set

from .conversations import ConversationManager
from .controllers.services import ServiceController
from .models import ServiceStatus
from .models import ServiceStatus

logger = logging.getLogger(__name__)


class ConversationOrchestrator:
    """Keeps the conversation listener aligned with service availability."""

    def __init__(
        self,
        controller: ServiceController,
        manager: ConversationManager,
        required_services: Iterable[str],
        poll_interval: float = 1.0,
        trigger_services: Iterable[str] | None = None,
        agent_service: str = "voice-agent",
    ) -> None:
        self._controller = controller
        self._manager = manager
        self._required = [svc.strip() for svc in required_services if svc.strip()]
        self._triggers = [svc.strip() for svc in (trigger_services or []) if svc.strip()]
        self._poll_interval = max(0.5, poll_interval)
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._active = False
        self._missing_logged: Set[str] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._agent_service = agent_service or "voice-agent"

    async def start(self) -> None:
        if not self._manager.enabled:
            logger.info("Conversation tracking disabled; orchestrator will not run")
            return
        if self._task and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            stop_event = self._stop_event
            if stop_event and not stop_event.is_set():
                stop_event.set()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
        self._stop_event = None
        if self._active:
            await self._stop_active()
        self._loop = None

    async def _run(self) -> None:
        assert self._stop_event is not None
        try:
            while not self._stop_event.is_set():
                try:
                    await self._sync()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Conversation orchestrator sync failed")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._stop_event = None

    async def _sync(self) -> None:
        should_run = self._should_run()
        if should_run and not self._active:
            await self._start_active()
        elif not should_run and self._active:
            await self._stop_active()

    def _should_run(self) -> bool:
        if not self._required:
            return self._triggers_ready()

        statuses = {svc.name: svc for svc in self._controller.list_statuses()}
        missing = [name for name in self._required if name not in statuses]
        if missing:
            self._log_missing(missing)
            return False
        self._missing_logged.clear()
        for name in self._required:
            svc = statuses[name]
            if svc.state != "active":
                return False
        return self._triggers_ready(statuses)

    def _log_missing(self, names: List[str]) -> None:
        new_missing = set(names) - self._missing_logged
        if new_missing:
            logger.warning(
                "Required conversation service(s) not found in config: %s",
                ", ".join(sorted(new_missing)),
            )
            self._missing_logged.update(new_missing)

    def _triggers_ready(self, statuses: Optional[Dict[str, ServiceStatus]] = None) -> bool:
        if not self._triggers:
            return True
        if statuses is None:
            statuses = {svc.name: svc for svc in self._controller.list_statuses()}
        for name in self._triggers:
            svc = statuses.get(name)
            if svc and svc.state == "active":
                return True
        return False

    async def _start_active(self) -> None:
        logger.info("Starting conversation listener (dependencies ready)")
        await self._manager.start()
        self._active = True

    async def _stop_active(self) -> None:
        if not self._active:
            return
        logger.info("Stopping conversation listener (dependencies not satisfied)")
        await self._manager.stop()
        self._active = False

    def notify_service_stopping(self, service: str) -> None:
        if service == self._agent_service:
            self.force_disconnect()

    def force_disconnect(self) -> None:
        if not self._manager.enabled:
            return
        loop = self._loop
        if not loop:
            return
        asyncio.run_coroutine_threadsafe(self._stop_active(), loop)


__all__ = ["ConversationOrchestrator"]
