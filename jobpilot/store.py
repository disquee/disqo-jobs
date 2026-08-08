"""SQLite persistence for jobs + generated applications.

Two tables:
  jobs         -- one row per discovered job (json blob + a few queryable cols)
  applications -- one row per job's generated artifacts
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterable, Iterator, Optional

from .config import DB_PATH, OUTPUT_DIR, ROOT
from .models import Application, Job, Status, WorkSearchActivity

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
CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    job_id TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date);
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
    _repair_moved_paths()


def _repair_moved_paths() -> None:
    """Rewrite absolute paths saved before user data moved out of the app folder.

    Rows store paths to generated PDFs and prep pages; after the one-time move
    those strings point at a directory that no longer exists. Idempotent.
    """
    legacy = str(ROOT / "output")
    current = str(OUTPUT_DIR)
    if legacy == current:
        return
    with _conn() as c:
        for table, key in (("applications", "job_id"), ("jobs", "id")):
            rows = c.execute(
                f"SELECT {key} k, data FROM {table} WHERE data LIKE ?", (f"%{legacy}%",)
            ).fetchall()
            for row in rows:
                c.execute(
                    f"UPDATE {table} SET data = ? WHERE {key} = ?",
                    (row["data"].replace(legacy, current), row["k"]),
                )


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


def delete_jobs(job_ids: Iterable[str]) -> int:
    """Drop jobs and their generated applications. Work-search activities are
    deliberately left alone — they're the record of a search, not of a listing,
    and an agency may ask for them long after the posting is gone."""
    rows = [(job_id,) for job_id in job_ids]
    if not rows:
        return 0
    with _conn() as c:
        c.executemany("DELETE FROM applications WHERE job_id = ?", rows)
        c.executemany("DELETE FROM jobs WHERE id = ?", rows)
    return len(rows)


def counts_by_status() -> dict[str, int]:
    with _conn() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


# ---- work-search activities -------------------------------------------

def save_activity(activity: WorkSearchActivity) -> WorkSearchActivity:
    activity.ensure_id()
    with _conn() as c:
        c.execute(
            "INSERT INTO activities (id, date, job_id, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET date = excluded.date, "
            "job_id = excluded.job_id, data = excluded.data",
            (activity.id, activity.date, activity.job_id, activity.model_dump_json()),
        )
    return activity


def get_activity(activity_id: str) -> Optional[WorkSearchActivity]:
    with _conn() as c:
        row = c.execute("SELECT data FROM activities WHERE id = ?", (activity_id,)).fetchone()
    return WorkSearchActivity.model_validate_json(row["data"]) if row else None


def delete_activity(activity_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM activities WHERE id = ?", (activity_id,))


def list_activities(
    date_from: Optional[str] = None, date_to: Optional[str] = None
) -> list[WorkSearchActivity]:
    """Newest first. Dates are inclusive ISO strings (YYYY-MM-DD)."""
    sql = "SELECT data FROM activities"
    clauses, params = [], []
    if date_from:
        clauses.append("date >= ?"); params.append(date_from)
    if date_to:
        clauses.append("date <= ?"); params.append(date_to)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY date DESC, rowid DESC"
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [WorkSearchActivity.model_validate_json(r["data"]) for r in rows]


def job_ids_with_activity() -> set[str]:
    """One query instead of one per job, for checks across the whole queue."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT job_id FROM activities WHERE job_id IS NOT NULL"
        ).fetchall()
    return {r["job_id"] for r in rows}


def activity_for_job(job_id: str) -> Optional[WorkSearchActivity]:
    with _conn() as c:
        row = c.execute(
            "SELECT data FROM activities WHERE job_id = ? ORDER BY date DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    return WorkSearchActivity.model_validate_json(row["data"]) if row else None
