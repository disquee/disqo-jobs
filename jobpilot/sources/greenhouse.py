"""Greenhouse public job board API.

Endpoint (no auth): https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
"""

from __future__ import annotations

from functools import lru_cache

import httpx

from ..models import Job
from .base import JobSource, html_to_text

BASE = "https://boards-api.greenhouse.io/v1/boards"


@lru_cache(maxsize=None)
def _fetch_board(slug: str) -> tuple[dict, ...]:
    """Fetch a company's full board once per run; cached by slug across queries."""
    url = f"{BASE}/{slug}/jobs"
    try:
        resp = httpx.get(url, params={"content": "true"}, timeout=30)
        resp.raise_for_status()
        return tuple(resp.json().get("jobs", []))
    except (httpx.HTTPError, ValueError):
        return ()


class GreenhouseSource(JobSource):
    name = "greenhouse"

    def __init__(self, slug: str) -> None:
        self.slug = slug

    def search(self, query: str, location: str, limit: int) -> list[Job]:
        q = query.lower()
        jobs: list[Job] = []
        for r in _fetch_board(self.slug):
            title = (r.get("title") or "").strip()
            if q and q not in title.lower():
                continue
            jobs.append(
                Job(
                    source=self.name,
                    title=title,
                    company=self.slug,
                    location=(r.get("location") or {}).get("name", "").strip(),
                    description=html_to_text(r.get("content", "")),
                    apply_url=r.get("absolute_url", ""),
                    posted_at=r.get("updated_at"),
                ).ensure_id()
            )
            if len(jobs) >= limit:
                break
        return jobs
