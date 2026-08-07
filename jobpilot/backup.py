"""Automatic, dated backups of the user's job-search data.

A search runs for months, and the work-search log may be needed for unemployment
reporting long after an application is forgotten. Two failure modes to survive:

  1. The database is lost or corrupted -> keep dated copies of it.
  2. jobpilot itself is gone, or the file won't open on some future machine ->
     keep the log as CSV and XLSX too. A spreadsheet outlives the tool that
     wrote it, and it's what an agency will accept anyway.

Retention keeps every backup from the last 30 days plus the first of every
month indefinitely, so a months-long search leaves a readable trail without
filling the disk.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import BACKUP_DIR, DB_PATH, PROFILE_DIR

KEEP_DAYS = 30


def _stamp() -> str:
    return date.today().isoformat()


def backup_database(dest_dir: Optional[Path] = None) -> Optional[Path]:
    """Copy the database using SQLite's backup API.

    The API is used rather than a file copy because it's safe while the app is
    running and mid-write; a plain copy can capture a torn file.
    """
    if not DB_PATH.exists():
        return None
    dest_dir = dest_dir or BACKUP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"jobpilot-{_stamp()}.db"
    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def export_log(dest_dir: Optional[Path] = None) -> list[Path]:
    """Write the work-search log as CSV and XLSX — readable without jobpilot."""
    from .store import list_activities
    from .worklog import to_csv, to_xlsx

    activities = list_activities()
    if not activities:
        return []
    dest_dir = dest_dir or BACKUP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    written = []

    csv_path = dest_dir / f"work-search-log-{_stamp()}.csv"
    csv_path.write_text(to_csv(activities), encoding="utf-8")
    written.append(csv_path)

    xlsx_path = dest_dir / f"work-search-log-{_stamp()}.xlsx"
    xlsx_path.write_bytes(to_xlsx(activities))
    written.append(xlsx_path)
    return written


def backup_profile(dest_dir: Optional[Path] = None) -> list[Path]:
    """Copy the resume and profile — they live in the app folder, which is the
    folder most likely to be replaced on an update."""
    dest_dir = (dest_dir or BACKUP_DIR) / "profile"
    written = []
    for name in ("resume_master.md", "profile.yaml"):
        src = PROFILE_DIR / name
        if src.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / name
            shutil.copy2(src, target)
            written.append(target)
    return written


def prune(keep_days: int = KEEP_DAYS) -> int:
    """Delete day backups older than ``keep_days``, keeping month-firsts forever."""
    if not BACKUP_DIR.exists():
        return 0
    cutoff = date.today() - timedelta(days=keep_days)
    removed = 0
    for path in BACKUP_DIR.iterdir():
        if not path.is_file():
            continue
        try:  # every backup name ends in -YYYY-MM-DD
            stamp = date.fromisoformat(path.stem[-10:])
        except ValueError:
            continue
        if stamp >= cutoff or stamp.day == 1:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def run_backup() -> dict:
    """Full backup: database, log exports, profile. Safe to call repeatedly —
    same-day runs overwrite that day's files rather than piling up."""
    result: dict = {"db": None, "exports": [], "profile": [], "pruned": 0, "error": None}
    try:
        result["db"] = backup_database()
        result["exports"] = export_log()
        result["profile"] = backup_profile()
        result["pruned"] = prune()
    except Exception as e:  # a failed backup must never take the app down
        result["error"] = str(e)
    return result


def backed_up_today() -> bool:
    return (BACKUP_DIR / f"jobpilot-{_stamp()}.db").exists()


def last_backup() -> Optional[datetime]:
    if not BACKUP_DIR.exists():
        return None
    stamps = [
        datetime.fromtimestamp(p.stat().st_mtime)
        for p in BACKUP_DIR.glob("jobpilot-*.db")
    ]
    return max(stamps) if stamps else None


def days_since_backup() -> Optional[int]:
    last = last_backup()
    return None if last is None else (datetime.now() - last).days


def list_backups() -> list[dict]:
    """Newest first, for the data page."""
    if not BACKUP_DIR.exists():
        return []
    entries = []
    for path in sorted(BACKUP_DIR.glob("*"), reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        entries.append({
            "name": path.name,
            "size_kb": max(1, stat.st_size // 1024),
            "when": datetime.fromtimestamp(stat.st_mtime),
            "kind": "database" if path.suffix == ".db" else "log export",
        })
    return entries


def restore_database(source: Path) -> None:
    """Replace the live database with a backup, keeping a safety copy first."""
    source = Path(source)
    if not source.exists():
        raise RuntimeError("That backup file doesn't exist.")
    try:
        con = sqlite3.connect(source)
        con.execute("SELECT COUNT(*) FROM jobs").fetchone()
        con.close()
    except sqlite3.DatabaseError as e:
        raise RuntimeError("That file isn't a readable jobpilot database.") from e

    if DB_PATH.exists():
        safety = BACKUP_DIR / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
        safety.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DB_PATH, safety)
    shutil.copy2(source, DB_PATH)
