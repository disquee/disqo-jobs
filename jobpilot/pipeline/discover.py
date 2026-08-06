"""Build sources from config and run discovery into the store."""

from __future__ import annotations

from ..config import load_config
from ..models import Job
from ..sources.adzuna import AdzunaSource
from ..sources.base import JobSource
from ..sources.greenhouse import GreenhouseSource
from ..sources.greenhouse import _fetch_board as _gh_board
from ..sources.jooble import JoobleSource
from ..sources.lever import LeverSource
from ..sources.lever import _fetch_postings as _lever_board
from ..store import init_db, upsert_job
from .normalize import apply_filters


def build_sources() -> list[tuple[JobSource, str, str]]:
    """Return (source, query, location) tasks to run, from config."""
    cfg = load_config()
    tasks: list[tuple[JobSource, str, str]] = []

    for s in cfg.get("searches", []):
        query, location = s.get("query", ""), s.get("location", "")
        tasks.append((AdzunaSource(), query, location))
        tasks.append((JoobleSource(), query, location))
        for slug in cfg.get("ats", {}).get("greenhouse", []):
            tasks.append((GreenhouseSource(slug), query, location))
        for slug in cfg.get("ats", {}).get("lever", []):
            tasks.append((LeverSource(slug), query, location))

    return tasks


def discover() -> dict[str, int]:
    """Run all sources, filter/dedupe, persist new jobs. Returns a summary."""
    init_db()
    # Each ATS board is cached per run so it downloads once across all queries.
    # Clear at the start so repeated runs (e.g. the dashboard) fetch fresh boards.
    _gh_board.cache_clear()
    _lever_board.cache_clear()
    cfg = load_config()
    limit = int(cfg.get("results_per_search", 25))

    raw: list[Job] = []
    skipped_sources: list[str] = []
    for source, query, location in build_sources():
        if not source.available():
            skipped_sources.append(source.name)
            continue
        raw.extend(source.search(query, location, limit))

    filtered = apply_filters(raw)
    new_count = sum(1 for job in filtered if upsert_job(job))

    return {
        "found": len(raw),
        "after_filter": len(filtered),
        "new": new_count,
        "unavailable_sources": sorted(set(skipped_sources)),
    }
