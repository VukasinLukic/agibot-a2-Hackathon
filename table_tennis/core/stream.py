"""In-process fan-out of committed events to SSE subscribers.

Publishing happens while the match lock is held right after the SQLite commit,
and subscription registers under the same lock, so a subscriber never misses
an event between its initial snapshot and the live stream.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StreamMessage:
    event: str  # "snapshot" | "match_event"
    data: str  # JSON
    id: Optional[str] = None


class Subscriber:
    def __init__(self, maxsize: int = 500):
        self.maxsize = maxsize
        self.lagging = False
        self.closed = False

    def put(self, msg: StreamMessage) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class AsyncSubscriber(Subscriber):
    """Delivers into an asyncio.Queue owned by the SSE response's event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int = 500):
        super().__init__(maxsize)
        self.loop = loop
        self.queue: asyncio.Queue[StreamMessage] = asyncio.Queue(maxsize=maxsize)

    def _put_nowait(self, msg: StreamMessage) -> None:
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            # Slow client: never block the engine; the client gets a resync.
            self.lagging = True

    def put(self, msg: StreamMessage) -> None:
        if self.closed:
            return
        try:
            self.loop.call_soon_threadsafe(self._put_nowait, msg)
        except RuntimeError:
            self.closed = True


class SyncSubscriber(Subscriber):
    """Thread-safe queue; used by tests and in-process tools."""

    def __init__(self, maxsize: int = 500):
        super().__init__(maxsize)
        self.queue: queue.Queue[StreamMessage] = queue.Queue(maxsize=maxsize)

    def put(self, msg: StreamMessage) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(msg)
        except queue.Full:
            self.lagging = True

    def drain(self) -> list[StreamMessage]:
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out


class Broker:
    def __init__(self) -> None:
        self._subs: dict[str, list[Subscriber]] = {}
        self._lock = threading.Lock()

    def add(self, match_id: str, sub: Subscriber) -> None:
        with self._lock:
            self._subs.setdefault(match_id, []).append(sub)

    def remove(self, match_id: str, sub: Subscriber) -> None:
        sub.closed = True
        with self._lock:
            subs = self._subs.get(match_id, [])
            if sub in subs:
                subs.remove(sub)

    def publish(self, match_id: str, messages: list[StreamMessage]) -> None:
        with self._lock:
            subs = list(self._subs.get(match_id, []))
        for sub in subs:
            for msg in messages:
                sub.put(msg)

    def count(self, match_id: str) -> int:
        with self._lock:
            return len(self._subs.get(match_id, []))
