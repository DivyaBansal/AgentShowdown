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


# The exact schema langlearn's orchestrator_state.sqlite3 was created with.
_LEGACY_SCHEMA = """
CREATE TABLE jobs (
    sandbox_name TEXT PRIMARY KEY,
    feature_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    branch TEXT,
    status TEXT NOT NULL,
    pr_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    detail TEXT
)
"""


def test_opening_a_legacy_database_adds_the_metric_columns(tmp_path: Path) -> None:
    """CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so a
    pre-metrics database would otherwise be permanently missing these."""
    db = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(db)
    legacy.execute(_LEGACY_SCHEMA)
    legacy.execute(
        "INSERT INTO jobs (sandbox_name, feature_id, agent_id, status, "
        "created_at, updated_at) VALUES ('old-box', 'f1', 'claude', "
        "'succeeded', '2026-08-24T18:16:57', '2026-08-24T18:19:51')"
    )
    legacy.commit()
    legacy.close()

    with StateStore(str(db)) as store:
        store.upsert("old-box", duration_seconds=174.0, input_tokens=1234)
        job = store.get("old-box")

    assert job is not None
    assert job["duration_seconds"] == 174.0
    assert job["input_tokens"] == 1234
    assert job["status"] == "succeeded"  # pre-existing data survives


def test_migration_is_idempotent_across_reopens(tmp_path: Path) -> None:
    db = str(tmp_path / "state.sqlite3")
    with StateStore(db) as store:
        store.upsert("box-1", feature_id="f1", agent_id="claude", status="queued")
    with StateStore(db) as store:  # must not fail on duplicate ADD COLUMN
        store.upsert("box-1", output_tokens=42)
        assert store.get("box-1")["output_tokens"] == 42


def test_metric_columns_round_trip(tmp_path: Path) -> None:
    with StateStore(str(tmp_path / "state.sqlite3")) as store:
        store.upsert(
            "box-1",
            feature_id="f1",
            agent_id="codex",
            status="succeeded",
            run_id="run-1",
            model="gpt-5.6-sol",
            duration_seconds=93.5,
            tests_passed=1,
            files_changed=3,
            lines_added=40,
            input_tokens=900,
            output_tokens=80,
        )
        job = store.get("box-1")

    assert job is not None
    assert job["run_id"] == "run-1"
    assert job["model"] == "gpt-5.6-sol"
    assert job["duration_seconds"] == 93.5
    assert job["lines_added"] == 40
    # Never reported -> stays NULL, so the UI can distinguish it from zero.
    assert job["num_turns"] is None
