"""SQLite-backed job state store -- survives orchestrator restarts."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from backend.orchestrator.events import bus

if TYPE_CHECKING:
    from backend.orchestrator.telemetry import Sample


@dataclass(frozen=True)
class AgentEventRow:
    """One OTel event to store, already normalized by the receiver.

    Attributes:
        name: The event name, e.g. "claude_code.tool_result".
        ts: When the CLI says it happened (ISO-8601), if it said.
        attributes: The event's attributes as plain JSON values.
    """

    name: str
    ts: str | None
    attributes: dict[str, object] = field(default_factory=dict)


# Columns `upsert` accepts as keyword arguments.
_WRITABLE_COLUMNS: tuple[str, ...] = (
    "feature_id",
    "agent_id",
    "branch",
    "status",
    "pr_url",
    "detail",
    "run_id",
    "model",
    "run_label",
    "cpus",
    "memory_limit",
    "started_at",
    "finished_at",
    "duration_seconds",
    "tests_passed",
    "lint_passed",
    "files_changed",
    "lines_added",
    "lines_removed",
    "input_tokens",
    "output_tokens",
    "num_turns",
    "repo",
    "launch_s",
    "agent_s",
    "verify_s",
    "push_s",
    "failure_category",
    "question_options",
)

# Why a job failed, as a fixed vocabulary rather than the free-text
# `detail`, so failures can be grouped and counted -- in particular so flaky
# infrastructure can be told apart from an agent that did a bad job.
# "cancelled" is never derived from a status: it's passed explicitly by the
# paths where a person removed the sandbox on purpose.
FailureCategory = Literal[
    "infra", "agent_stuck", "timeout", "tests", "lint", "internal", "cancelled"
]
_FAILURE_CATEGORIES: dict[str, FailureCategory] = {
    "crashed": "infra",
    "lost": "infra",
    "stuck": "agent_stuck",
    "timed_out": "timeout",
    "tests_failed": "tests",
    "lint_failed": "lint",
    "error": "internal",
}


def failure_category_for(status: str) -> FailureCategory | None:
    """Maps a job status to its failure category.

    Args:
        status: A job status as stored in the `jobs` table.

    Returns:
        The category, or None when the status is not a failure (succeeded,
        still in flight, or parked on purpose: stopped / awaiting_input).
    """
    return _FAILURE_CATEGORIES.get(status)


# Columns added after the original langlearn schema, with the exact DDL to
# add each one. Written out literally rather than generated, so no SQL is
# ever built by string formatting.
_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("run_id", "ALTER TABLE jobs ADD COLUMN run_id TEXT"),
    ("model", "ALTER TABLE jobs ADD COLUMN model TEXT"),
    ("run_label", "ALTER TABLE jobs ADD COLUMN run_label TEXT"),
    ("cpus", "ALTER TABLE jobs ADD COLUMN cpus INTEGER"),
    ("memory_limit", "ALTER TABLE jobs ADD COLUMN memory_limit TEXT"),
    ("started_at", "ALTER TABLE jobs ADD COLUMN started_at TEXT"),
    ("finished_at", "ALTER TABLE jobs ADD COLUMN finished_at TEXT"),
    ("duration_seconds", "ALTER TABLE jobs ADD COLUMN duration_seconds REAL"),
    ("tests_passed", "ALTER TABLE jobs ADD COLUMN tests_passed INTEGER"),
    ("lint_passed", "ALTER TABLE jobs ADD COLUMN lint_passed INTEGER"),
    ("files_changed", "ALTER TABLE jobs ADD COLUMN files_changed INTEGER"),
    ("lines_added", "ALTER TABLE jobs ADD COLUMN lines_added INTEGER"),
    ("lines_removed", "ALTER TABLE jobs ADD COLUMN lines_removed INTEGER"),
    ("input_tokens", "ALTER TABLE jobs ADD COLUMN input_tokens INTEGER"),
    ("output_tokens", "ALTER TABLE jobs ADD COLUMN output_tokens INTEGER"),
    ("num_turns", "ALTER TABLE jobs ADD COLUMN num_turns INTEGER"),
    ("repo", "ALTER TABLE jobs ADD COLUMN repo TEXT"),
    ("log_tail", "ALTER TABLE jobs ADD COLUMN log_tail TEXT"),
    ("launch_s", "ALTER TABLE jobs ADD COLUMN launch_s REAL"),
    ("agent_s", "ALTER TABLE jobs ADD COLUMN agent_s REAL"),
    ("verify_s", "ALTER TABLE jobs ADD COLUMN verify_s REAL"),
    ("push_s", "ALTER TABLE jobs ADD COLUMN push_s REAL"),
    ("failure_category", "ALTER TABLE jobs ADD COLUMN failure_category TEXT"),
    # JSON-encoded list of strings: the choices an awaiting_input agent
    # offered alongside its question, if any.
    ("question_options", "ALTER TABLE jobs ADD COLUMN question_options TEXT"),
)

# One fully static statement -- no identifier is ever interpolated into SQL.
# `upsert` merges the caller's fields onto the existing row in Python and
# rewrites the whole row, so partial updates still leave untouched columns
# alone without needing a dynamically built SET clause.
_UPSERT_SQL = """
    INSERT INTO jobs (
        sandbox_name,
        feature_id,
        agent_id,
        branch,
        status,
        pr_url,
        detail,
        run_id,
        model,
        run_label,
        cpus,
        memory_limit,
        started_at,
        finished_at,
        duration_seconds,
        tests_passed,
        lint_passed,
        files_changed,
        lines_added,
        lines_removed,
        input_tokens,
        output_tokens,
        num_turns,
        repo,
        launch_s,
        agent_s,
        verify_s,
        push_s,
        failure_category,
        question_options,
        created_at,
        updated_at
    )
    VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
        ?
    )
    ON CONFLICT(sandbox_name) DO UPDATE SET
        feature_id = excluded.feature_id,
        agent_id = excluded.agent_id,
        branch = excluded.branch,
        status = excluded.status,
        pr_url = excluded.pr_url,
        detail = excluded.detail,
        run_id = excluded.run_id,
        model = excluded.model,
        run_label = excluded.run_label,
        cpus = excluded.cpus,
        memory_limit = excluded.memory_limit,
        started_at = excluded.started_at,
        finished_at = excluded.finished_at,
        duration_seconds = excluded.duration_seconds,
        tests_passed = excluded.tests_passed,
        lint_passed = excluded.lint_passed,
        files_changed = excluded.files_changed,
        lines_added = excluded.lines_added,
        lines_removed = excluded.lines_removed,
        input_tokens = excluded.input_tokens,
        output_tokens = excluded.output_tokens,
        num_turns = excluded.num_turns,
        repo = excluded.repo,
        launch_s = excluded.launch_s,
        agent_s = excluded.agent_s,
        verify_s = excluded.verify_s,
        push_s = excluded.push_s,
        failure_category = excluded.failure_category,
        question_options = excluded.question_options,
        updated_at = excluded.updated_at
"""


class StateStore:
    """Persists job rows to SQLite. Each caller opens its own connection --
    the store is safe to instantiate once per worker thread."""

    def __init__(self, db_path: str) -> None:
        # sqlite3 creates the database file but never its parent directory,
        # and the shared jobs database lives under ~/.agentshowdown, which
        # does not exist on a fresh install. Without this, first startup dies
        # with "unable to open database file".
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        # WAL + a generous busy_timeout let concurrent writers (multiple
        # worker threads, each with their own connection) queue instead of
        # raising "database is locked".
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
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
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                feature_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                mode TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS samples (
                sandbox_name TEXT NOT NULL,
                ts TEXT NOT NULL,
                cpu_cores REAL,
                mem_bytes INTEGER,
                mem_limit_bytes INTEGER,
                pids INTEGER
            )
            """
        )
        # Kept out of `jobs` on purpose: job rows are served to the UI
        # verbatim (`SELECT *`), and a spec's env can hold real tokens.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_specs (
                sandbox_name TEXT PRIMARY KEY,
                spec_json TEXT NOT NULL
            )
            """
        )
        # OTel log events exported by the agent CLIs (see agent_otel). Kept
        # out of `jobs` for the same reason as samples: many rows per job.
        # `received_at` is the host's clock, not the CLI's, so it orders
        # consistently against `agent_launches`.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sandbox_name TEXT NOT NULL,
                received_at TEXT NOT NULL,
                ts TEXT,
                name TEXT NOT NULL,
                attributes TEXT NOT NULL
            )
            """
        )
        # When each job's agent was last launched, so a resumed job's usage
        # counts only the current turn's events -- the same scope the run
        # log, which every launch overwrites, gives `parse_usage`.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_launches (
                sandbox_name TEXT PRIMARY KEY,
                launched_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_sandbox ON samples (sandbox_name, ts)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_events_sandbox ON agent_events (sandbox_name, id)"
        )
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Adds any metric columns a pre-existing database predates.

        `CREATE TABLE IF NOT EXISTS` silently does nothing against a database
        created by an earlier version, so a store opened on a langlearn-era
        file would otherwise be missing every metric column. Each ALTER is
        skipped when the column is already present, so this is safe to run on
        every open.
        """
        present = {row[1] for row in self.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        for column, ddl in _MIGRATIONS:
            if column not in present:
                self.conn.execute(ddl)

    def upsert(self, sandbox_name: str, **fields: object) -> None:
        """Inserts or updates a job row.

        Args:
            sandbox_name: Primary key of the job.
            **fields: Column values to set (e.g. status=, detail=). Every
                key must be a member of `_WRITABLE_COLUMNS`. Writing `status`
                also writes `failure_category` derived from it, unless the
                caller passes one explicitly -- so every path that moves a
                job's status keeps the category in step, including ones far
                from the orchestrator (kill-all, restart reconciliation).

        Raises:
            ValueError: If a field name isn't a known writable column.
        """
        unknown = set(fields) - set(_WRITABLE_COLUMNS)
        if unknown:
            raise ValueError(
                f"unknown job column(s): {sorted(unknown)}. Known: {sorted(_WRITABLE_COLUMNS)}"
            )
        if "status" in fields and "failure_category" not in fields:
            fields["failure_category"] = failure_category_for(str(fields["status"]))
        now = dt.datetime.now(dt.UTC).isoformat()
        # `with self.conn` wraps the read and the write in one transaction, so
        # a concurrent writer can't slip between them and lose an update.
        with self.conn:
            existing = self.get(sandbox_name)
            merged: dict[str, object] = {col: None for col in _WRITABLE_COLUMNS}
            if existing is not None:
                merged.update({c: existing[c] for c in _WRITABLE_COLUMNS})
            merged.update(fields)
            created_at = existing["created_at"] if existing is not None else now
            self.conn.execute(
                _UPSERT_SQL,
                (
                    sandbox_name,
                    *(merged[col] for col in _WRITABLE_COLUMNS),
                    created_at,
                    now,
                ),
            )

        # Published after the transaction commits, so any subscriber that
        # reads back sees the write that triggered it.
        bus.publish(
            "job_changed",
            sandbox_name=sandbox_name,
            status=merged["status"],
            changed=sorted(fields),
        )

    def update_log_tail(self, sandbox_name: str, tail: str) -> None:
        """Records a sandbox's latest log tail, without publishing `job_changed`.

        Deliberately bypasses `upsert`: the tail refreshes on every poll
        tick regardless of whether anything else about the job changed, and
        routing that through the same "something changed, refetch
        everything" signal the UI uses for status transitions would turn a
        live tail into a full job-list refetch storm. A caller that wants
        subscribers notified publishes its own event instead -- see
        `orchestrator.job.poll_until_signal`'s `job_log` event, which
        mirrors how sample publishing works for telemetry (`add_sample`
        below doesn't publish either).

        `log_tail` is intentionally absent from `_WRITABLE_COLUMNS`: this
        is the only path that ever writes it, so it stays untouched by
        every other `upsert` call instead of being reset to NULL by one.

        Args:
            sandbox_name: Job to update. A silent no-op if unknown -- the
                log tail is best-effort and must never fail a poll tick.
            tail: The new tail text.
        """
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET log_tail = ? WHERE sandbox_name = ?", (tail, sandbox_name)
            )

    def add_run(
        self, run_id: str, *, feature_id: str, created_at: str, mode: str, status: str
    ) -> None:
        """Records a new run, which groups the jobs of one comparison."""
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, feature_id, created_at, "
                "finished_at, mode, status) VALUES (?, ?, ?, NULL, ?, ?)",
                (run_id, feature_id, created_at, mode, status),
            )

    def finish_run(self, run_id: str, *, finished_at: str, status: str) -> None:
        """Marks a run complete."""
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?",
                (finished_at, status, run_id),
            )

    def get_run(self, run_id: str) -> dict | None:
        """Fetches one run, or None if unknown."""
        row = self.conn.execute(
            "SELECT run_id, feature_id, created_at, finished_at, mode, status "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        cols = ("run_id", "feature_id", "created_at", "finished_at", "mode", "status")
        return dict(zip(cols, row, strict=True)) if row else None

    def all_runs(self) -> list[dict]:
        """Fetches every run, newest first."""
        rows = self.conn.execute(
            "SELECT run_id, feature_id, created_at, finished_at, mode, status "
            "FROM runs ORDER BY created_at DESC"
        ).fetchall()
        cols = ("run_id", "feature_id", "created_at", "finished_at", "mode", "status")
        return [dict(zip(cols, row, strict=True)) for row in rows]

    def jobs_for_run(self, run_id: str) -> list[dict]:
        """Fetches every job belonging to a run, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE run_id = ? ORDER BY created_at", (run_id,)
        ).fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jobs LIMIT 0").description]
        return [dict(zip(cols, row, strict=True)) for row in rows]

    def add_sample(self, sample: Sample) -> None:
        """Records one resource sample for a sandbox.

        Args:
            sample: The reading to persist. Any field may be None -- a
                sandbox that can't report is not a failed job, and a NULL
                must stay distinguishable from a real zero.
        """
        with self.conn:
            self.conn.execute(
                "INSERT INTO samples (sandbox_name, ts, cpu_cores, mem_bytes, "
                "mem_limit_bytes, pids) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    sample.sandbox_name,
                    sample.ts,
                    sample.cpu_cores,
                    sample.mem_bytes,
                    sample.mem_limit_bytes,
                    sample.pids,
                ),
            )

    def get_samples(self, sandbox_name: str, limit: int = 720) -> list[dict]:
        """Returns a sandbox's samples, oldest first.

        Args:
            sandbox_name: Sandbox to read.
            limit: Most recent N samples to return. The default is an hour
                at the 5s sampling interval.

        Returns:
            Sample rows in chronological order.
        """
        rows = self.conn.execute(
            "SELECT sandbox_name, ts, cpu_cores, mem_bytes, mem_limit_bytes, pids "
            "FROM samples WHERE sandbox_name = ? ORDER BY ts DESC LIMIT ?",
            (sandbox_name, limit),
        ).fetchall()
        cols = ("sandbox_name", "ts", "cpu_cores", "mem_bytes", "mem_limit_bytes", "pids")
        return [dict(zip(cols, row, strict=True)) for row in reversed(rows)]

    def add_agent_events(self, sandbox_name: str, events: list[AgentEventRow]) -> None:
        """Records a batch of OTel events exported by a job's agent.

        Committed before returning, so by the time the receiver answers the
        exporter, the batch is readable -- which is what lets "the agent
        process exited" stand in for "its telemetry has arrived".

        Args:
            sandbox_name: The job the events belong to.
            events: The batch, in the order the exporter sent it.
        """
        received_at = dt.datetime.now(dt.UTC).isoformat()
        with self.conn:
            self.conn.executemany(
                "INSERT INTO agent_events (sandbox_name, received_at, ts, name, attributes) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (sandbox_name, received_at, e.ts, e.name, json.dumps(e.attributes))
                    for e in events
                ],
            )

    def get_agent_events(
        self, sandbox_name: str, *, since: str | None = None, limit: int = 5000
    ) -> list[dict]:
        """Returns a job's OTel events, oldest first.

        Args:
            sandbox_name: The job to read.
            since: Only events received at or after this ISO-8601 UTC time.
            limit: Most recent N events to return.

        Returns:
            Rows with "received_at", "ts", "name" and decoded "attributes".
        """
        rows = self.conn.execute(
            "SELECT received_at, ts, name, attributes FROM agent_events "
            "WHERE sandbox_name = ? AND received_at >= ? ORDER BY id DESC LIMIT ?",
            (sandbox_name, since or "", limit),
        ).fetchall()
        return [
            {"received_at": r[0], "ts": r[1], "name": r[2], "attributes": json.loads(r[3])}
            for r in reversed(rows)
        ]

    def set_agent_launched_at(self, sandbox_name: str, launched_at: str) -> None:
        """Records when a job's agent was (re)launched.

        Args:
            sandbox_name: The job.
            launched_at: ISO-8601 UTC time, taken just before the launch.
        """
        with self.conn:
            self.conn.execute(
                "INSERT INTO agent_launches (sandbox_name, launched_at) VALUES (?, ?) "
                "ON CONFLICT(sandbox_name) DO UPDATE SET launched_at = excluded.launched_at",
                (sandbox_name, launched_at),
            )

    def get_agent_launched_at(self, sandbox_name: str) -> str | None:
        """Returns the time recorded by `set_agent_launched_at`, or None."""
        row = self.conn.execute(
            "SELECT launched_at FROM agent_launches WHERE sandbox_name = ?", (sandbox_name,)
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        """Closes the underlying connection.

        Each caller opens its own connection, so a long-lived process that
        creates a store per job leaks one until GC runs. Callers that own a
        store for a bounded scope should close it (or use it as a context
        manager) rather than relying on collection.
        """
        self.conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def set_spec(self, sandbox_name: str, spec_json: str) -> None:
        """Records the agent spec a job was launched with, replacing any earlier one.

        Args:
            sandbox_name: The job's sandbox.
            spec_json: The serialized AgentSpec (see config.spec_to_json).
        """
        self.conn.execute(
            "INSERT INTO job_specs (sandbox_name, spec_json) VALUES (?, ?) "
            "ON CONFLICT(sandbox_name) DO UPDATE SET spec_json = excluded.spec_json",
            (sandbox_name, spec_json),
        )
        self.conn.commit()

    def get_spec(self, sandbox_name: str) -> str | None:
        """Returns the serialized spec recorded by `set_spec`, or None if there is none."""
        row = self.conn.execute(
            "SELECT spec_json FROM job_specs WHERE sandbox_name = ?", (sandbox_name,)
        ).fetchone()
        return row[0] if row else None

    def get(self, sandbox_name: str) -> dict | None:
        """Fetches one job row by sandbox_name, or None if unknown."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE sandbox_name = ?", (sandbox_name,)
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jobs LIMIT 0").description]
        return dict(zip(cols, row, strict=True))

    def all_jobs(self, repo: str | None = None) -> list[dict]:
        """Fetches job rows, most recently updated first.

        Args:
            repo: When given, only jobs recorded against that repo. One
                shared database now holds every workspace's history, so the
                default view needs a way to narrow to the repo in hand.

        Returns:
            The matching rows as dicts.
        """
        # Two complete statements chosen by a branch -- no SQL is assembled
        # from strings, per the project's no-string-formatted-SQL rule.
        if repo is None:
            rows = self.conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE repo = ? ORDER BY updated_at DESC", (repo,)
            ).fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jobs LIMIT 0").description]
        return [dict(zip(cols, row, strict=True)) for row in rows]
