"""Tests for agentshowdown.orchestrator.events."""

from __future__ import annotations

import threading

from agentshowdown.orchestrator.events import Event, EventBus


def test_publish_delivers_to_every_subscriber() -> None:
    bus = EventBus()
    a: list[Event] = []
    b: list[Event] = []
    bus.subscribe(a.append)
    bus.subscribe(b.append)

    bus.publish("job_changed", sandbox="box-1")

    assert len(a) == len(b) == 1
    assert a[0].type == "job_changed"
    assert a[0].data == {"sandbox": "box-1"}


def test_event_ids_increase_monotonically() -> None:
    bus = EventBus()
    ids = [bus.publish("e").id for _ in range(5)]
    assert ids == [1, 2, 3, 4, 5]


def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    received: list[Event] = []
    unsubscribe = bus.subscribe(received.append)

    bus.publish("first")
    unsubscribe()
    bus.publish("second")

    assert [e.type for e in received] == ["first"]
    assert bus.subscriber_count == 0


def test_a_failing_subscriber_cannot_break_the_publisher() -> None:
    """One dropped browser connection must not kill a worker thread."""
    bus = EventBus()
    good: list[Event] = []

    def explode(_event: Event) -> None:
        raise RuntimeError("subscriber died")

    bus.subscribe(explode)
    bus.subscribe(good.append)

    bus.publish("job_changed")  # must not raise

    assert len(good) == 1


def test_replay_returns_only_events_after_the_last_seen_id() -> None:
    bus = EventBus()
    bus.publish("a")
    second = bus.publish("b")
    bus.publish("c")

    missed = bus.replay_since(second.id)

    assert missed is not None
    assert [e.type for e in missed] == ["c"]


def test_fresh_connection_replays_nothing() -> None:
    """A new client loads state over HTTP rather than replaying history."""
    bus = EventBus()
    bus.publish("a")
    assert bus.replay_since(None) == []


def test_replay_signals_resync_when_the_gap_is_too_large() -> None:
    """Events the client needs have already aged out of the buffer."""
    bus = EventBus(buffer_size=3)
    for _ in range(10):
        bus.publish("e")

    assert bus.replay_since(1) is None


def test_replay_at_the_buffer_boundary_still_succeeds() -> None:
    bus = EventBus(buffer_size=3)
    for _ in range(5):
        bus.publish("e")
    # Buffer holds ids 3,4,5; a client that saw 2 lost nothing.
    missed = bus.replay_since(2)
    assert missed is not None
    assert [e.id for e in missed] == [3, 4, 5]


def test_buffer_is_bounded() -> None:
    bus = EventBus(buffer_size=5)
    for _ in range(50):
        bus.publish("e")
    assert len(bus.replay_since(45) or []) == 5


def test_last_event_id_tracks_the_newest_event() -> None:
    bus = EventBus()
    assert bus.last_event_id == 0
    bus.publish("a")
    assert bus.last_event_id == 1


def test_publishing_from_many_threads_assigns_unique_ids() -> None:
    """Worker threads publish concurrently; ids must not collide."""
    bus = EventBus(buffer_size=1000)
    received: list[Event] = []
    lock = threading.Lock()

    def collect(event: Event) -> None:
        with lock:
            received.append(event)

    bus.subscribe(collect)
    threads = [
        threading.Thread(target=lambda: [bus.publish("e") for _ in range(20)]) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = [e.id for e in received]
    assert len(ids) == 160
    assert len(set(ids)) == 160
