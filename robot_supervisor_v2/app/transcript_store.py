"""Thread-safe in-memory storage for conversation transcripts."""

from collections import deque
from threading import RLock
from typing import Dict, List, Optional
import time

from .models.conversation import ConversationEntry, ConversationRole


class TranscriptStore:
    """Thread-safe in-memory storage for conversation transcripts."""

    def __init__(self, max_entries: int = 500):
        self._history: deque[ConversationEntry] = deque(maxlen=max_entries)
        self._pending: Dict[str, ConversationEntry] = {}  # segment_id -> entry
        self._lock = RLock()
        self._session_id = 0

    def upsert(
        self,
        segment_id: str,
        entry_id: str,
        participant_identity: Optional[str],
        role: ConversationRole,
        text: str,
        final: bool,
    ) -> None:
        """Insert or update a transcript entry."""
        with self._lock:
            timestamp = time.time()
            entry = ConversationEntry(
                id=entry_id,
                segment_id=segment_id,
                participant_identity=participant_identity,
                role=role,
                text=text,
                final=final,
                updated_at=timestamp,
                session_id=self._session_id
            )

            if final:
                # Move from pending to history
                if segment_id in self._pending:
                    del self._pending[segment_id]
                self._history.append(entry)
            else:
                # Update pending entry
                self._pending[segment_id] = entry

    def snapshot(self) -> List[ConversationEntry]:
        """Get current transcript snapshot (history + pending)."""
        with self._lock:
            all_entries = list(self._history) + list(self._pending.values())
            return sorted(all_entries, key=lambda e: e.updated_at)

    def clear(self) -> None:
        """Clear all transcript data."""
        with self._lock:
            self._history.clear()
            self._pending.clear()

    def new_session(self) -> None:
        """Increment session ID for new conversation."""
        with self._lock:
            self._session_id += 1
