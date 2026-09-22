"""In-process event bus bridging worker threads to whatever is watching.

The orchestrator runs jobs on a ThreadPoolExecutor and blocks on
`time.sleep`; the web layer is asyncio. This module is the seam between
them, and it is deliberately stdlib-only so `orchestrator/` never grows a
dependency on FastAPI: publishers here just call plain callables, and it is
the API layer's job to hand over a callback that hops threads safely (via
`loop.call_soon_threadsafe`).

Every event carries a monotonically increasing id and is retained in a
bounded ring buffer. That is what makes a dropped browser connection
recoverable: an SSE client reconnects with `Last-Event-ID`, and
`replay_since` returns exactly what it missed -- or signals that the gap is
too large and the client should refetch from scratch.
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.logging import log

if TYPE_CHECKING:
    from collections.abc import Callable

# How many past events to keep for replay. At a few events per job per poll
# this covers a long run; beyond it, clients resync instead.
DEFAULT_BUFFER_SIZE = 1000


@dataclass(frozen=True)
class Event:
    """One published event."""

    id: int
    type: str
    data: dict[str, object] = field(default_factory=dict)


class EventBus:
    """Fan-out of events to live subscribers, with bounded replay.

    Safe to publish from any thread. Subscriber callbacks run on the
    publishing thread, so they must be cheap and non-blocking -- the
    intended callback just enqueues onto an asyncio queue.
    """

    def __init__(self, buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
        """
        Args:
            buffer_size: How many recent events to retain for replay.
        """
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        self._subscribers: dict[int, Callable[[Event], None]] = {}
        self._sub_ids = itertools.count(1)

    def publish(self, event_type: str, **data: object) -> Event:
        """Publishes an event to every subscriber.

        A subscriber that raises is logged and skipped rather than allowed
        to propagate: one broken browser connection must not take down the
        worker thread that happened to be publishing.

        Args:
            event_type: Event name (e.g. "job_changed").
            **data: JSON-serialisable payload.

        Returns:
            The published event, including its assigned id.
        """
        with self._lock:
            event = Event(id=next(self._ids), type=event_type, data=dict(data))
            self._buffer.append(event)
            subscribers = list(self._subscribers.values())

        for callback in subscribers:
            try:
                callback(event)
            except Exception:  # noqa: BLE001 -- a dead subscriber must not kill the publisher
                log.warning("event_subscriber_failed", event_type=event_type)
        return event

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        """Registers a subscriber.

        Args:
            callback: Called with each subsequent event, on the publishing
                thread. Must not block.

        Returns:
            A zero-argument function that unsubscribes. Callers must invoke
            it when done, or the bus keeps the callback (and whatever it
            closes over) alive for the process's lifetime.
        """
        with self._lock:
            sub_id = next(self._sub_ids)
            self._subscribers[sub_id] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(sub_id, None)

        return unsubscribe

    @property
    def subscriber_count(self) -> int:
        """How many subscribers are currently registered."""
        with self._lock:
            return len(self._subscribers)

    @property
    def last_event_id(self) -> int:
        """The id of the most recent event, or 0 if none published yet."""
        with self._lock:
            return self._buffer[-1].id if self._buffer else 0

    def replay_since(self, last_event_id: int | None) -> list[Event] | None:
        """Returns the events a reconnecting client missed.

        Args:
            last_event_id: The last id the client saw, or None for a fresh
                connection.

        Returns:
            The events after `last_event_id`, oldest first. Returns None
            when the gap can't be served -- the buffer has already discarded
            events the client needs -- which the caller should turn into a
            "resync" signal so the client refetches full state instead of
            silently missing updates. A fresh connection (None) gets an
            empty list: it should load current state over HTTP, not replay
            the whole history.
        """
        if last_event_id is None:
            return []
        with self._lock:
            buffered = list(self._buffer)
        if not buffered:
            return []
        oldest = buffered[0].id
        if last_event_id < oldest - 1:
            return None  # gap too large -- tell the client to resync
        return [event for event in buffered if event.id > last_event_id]


# Process-wide bus. The orchestrator publishes to it from worker threads;
# the API layer subscribes. A module-level instance keeps `orchestrator/`
# free of any wiring back to the web layer.
bus = EventBus()
