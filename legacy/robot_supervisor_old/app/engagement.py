from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from threading import RLock
from typing import Optional

from .controllers.services import ServiceController
from .models import EngagementStatus

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency during tests
    from livekit import api as livekit_api
    from livekit.api.agent import AgentServiceClient as LiveKitAgentServiceClient
except Exception:  # pragma: no cover - defensive
    livekit_api = None
    LiveKitAgentServiceClient = None


class EngagementError(RuntimeError):
    """Raised when engagement orchestration cannot proceed."""


@dataclass
class EngagementSettings:
    url: str
    api_key: str
    api_secret: str
    worker: Optional[str] = None
    default_room: Optional[str] = None
    agent_identity: Optional[str] = None


def load_engagement_settings() -> Optional[EngagementSettings]:
    url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    default_room = os.getenv("ROBOT_SUPERVISOR_DISPATCH_ROOM") or os.getenv("LIVEKIT_ROOM")

    if not all([url, api_key, api_secret]):
        logger.info("Engagement dispatch disabled; missing LiveKit credentials")
        return None

    worker = os.environ["LIVEKIT_AGENT_NAME"]
    agent_identity = os.environ["LIVEKIT_AGENT_NAME"]

    return EngagementSettings(
        url=url,
        api_key=api_key,
        api_secret=api_secret,
        worker=worker,
        default_room=default_room,
        agent_identity=agent_identity,
    )


class EngagementController:
    """Coordinates higher-level engagement states for the embodied agent."""

    def __init__(self, controller: ServiceController, settings: Optional[EngagementSettings]) -> None:
        self._controller = controller
        self._settings = settings
        self._status = EngagementStatus(state="idle", updated_at=time.time())
        self._lock = RLock()
        self._agent_client: Optional["LiveKitAgentServiceClient"] = None

    def status(self) -> EngagementStatus:
        with self._lock:
            return EngagementStatus(**self._status.model_dump())

    def dispatch(self, room: Optional[str] = None, restart_livekit: bool = False) -> EngagementStatus:
        with self._lock:
            if self._status.state in {"dispatching", "wrapping"}:
                raise EngagementError("Engagement workflow already running")
            self._set_state("dispatching")

        try:
            self._controller.stop("camera-bridge")
            self._controller.stop("audio-bridge")
            if restart_livekit:
                self._controller.restart("voice-agent")
            else:
                self._controller.stop("voice-agent")
                self._controller.start("voice-agent")
            job_id, room_name = self._maybe_create_job(room)
            self._controller.start("audio-bridge")
            self._controller.start("camera-bridge")
        except Exception as exc:
            logger.exception("Dispatch failed")
            self._set_error(str(exc))
            raise EngagementError(str(exc)) from exc

        with self._lock:
            self._status = EngagementStatus(
                state="engaged",
                updated_at=time.time(),
                last_room=room_name,
                last_job_id=job_id,
                last_error=None,
            )
            return EngagementStatus(**self._status.model_dump())

    def wrap(self, reset_voice_agent: bool = True) -> EngagementStatus:
        with self._lock:
            if self._status.state == "wrapping":
                raise EngagementError("Wrap already in progress")
            self._set_state("wrapping")

        try:
            self._controller.stop("camera-bridge")
            self._controller.stop("audio-bridge")
            if reset_voice_agent:
                self._controller.restart("voice-agent")
        except Exception as exc:
            logger.exception("Wrap failed")
            self._set_error(str(exc))
            raise EngagementError(str(exc)) from exc

        with self._lock:
            self._status = EngagementStatus(
                state="idle",
                updated_at=time.time(),
                last_room=None,
                last_job_id=None,
                last_error=None,
            )
            return EngagementStatus(**self._status.model_dump())

    def _set_state(self, state: str) -> None:
        self._status = EngagementStatus(
            state=state,
            updated_at=time.time(),
            last_room=self._status.last_room,
            last_job_id=self._status.last_job_id,
            last_error=self._status.last_error,
        )

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._status = EngagementStatus(
                state="error",
                updated_at=time.time(),
                last_room=self._status.last_room,
                last_job_id=self._status.last_job_id,
                last_error=message,
            )

    def _maybe_create_job(self, requested_room: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if not self._settings or not livekit_api:
            if not self._settings:
                logger.info("No engagement settings configured; skipping job creation")
            elif not livekit_api:
                logger.warning("LiveKit API unavailable; skipping job creation")
            return None, requested_room

        room_name = requested_room or self._settings.default_room
        if not room_name:
            raise EngagementError("Room name required for dispatch (set ROBOT_SUPERVISOR_DISPATCH_ROOM or pass room)")

        worker = self._settings.worker
        if not worker:
            raise EngagementError("LiveKit agent name not configured (set LIVEKIT_AGENT_NAME)")

        client = self._agent_client or self._create_agent_client()
        request = livekit_api.CreateJobRequest(
            worker=worker,
            room=livekit_api.JobRoom(room_name=room_name),
            agent_identity=self._settings.agent_identity,
        )
        response = client.create_job(request)
        job_id = getattr(getattr(response, "job", None), "id", None)
        logger.info("Created LiveKit job %s for room %s", job_id, room_name)
        return job_id, room_name

    def _create_agent_client(self) -> "LiveKitAgentServiceClient":
        if not livekit_api or not LiveKitAgentServiceClient:
            raise EngagementError("LiveKit Agent client not available")
        assert self._settings is not None
        self._agent_client = LiveKitAgentServiceClient(
            self._settings.url,
            self._settings.api_key,
            self._settings.api_secret,
        )
        return self._agent_client


__all__ = [
    "EngagementController",
    "EngagementError",
    "EngagementSettings",
    "load_engagement_settings",
]
