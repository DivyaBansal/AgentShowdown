"""SQLite-backed job state store -- survives orchestrator restarts."""

from __future__ import annotations

import datetime as dt
import sqlite3

# Columns `upsert` accepts as keyword arguments.
_WRITABLE_COLUMNS: tuple[str, ...] = (
    "feature_id",
    "agent_id",
    "branch",
    "status",
    "pr_url",
    "detail",
)

# One fully static statement -- no identifier is ever interpolated into SQL.
# `upsert` merges the caller's fields onto the existing row in Python and
# rewrites the whole row, so partial updates still leave untouched columns
# alone without needing a dynamically built SET clause.
_UPSERT_SQL = """
    INSERT INTO jobs (
        sandbox_name, feature_id, agent_id, branch, status, pr_url,
        created_at, updated_at, detail
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(sandbox_name) DO UPDATE SET
        feature_id = excluded.feature_id,
        agent_id   = excluded.agent_id,
        branch     = excluded.branch,
        status     = excluded.status,
        pr_url     = excluded.pr_url,
        updated_at = excluded.updated_at,
        detail     = excluded.detail
"""


class StateStore:
    """Persists job rows to SQLite. Each caller opens its own connection --
    the store is safe to instantiate once per worker thread."""

    def __init__(self, db_path: str) -> None:
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
        self.conn.commit()

    def upsert(self, sandbox_name: str, **fields: object) -> None:
        """Inserts or updates a job row.

        Args:
            sandbox_name: Primary key of the job.
            **fields: Column values to set (e.g. status=, detail=). Every
                key must be a member of `_WRITABLE_COLUMNS`.

        Raises:
            ValueError: If a field name isn't a known writable column.
        """
        unknown = set(fields) - set(_WRITABLE_COLUMNS)
        if unknown:
            raise ValueError(
                f"unknown job column(s): {sorted(unknown)}. Known: {sorted(_WRITABLE_COLUMNS)}"
            )
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
                    merged["feature_id"],
                    merged["agent_id"],
                    merged["branch"],
                    merged["status"],
                    merged["pr_url"],
                    created_at,
                    now,
                    merged["detail"],
                ),
            )

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

    def get(self, sandbox_name: str) -> dict | None:
        """Fetches one job row by sandbox_name, or None if unknown."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE sandbox_name = ?", (sandbox_name,)
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jobs LIMIT 0").description]
        return dict(zip(cols, row, strict=True))

    def all_jobs(self) -> list[dict]:
        """Fetches every job row, most recently updated first."""
        rows = self.conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC").fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jobs LIMIT 0").description]
        return [dict(zip(cols, row, strict=True)) for row in rows]
