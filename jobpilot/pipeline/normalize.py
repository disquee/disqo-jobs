"""Filter + dedupe normalized jobs against config rules."""

from __future__ import annotations

from ..config import load_config
from ..models import Job


def apply_filters(jobs: list[Job]) -> list[Job]:
    cfg = load_config()
    bad_titles = [k.lower() for k in cfg.get("exclude_title_keywords", [])]
    bad_company = [c.lower() for c in cfg.get("exclude_company", [])]

    kept: list[Job] = []
    seen: set[str] = set()
    for job in jobs:
        if not job.title or not job.apply_url:
            continue
        title_l = job.title.lower()
        company_l = job.company.lower()
        if any(k in title_l for k in bad_titles):
            continue
        if any(c in company_l for c in bad_company):
            continue
        if job.id in seen:
            continue
        seen.add(job.id)
        kept.append(job)
    return kept
