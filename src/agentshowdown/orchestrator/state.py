"""SQLite-backed job state store -- survives orchestrator restarts."""

from __future__ import annotations

import datetime as dt
import sqlite3


class StateStore:
    """Persists job rows to SQLite. Each caller opens its own connection --
    the store is safe to instantiate once per worker thread."""

    def __init__(self, db_path: str):
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

    def upsert(self, sandbox_name: str, **fields):
        """Inserts or updates a job row.

        Args:
            sandbox_name: Primary key of the job.
            **fields: Column values to set (e.g. status=, detail=).
        """
        now = dt.datetime.now(dt.UTC).isoformat()
        existing = self.conn.execute(
            "SELECT sandbox_name FROM jobs WHERE sandbox_name = ?", (sandbox_name,)
        ).fetchone()
        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(
                f"UPDATE jobs SET {sets}, updated_at = ? WHERE sandbox_name = ?",
                (*fields.values(), now, sandbox_name),
            )
        else:
            cols = ["sandbox_name", "created_at", "updated_at"] + list(fields.keys())
            vals = [sandbox_name, now, now] + list(fields.values())
            placeholders = ", ".join("?" for _ in cols)
            self.conn.execute(
                f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({placeholders})", vals
            )
        self.conn.commit()

    def get(self, sandbox_name: str) -> dict | None:
        """Fetches one job row by sandbox_name, or None if unknown."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE sandbox_name = ?", (sandbox_name,)
        ).fetchone()
        if not row:
            return None
        cols = [
            d[0] for d in self.conn.execute("SELECT * FROM jobs LIMIT 0").description
        ]
        return dict(zip(cols, row))

    def all_jobs(self) -> list[dict]:
        """Fetches every job row, most recently updated first."""
        rows = self.conn.execute(
            "SELECT * FROM jobs ORDER BY updated_at DESC"
        ).fetchall()
        cols = [
            d[0] for d in self.conn.execute("SELECT * FROM jobs LIMIT 0").description
        ]
        return [dict(zip(cols, row)) for row in rows]
