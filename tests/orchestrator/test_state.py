"""Tests for backend.orchestrator.state."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from backend.orchestrator import state
from backend.orchestrator.state import StateStore
from backend.orchestrator.telemetry import Sample


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


def _sample(name: str, ts: str, **kw: object) -> Sample:
    defaults = {
        "cpu_cores": 1.0,
        "mem_bytes": 1,
        "mem_limit_bytes": 2,
        "pids": 1,
    }
    return Sample(name, ts, **{**defaults, **kw})  # type: ignore[arg-type]


def test_samples_round_trip_in_chronological_order(tmp_path: Path) -> None:
    with StateStore(str(tmp_path / "s.sqlite3")) as store:
        for i, ts in enumerate(["t1", "t2", "t3"]):
            store.add_sample(_sample("box-1", ts, cpu_cores=float(i), mem_bytes=100 + i))
        samples = store.get_samples("box-1")

    assert [s["ts"] for s in samples] == ["t1", "t2", "t3"]
    assert samples[2]["cpu_cores"] == 2.0


def test_samples_are_scoped_per_sandbox(tmp_path: Path) -> None:
    with StateStore(str(tmp_path / "s.sqlite3")) as store:
        store.add_sample(_sample("box-1", "t1"))
        store.add_sample(_sample("box-2", "t1", cpu_cores=9.0))

        assert len(store.get_samples("box-1")) == 1
        assert store.get_samples("box-2")[0]["cpu_cores"] == 9.0


def test_unreported_sample_fields_stay_null(tmp_path: Path) -> None:
    """A NULL must stay distinguishable from a real zero."""
    with StateStore(str(tmp_path / "s.sqlite3")) as store:
        store.add_sample(
            _sample(
                "box-1",
                "t1",
                cpu_cores=None,
                mem_bytes=None,
                mem_limit_bytes=None,
                pids=None,
            )
        )
        sample = store.get_samples("box-1")[0]

    assert sample["cpu_cores"] is None
    assert sample["mem_bytes"] is None


def test_upsert_sql_column_list_matches_writable_columns() -> None:
    """The INSERT column list and _WRITABLE_COLUMNS are hand-synced.

    `upsert` binds values positionally in _WRITABLE_COLUMNS order, so a
    column inserted mid-tuple (rather than appended) silently shifts every
    later value into the wrong column. Nothing else in the suite would
    catch that, which is what makes this test worth its weight.
    """
    insert_cols = re.search(r"INSERT INTO jobs \((.*?)\)", state._UPSERT_SQL, re.S)
    assert insert_cols is not None
    names = tuple(c.strip() for c in insert_cols.group(1).split(",") if c.strip())

    assert names == ("sandbox_name", *state._WRITABLE_COLUMNS, "created_at", "updated_at")
    assert state._UPSERT_SQL.count("?") == len(names)


def test_every_writable_column_survives_a_round_trip(tmp_path: Path) -> None:
    """Guards the positional binding: a shifted column shows up here as a
    value landing in the wrong field."""
    db = tmp_path / "state.sqlite3"
    with StateStore(str(db)) as store:
        values: dict[str, object] = {col: f"value-for-{col}" for col in state._WRITABLE_COLUMNS}
        store.upsert("box-1", **values)

        row = store.get("box-1")

    assert row is not None
    for col, expected in values.items():
        assert row[col] == expected, f"{col} did not round-trip"


def test_repo_column_is_added_to_a_legacy_database(tmp_path: Path) -> None:
    """An existing database from before the shared-DB change must open."""
    db = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs ("
        "sandbox_name TEXT PRIMARY KEY, feature_id TEXT, agent_id TEXT, branch TEXT,"
        "status TEXT, pr_url TEXT, detail TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (sandbox_name, status, created_at, updated_at)"
        " VALUES ('old-box', 'succeeded', '2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    with StateStore(str(db)) as store:
        store.upsert(
            "new-box",
            feature_id="f1",
            agent_id="claude",
            status="queued",
            repo="/home/me/code/app",
        )
        rows = {row["sandbox_name"]: row for row in store.all_jobs()}

    # The pre-existing row survives with a NULL repo rather than a guess.
    assert rows["old-box"]["repo"] is None
    assert rows["new-box"]["repo"] == "/home/me/code/app"


def test_all_jobs_filters_by_repo(tmp_path: Path) -> None:
    """One shared database holds every workspace, so narrowing must work."""
    with StateStore(str(tmp_path / "state.sqlite3")) as store:
        common = {"feature_id": "f1", "agent_id": "claude", "status": "succeeded"}
        store.upsert("a", **common, repo="/repos/one")
        store.upsert("b", **common, repo="/repos/two")
        store.upsert("c", **common)

        names = {job["sandbox_name"] for job in store.all_jobs(repo="/repos/one")}
        assert names == {"a"}
        assert len(store.all_jobs()) == 3


def test_opens_a_database_in_a_directory_that_does_not_exist_yet(tmp_path: Path) -> None:
    """A fresh install has no ~/.agentshowdown, and sqlite3 will not make it.

    sqlite3.connect creates the database *file* but not its parent directory,
    so without this the very first startup dies with "unable to open database
    file" before the app can serve anything.
    """
    db = tmp_path / "not-created-yet" / "nested" / "jobs.sqlite3"

    with StateStore(str(db)) as store:
        store.upsert("box-1", feature_id="f1", agent_id="claude", status="queued")

    assert db.exists()
