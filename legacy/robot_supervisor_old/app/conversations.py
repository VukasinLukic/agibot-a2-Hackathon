from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from threading import RLock
from typing import Awaitable, Deque, Dict, List, Optional

from livekit import api, rtc
from livekit.agents.types import (
    ATTRIBUTE_TRANSCRIPTION_FINAL,
    ATTRIBUTE_TRANSCRIPTION_SEGMENT_ID,
    ATTRIBUTE_TRANSCRIPTION_TRACK_ID,
    TOPIC_TRANSCRIPTION,
)

from .models import ConversationEntry, ConversationState

logger = logging.getLogger(__name__)


@dataclass
class ConversationSettings:
    url: str
    room: str
    api_key: str
    api_secret: str
    identity: str
    agent_identity: Optional[str] = None
    max_entries: int = 200
    topics: List[str] = field(default_factory=list)


def load_conversation_settings() -> Optional[ConversationSettings]:
    url = os.getenv("LIVEKIT_URL")
    room = os.getenv("LIVEKIT_ROOM")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    identity = os.getenv("ROBOT_SUPERVISOR_CONVERSATION_ID", "supervisor-monitor")
    topics_env = os.getenv("ROBOT_SUPERVISOR_CONVERSATION_TOPICS")

    if not all([url, room, api_key, api_secret]):
        logger.info("Conversation tracking disabled; missing LiveKit connection settings")
        return None

    agent_identity = os.environ["LIVEKIT_AGENT_NAME"]

    if topics_env:
        topics = [topic.strip() for topic in topics_env.split(",") if topic.strip()]
    else:
        topics = []

    return ConversationSettings(
        url=url,
        room=room,
        api_key=api_key,
        api_secret=api_secret,
        identity=identity,
        agent_identity=agent_identity,
        topics=topics,
    )


class ConversationStore:
    def __init__(self, max_entries: int = 200) -> None:
        self._history: Deque[ConversationEntry] = deque(maxlen=max_entries)
        self._pending: Dict[str, ConversationEntry] = {}
        self._lock = RLock()

    def upsert(
        self,
        *,
        segment_id: str,
        entry_id: str,
        participant_identity: Optional[str],
        role: str,
        text: str,
        final: bool,
        session_id: int,
    ) -> None:
        normalized = text.strip()
        if not normalized:
            return

        entry = ConversationEntry(
            id=entry_id,
            segment_id=segment_id,
            participant_identity=participant_identity,
            role=role,
            text=normalized,
            final=final,
            updated_at=time.time(),
            session_id=session_id,
        )

        with self._lock:
            if final:
                self._pending.pop(segment_id, None)
                self._history.append(entry)
            else:
                self._pending[segment_id] = entry

    def snapshot(self) -> List[ConversationEntry]:
        with self._lock:
            entries = list(self._history)
            entries.extend(self._pending.values())
        entries.sort(key=lambda item: item.updated_at)
        return [item.model_copy() for item in entries]

    def clear_pending(self) -> None:
        with self._lock:
            self._pending.clear()


class ConversationManager:
    def __init__(self, settings: ConversationSettings | None) -> None:
        self._settings = settings
        self._store = ConversationStore(settings.max_entries if settings else 200)
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._connected = False
        self._connected_lock = RLock()
        self._session_counter = 0
        self._current_session_id = 0

    @property
    def enabled(self) -> bool:
        return self._settings is not None

    def state(self) -> ConversationState:
        entries = self._store.snapshot()
        return ConversationState(connected=self.is_connected, entries=entries, enabled=self.enabled)

    @property
    def is_connected(self) -> bool:
        with self._connected_lock:
            return self._connected

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._stop_event = None

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                await self._listen_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Conversation listener error; retrying in %.1fs", backoff)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    async def _listen_once(self) -> None:
        assert self._settings is not None and self._stop_event is not None

        room = rtc.Room()
        token = self._build_access_token()
        await room.connect(self._settings.url, token)
        disconnect_event = asyncio.Event()
        session_id = self._next_session_id()
        self._current_session_id = session_id

        @room.on("disconnected")
        def _on_disconnected(_reason: rtc.DisconnectReason):
            disconnect_event.set()

        handler_tasks: set[asyncio.Task] = set()

        def _track_task(coro: Awaitable[None]) -> None:
            task = asyncio.create_task(coro)
            handler_tasks.add(task)

            def _done(_: asyncio.Future):
                handler_tasks.discard(task)

            task.add_done_callback(_done)

        def _handle_text_stream(reader: rtc.TextStreamReader, participant_identity: str):
            _track_task(self._consume_stream(reader, participant_identity, session_id))

        registered_topics: List[str] = []
        topics = list(dict.fromkeys(self._settings.topics))
        if topics:
            for topic in topics:
                room.register_text_stream_handler(topic, _handle_text_stream)
                registered_topics.append(topic)
            logger.info(
                "Conversation listener connected to room '%s' (topics=%s)",
                self._settings.room,
                ", ".join(registered_topics),
            )
        else:
            logger.info(
                "Conversation listener connected to room '%s' (topics disabled; using transcription events)",
                self._settings.room,
            )

        def _handle_transcription_event(segments, participant, publication):
            identity = getattr(participant, "identity", None) if participant else None
            _track_task(self._consume_transcription_segments(segments, identity, session_id))

        room.on("transcription_received", _handle_transcription_event)

        self._set_connected(True)

        wait_tasks = [
            asyncio.create_task(self._stop_event.wait()),
            asyncio.create_task(disconnect_event.wait()),
        ]
        done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            task.cancel()

        self._set_connected(False)
        logger.info("Conversation listener disconnecting from room '%s'", self._settings.room)

        for topic in registered_topics:
            room.unregister_text_stream_handler(topic)
        room.off("transcription_received", _handle_transcription_event)
        await self._cancel_tasks(handler_tasks)
        await room.disconnect()
        self._store.clear_pending()

    async def _consume_stream(
        self,
        reader: rtc.TextStreamReader,
        participant_identity: str,
        session_id: int,
    ) -> None:
        segment_id = reader.info.attributes.get(ATTRIBUTE_TRANSCRIPTION_SEGMENT_ID)
        if not segment_id:
            logger.debug("Skipping transcription without segment id from %s", participant_identity)
            return

        stream_id = reader.info.stream_id or segment_id
        track_id = reader.info.attributes.get(ATTRIBUTE_TRANSCRIPTION_TRACK_ID)
        role = self._role_for(participant_identity)

        chunks: List[str] = []
        async for chunk in reader:
            chunks.append(chunk)
            text = "".join(chunks)
            self._store.upsert(
                segment_id=segment_id,
                entry_id=f"{segment_id}:{stream_id}",
                participant_identity=participant_identity,
                role=role,
                text=text,
                final=False,
                session_id=session_id,
            )

        final_attr = reader.info.attributes.get(ATTRIBUTE_TRANSCRIPTION_FINAL)
        final_flag = str(final_attr).lower() == "true"
        text = "".join(chunks)
        self._store.upsert(
            segment_id=segment_id,
            entry_id=f"{segment_id}:{stream_id}",
            participant_identity=participant_identity,
            role=role,
            text=text,
            final=final_flag,
            session_id=session_id,
        )

    def _role_for(self, participant_identity: Optional[str]) -> str:
        if not participant_identity:
            return "unknown"
        if participant_identity.startswith("agent-"):
            return "agent"
        if self._settings and self._settings.agent_identity:
            if participant_identity == self._settings.agent_identity:
                return "agent"
        return "user"

    def _set_connected(self, value: bool) -> None:
        with self._connected_lock:
            self._connected = value

    def _next_session_id(self) -> int:
        self._session_counter += 1
        return self._session_counter

    async def _cancel_tasks(self, tasks: set[asyncio.Task]) -> None:
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _consume_transcription_segments(
        self,
        segments,
        participant_identity: Optional[str],
        session_id: int,
    ) -> None:
        if not segments:
            return
        for segment in segments:
            text = getattr(segment, "text", "")
            if not text or not text.strip():
                continue
            segment_id = getattr(segment, "id", None) or uuid.uuid4().hex
            entry_id = f"{segment_id}:transcription"
            final = bool(getattr(segment, "final", False))
            role = self._role_for(participant_identity)
            self._store.upsert(
                segment_id=segment_id,
                entry_id=entry_id,
                participant_identity=participant_identity,
                role=role,
                text=text,
                final=final,
                session_id=session_id,
            )

    def _build_access_token(self) -> str:
        assert self._settings is not None
        grants = api.VideoGrants(
            room_join=True,
            room=self._settings.room,
            can_publish=False,
            can_subscribe=True,
            can_publish_data=False,
        )
        return (
            api.AccessToken(self._settings.api_key, self._settings.api_secret)
            .with_identity(self._settings.identity)
            .with_name("Supervisor Conversation Monitor")
            .with_grants(grants)
            .to_jwt()
        )


__all__ = [
    "ConversationManager",
    "ConversationSettings",
    "load_conversation_settings",
]
