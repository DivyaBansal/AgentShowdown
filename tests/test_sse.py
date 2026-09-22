"""Tests for the SSE bridge and its reconnect behaviour.

The point of these is the durability guarantee: a browser that drops its
connection mid-run must be able to come back and not silently miss updates.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from backend.api import stream as stream_module
from backend.api.stream import (
    format_resync,
    format_sse,
    parse_last_event_id,
)
from backend.orchestrator.events import Event, EventBus


class _FakeRequest:
    """A request that reports disconnection after N checks."""

    def __init__(self, disconnect_after: int = 1) -> None:
        self._checks = 0
        self._limit = disconnect_after

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > self._limit


def test_frame_carries_id_event_and_json_data() -> None:
    frame = format_sse(Event(id=7, type="job_changed", data={"sandbox_name": "box-1"}))
    assert frame.startswith("id: 7\n")
    assert "event: job_changed\n" in frame
    assert '"sandbox_name": "box-1"' in frame
    assert frame.endswith("\n\n")  # blank line terminates a frame


def test_non_serialisable_values_do_not_break_a_frame() -> None:
    """A stray object in a payload must not kill the whole stream."""
    frame = format_sse(Event(id=1, type="e", data={"when": object()}))
    assert frame.startswith("id: 1\n")


def test_resync_frame_names_the_resume_point() -> None:
    assert '"resume_from": 42' in format_resync(42)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("12", 12), (None, None), ("", None), ("junk", None), ("-3", None), ("0", None)],
)
def test_last_event_id_header_parsing(header: str | None, expected: int | None) -> None:
    """A malformed header is a fresh connection, not an error -- the client
    still reconciles over HTTP."""
    assert parse_last_event_id(header) == expected


def test_a_worker_thread_publish_reaches_an_async_subscriber() -> None:
    """The thread -> asyncio hop is the whole point of the bridge."""

    async def scenario() -> list[Event]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue()
        bus = EventBus()
        bus.subscribe(lambda e: loop.call_soon_threadsafe(queue.put_nowait, e))

        # Publish from a worker thread, exactly as run_job does.
        await asyncio.to_thread(bus.publish, "job_changed", sandbox_name="box-1")

        return [await asyncio.wait_for(queue.get(), timeout=2)]

    received = asyncio.run(scenario())
    assert received[0].type == "job_changed"
    assert received[0].data["sandbox_name"] == "box-1"


def test_replay_delivers_exactly_what_was_missed() -> None:
    bus = EventBus()
    bus.publish("a")
    seen = bus.publish("b")
    bus.publish("c")
    bus.publish("d")

    missed = bus.replay_since(seen.id)

    assert missed is not None
    assert [e.type for e in missed] == ["c", "d"]


def test_a_client_gone_too_long_is_told_to_resync() -> None:
    """Silently missing updates would be worse than an explicit resync."""
    bus = EventBus(buffer_size=2)
    for _ in range(10):
        bus.publish("e")

    assert bus.replay_since(1) is None


def test_subscriber_is_removed_when_the_stream_ends() -> None:
    """A leaked subscriber would keep the connection's queue alive forever."""

    async def scenario() -> int:
        request = _FakeRequest(disconnect_after=0)
        agen = stream_module.event_stream(request, None)
        async for _frame in agen:
            break
        await agen.aclose()
        return stream_module.bus.subscriber_count

    before = stream_module.bus.subscriber_count
    after = asyncio.run(scenario())
    assert after == before


def test_slow_client_does_not_block_the_publisher() -> None:
    """A full queue drops events rather than stalling a worker thread."""

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1)
        bus = EventBus()

        def on_event(event: Event) -> None:
            with contextlib.suppress(asyncio.QueueFull, RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, event)

        bus.subscribe(on_event)
        for _ in range(50):
            await asyncio.to_thread(bus.publish, "e")

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_stream_ends_itself_so_it_cannot_block_server_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An endless stream holds uvicorn in "waiting for connections to close".

    Regression test for the dev server wedging on reload: uvicorn stops
    accepting, then waits for in-flight responses before running lifespan
    shutdown. A browser tab keeps `/events` in-flight indefinitely, so the
    old worker never exited, no replacement started, and every later request
    hung in the accept queue. The stream now retires itself and the client
    reconnects with `Last-Event-ID`.
    """
    monkeypatch.setattr(stream_module, "MAX_STREAM_SECONDS", 0.05)
    monkeypatch.setattr(stream_module, "KEEPALIVE_SECONDS", 0.01)

    async def scenario() -> None:
        # A client that never hangs up -- the lifetime cap is the only thing
        # that can end this stream.
        request = _FakeRequest(disconnect_after=1_000_000)
        async for _frame in stream_module.event_stream(request, None):
            pass

    # Without the cap this never returns and wait_for raises TimeoutError.
    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
