"""Server-Sent Events, bridged from the orchestrator's worker threads.

The orchestrator publishes events from a ThreadPoolExecutor; SSE consumers
live on the asyncio event loop. Each subscriber gets its own asyncio.Queue,
and the bus callback -- which runs on whichever worker thread published --
hands items across with `loop.call_soon_threadsafe`. That is the only place
the two concurrency models touch.

Surviving a dropped connection has three parts, and all three matter:

1. Every frame carries an `id:`. Browsers resend the last one they saw as
   the `Last-Event-ID` header automatically on reconnect.
2. `replay_since` returns exactly the events that were missed, or signals
   that the gap is too large -- in which case a `resync` frame tells the
   client to refetch full state rather than silently miss updates.
3. The client reloads current state over HTTP on every connect regardless,
   so SSE is an accelerator and never the sole source of truth.

The endpoint is `async def` on purpose: a run can last an hour, and a sync
handler would pin one of FastAPI's threadpool workers for its whole
duration.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING

from agentshowdown.orchestrator.events import Event, bus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import Request

# How long to wait for an event before emitting a keepalive comment.
# Proxies and load balancers drop idle connections; a comment costs nothing
# and is ignored by EventSource.
KEEPALIVE_SECONDS = 15

# Bound per-subscriber buffering. A client too slow to keep up is
# disconnected rather than allowed to grow the queue without limit; it will
# reconnect and resync.
MAX_QUEUED_EVENTS = 500


def format_sse(event: Event) -> str:
    """Renders one event as an SSE frame.

    Args:
        event: The event to render.

    Returns:
        A complete `id:`/`event:`/`data:` frame, blank-line terminated.
    """
    payload = json.dumps(event.data, default=str)
    return f"id: {event.id}\nevent: {event.type}\ndata: {payload}\n\n"


def format_resync(last_event_id: int) -> str:
    """Renders the frame telling a client its replay gap was too large."""
    payload = json.dumps({"reason": "event buffer gap", "resume_from": last_event_id})
    return f"id: {last_event_id}\nevent: resync\ndata: {payload}\n\n"


def parse_last_event_id(raw: str | None) -> int | None:
    """Parses the `Last-Event-ID` header.

    Args:
        raw: Header value, or None when absent.

    Returns:
        The id, or None if absent or not a positive integer. A malformed
        header is treated as a fresh connection rather than an error --
        the client still reconciles over HTTP.
    """
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


async def event_stream(request: Request, last_event_id: int | None) -> AsyncIterator[str]:
    """Yields SSE frames until the client goes away.

    Args:
        request: The live request, polled for disconnection.
        last_event_id: The last id this client saw, if it is reconnecting.

    Yields:
        SSE frames as strings.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUED_EVENTS)

    def on_event(event: Event) -> None:
        # Runs on a worker thread -- hop to the loop before touching the
        # queue. A full queue means the client cannot keep up; drop the
        # event rather than block the publisher, and let the eventual
        # reconnect resync.
        with contextlib.suppress(asyncio.QueueFull, RuntimeError):
            loop.call_soon_threadsafe(queue.put_nowait, event)

    # Replay before subscribing would race (an event could land in between),
    # so subscribe first and de-duplicate against the replayed ids.
    unsubscribe = bus.subscribe(on_event)
    try:
        missed = bus.replay_since(last_event_id)
        if missed is None:
            yield format_resync(bus.last_event_id)
            highest = bus.last_event_id
        else:
            for event in missed:
                yield format_sse(event)
            highest = missed[-1].id if missed else (last_event_id or 0)

        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event.id <= highest:
                continue  # already delivered via replay
            highest = event.id
            yield format_sse(event)
    finally:
        unsubscribe()
