"""Manages LiveKit connection and transcript capture."""

import asyncio
import logging
from typing import Optional
from livekit import rtc
from livekit.api import AccessToken, VideoGrants
from livekit.agents.types import (
    ATTRIBUTE_TRANSCRIPTION_FINAL,
    ATTRIBUTE_TRANSCRIPTION_SEGMENT_ID,
    ATTRIBUTE_TRANSCRIPTION_TRACK_ID,
    TOPIC_TRANSCRIPTION,
)

from .transcript_store import TranscriptStore
from .models.conversation import ConversationRole


logger = logging.getLogger(__name__)


class TranscriptManager:
    """Manages LiveKit connection and transcript capture."""

    def __init__(
        self,
        store: TranscriptStore,
        livekit_url: str,
        livekit_api_key: str,
        livekit_api_secret: str,
        supervisor_identity: str = "supervisor-monitor",
        agent_name: Optional[str] = None,
        topics: Optional[list[str]] = None
    ):
        self._store = store
        self._livekit_url = livekit_url
        self._livekit_api_key = livekit_api_key
        self._livekit_api_secret = livekit_api_secret
        self._supervisor_identity = supervisor_identity
        self._agent_name = agent_name
        self._topics = topics or [TOPIC_TRANSCRIPTION]  # Default LiveKit agents topic
        logger.info(f"TranscriptManager initialized with topics: {', '.join(self._topics)}")

        self._room: Optional[rtc.Room] = None
        self._connected = False
        self._enabled = False
        self._room_name: Optional[str] = None
        self._registered_topics: list[str] = []
        self._handler_tasks: set[asyncio.Task] = set()  # Track tasks to prevent GC

    async def connect(self, room_name: str) -> None:
        """Connect to LiveKit room and start capturing transcripts."""
        if self._connected:
            logger.warning("Already connected to LiveKit")
            return

        logger.info(f"Connecting transcript manager to room: {room_name}")

        try:
            # Create room
            self._room = rtc.Room()

            # Generate token
            token = AccessToken(
                self._livekit_api_key,
                self._livekit_api_secret
            ).with_identity(self._supervisor_identity).with_name("Supervisor Monitor").with_grants(
                VideoGrants(room_join=True, room=room_name)
            ).to_jwt()

            # Connect to room FIRST
            await self._room.connect(self._livekit_url, token)

            # THEN register text stream handlers for topics (must be after connect!)
            for topic in self._topics:
                self._room.register_text_stream_handler(topic, self._on_text_stream)
                self._registered_topics.append(topic)

            # Register other event handlers for debugging
            self._room.on("track_subscribed", self._on_track_subscribed)
            self._room.on("participant_connected", self._on_participant_connected)

            self._connected = True
            self._enabled = True
            self._room_name = room_name
            self._store.new_session()

            logger.info(f"✓ Transcript manager connected to room '{room_name}'")
            logger.info(f"  - Identity: {self._supervisor_identity}")
            logger.info(f"  - LiveKit URL: {self._livekit_url}")
            logger.info(f"  - Waiting for transcription events...")

        except Exception as e:
            logger.error(f"Failed to connect transcript manager: {e}")
            self._connected = False
            raise

    async def disconnect(self) -> None:
        """Disconnect from LiveKit room."""
        if not self._connected or not self._room:
            return

        logger.info("Disconnecting transcript manager")

        try:
            # Cancel all handler tasks
            if self._handler_tasks:
                for task in self._handler_tasks:
                    task.cancel()
                await asyncio.gather(*self._handler_tasks, return_exceptions=True)
                self._handler_tasks.clear()

            # Unregister text stream handlers
            for topic in self._registered_topics:
                self._room.unregister_text_stream_handler(topic)

            await self._room.disconnect()
        except Exception as e:
            logger.error(f"Error disconnecting transcript manager: {e}")
        finally:
            self._room = None
            self._connected = False
            self._enabled = False
            self._room_name = None
            self._registered_topics = []

    def _on_text_stream(self, reader: rtc.TextStreamReader, participant_identity: str) -> None:
        """Handle text stream from LiveKit agents framework."""
        logger.info(f"Text stream received from {participant_identity} on topic {reader.info.topic}")

        # Create async task and track it to prevent GC (like v1 does)
        task = asyncio.create_task(self._consume_text_stream(reader, participant_identity))
        self._handler_tasks.add(task)

        # Remove from set when done
        def _done(t: asyncio.Task):
            self._handler_tasks.discard(t)

        task.add_done_callback(_done)

    async def _consume_text_stream(self, reader: rtc.TextStreamReader, participant_identity: str) -> None:
        """Consume text stream and store transcript entries."""
        try:
            # Get attributes from stream using LiveKit constants
            attributes = reader.info.attributes

            segment_id = attributes.get(ATTRIBUTE_TRANSCRIPTION_SEGMENT_ID)
            if not segment_id:
                logger.debug(f"Skipping transcription without segment id from {participant_identity}")
                return

            stream_id = reader.info.stream_id or segment_id
            track_id = attributes.get(ATTRIBUTE_TRANSCRIPTION_TRACK_ID, "unknown")
            role = self._detect_role(participant_identity)

            # Accumulate text from chunks, updating as we go (like v1)
            text_parts = []
            async for chunk in reader:
                text_parts.append(chunk)
                # Store interim updates
                interim_text = "".join(text_parts).strip()
                if interim_text:
                    self._store.upsert(
                        segment_id=segment_id,
                        entry_id=f"{segment_id}:{stream_id}",
                        participant_identity=participant_identity,
                        role=role,
                        text=interim_text,
                        final=False
                    )

            # Check if final and store final version
            final_attr = attributes.get(ATTRIBUTE_TRANSCRIPTION_FINAL)
            is_final = str(final_attr).lower() == "true"
            full_text = "".join(text_parts).strip()

            if full_text:
                logger.info(f"Storing text stream: {role} - '{full_text}' (final={is_final})")
                self._store.upsert(
                    segment_id=segment_id,
                    entry_id=f"{segment_id}:{stream_id}",
                    participant_identity=participant_identity,
                    role=role,
                    text=full_text,
                    final=is_final
                )

        except Exception as e:
            logger.error(f"Error consuming text stream: {e}", exc_info=True)

    def _on_transcription_received(self, segments, participant, publication=None) -> None:
        """Handle transcription_received events from LiveKit.

        Args:
            segments: List of transcription segments
            participant: RemoteParticipant who sent the transcription (can be None)
            publication: Track publication (optional)
        """
        # Handle None participant like v1 does
        logger.info(f"Transcription received event: {len(segments)} segments from participant {getattr(participant, 'identity', 'unknown') if participant else 'None'}")
        participant_identity = getattr(participant, "identity", None) if participant else None

        if not segments:
            return

        logger.info(f"Transcription received: {len(segments)} segments from {participant_identity or 'unknown'}")

        for segment in segments:
            text = getattr(segment, "text", "")
            if not text or not text.strip():
                continue

            # Determine role
            role = self._detect_role(participant_identity)

            # Store in transcript
            segment_id = getattr(segment, "id", None)
            if not segment_id:
                import uuid
                segment_id = uuid.uuid4().hex

            entry_id = f"{segment_id}:transcription"
            final = bool(getattr(segment, "final", False))

            logger.debug(f"Storing transcript: {role} - '{text}' (final={final})")
            self._store.upsert(
                segment_id=segment_id,
                entry_id=entry_id,
                participant_identity=participant_identity,
                role=role,
                text=text,
                final=final
            )

    def _on_track_subscribed(self, track, publication, participant) -> None:
        """Handle track subscription for text streams."""
        logger.info(f"Track subscribed: {track.kind} from {participant.identity} (sid: {track.sid})")

    def _on_participant_connected(self, participant) -> None:
        """Handle participant connection."""
        logger.info(f"Participant connected: {participant.identity} (sid: {participant.sid})")

    def _detect_role(self, identity: Optional[str]) -> ConversationRole:
        """Detect role from participant identity."""
        if not identity:
            return ConversationRole.UNKNOWN
        if identity.startswith("agent-") or (self._agent_name and identity == self._agent_name):
            return ConversationRole.AGENT
        elif identity == self._supervisor_identity:
            return ConversationRole.SYSTEM
        else:
            return ConversationRole.USER

    def get_state(self) -> dict:
        """Get current transcript state."""
        return {
            "connected": self._connected,
            "enabled": self._enabled,
            "entries": [e.model_dump() for e in self._store.snapshot()]
        }

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def enabled(self) -> bool:
        return self._enabled
