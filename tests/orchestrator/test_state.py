"""Tests for agentshowdown.orchestrator.state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentshowdown.orchestrator.state import StateStore


def test_upsert_then_get_round_trips(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path / "state.sqlite3"))
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b1", status="queued")

    job = store.get("box-1")

    assert job is not None
    assert job["feature_id"] == "f1"
    assert job["agent_id"] == "claude"
    assert job["status"] == "queued"


def test_get_unknown_sandbox_returns_none(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path / "state.sqlite3"))
    assert store.get("nonexistent") is None


def test_upsert_updates_existing_row(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path / "state.sqlite3"))
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b1", status="queued")
    store.upsert("box-1", status="running")

    job = store.get("box-1")

    assert job is not None
    assert job["status"] == "running"
    assert job["feature_id"] == "f1"  # untouched fields survive


def test_all_jobs_orders_most_recently_updated_first(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path / "state.sqlite3"))
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b1", status="queued")
    store.upsert("box-2", feature_id="f2", agent_id="claude", branch="b2", status="queued")
    store.upsert("box-1", status="running")  # touch box-1 again, moves it to the front

    jobs = store.all_jobs()

    assert [j["sandbox_name"] for j in jobs] == ["box-1", "box-2"]


def test_all_jobs_empty_store_returns_empty_list(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path / "state.sqlite3"))
    assert store.all_jobs() == []


def test_wal_journal_mode_enabled(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path / "state.sqlite3"))
    mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_concurrent_writers_do_not_raise_database_locked(tmp_path: Path) -> None:
    db_path = str(tmp_path / "state.sqlite3")
    store_a = StateStore(db_path)
    store_b = StateStore(db_path)

    store_a.upsert("box-1", feature_id="f1", agent_id="claude", branch="b1", status="queued")
    store_b.upsert("box-2", feature_id="f2", agent_id="claude", branch="b2", status="queued")

    assert len(store_a.all_jobs()) == 2


def test_close_releases_the_connection(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path / "state.sqlite3"))
    store.upsert("box-1", feature_id="f1", agent_id="claude", status="queued")
    store.close()

    with pytest.raises(sqlite3.ProgrammingError):
        store.get("box-1")


def test_context_manager_closes_on_exit(tmp_path: Path) -> None:
    with StateStore(str(tmp_path / "state.sqlite3")) as store:
        store.upsert("box-1", feature_id="f1", agent_id="claude", status="queued")
        assert store.get("box-1") is not None

    with pytest.raises(sqlite3.ProgrammingError):
        store.get("box-1")


def test_upsert_rejects_unknown_column(tmp_path: Path) -> None:
    with StateStore(str(tmp_path / "state.sqlite3")) as store:
        with pytest.raises(ValueError, match="unknown job column"):
            store.upsert("box-1", status="queued", not_a_column="x")
