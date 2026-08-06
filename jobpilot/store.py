"""SQLite persistence for jobs + generated applications.

Two tables:
  jobs         -- one row per discovered job (json blob + a few queryable cols)
  applications -- one row per job's generated artifacts
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from .config import DB_PATH
from .models import Application, Job, Status

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    fit_score INTEGER,
    title TEXT,
    company TEXT,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
"""


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


# ---- jobs -------------------------------------------------------------

def upsert_job(job: Job) -> bool:
    """Insert a job if new. Returns True if newly inserted, False if it existed."""
    with _conn() as c:
        existing = c.execute("SELECT id FROM jobs WHERE id = ?", (job.id,)).fetchone()
        if existing:
            return False
        c.execute(
            "INSERT INTO jobs (id, status, fit_score, title, company, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job.id, job.status.value, job.fit_score, job.title, job.company,
             job.model_dump_json()),
        )
        return True


def save_job(job: Job) -> None:
    """Persist updates to an existing job (status, score, etc.)."""
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET status = ?, fit_score = ?, data = ? WHERE id = ?",
            (job.status.value, job.fit_score, job.model_dump_json(), job.id),
        )


def get_job(job_id: str) -> Optional[Job]:
    with _conn() as c:
        row = c.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return Job.model_validate_json(row["data"]) if row else None


def list_jobs(status: Optional[Status] = None) -> list[Job]:
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT data FROM jobs WHERE status = ? ORDER BY fit_score DESC NULLS LAST",
                (status.value,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT data FROM jobs ORDER BY fit_score DESC NULLS LAST"
            ).fetchall()
    return [Job.model_validate_json(r["data"]) for r in rows]


# ---- applications -----------------------------------------------------

def save_application(app: Application) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO applications (job_id, data) VALUES (?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET data = excluded.data",
            (app.job_id, app.model_dump_json()),
        )


def get_application(job_id: str) -> Optional[Application]:
    with _conn() as c:
        row = c.execute(
            "SELECT data FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
    return Application.model_validate_json(row["data"]) if row else None


def counts_by_status() -> dict[str, int]:
    with _conn() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
