"""Lever public postings API.

Endpoint (no auth): https://api.lever.co/v0/postings/{slug}?mode=json
"""

from __future__ import annotations

from functools import lru_cache

import httpx

from ..models import Job
from .base import JobSource, html_to_text

BASE = "https://api.lever.co/v0/postings"


@lru_cache(maxsize=None)
def _fetch_postings(slug: str) -> tuple[dict, ...]:
    """Fetch a company's full postings once per run; cached by slug across queries."""
    url = f"{BASE}/{slug}"
    try:
        resp = httpx.get(url, params={"mode": "json"}, timeout=30)
        resp.raise_for_status()
        return tuple(resp.json())
    except (httpx.HTTPError, ValueError):
        return ()


class LeverSource(JobSource):
    name = "lever"

    def __init__(self, slug: str) -> None:
        self.slug = slug

    def search(self, query: str, location: str, limit: int) -> list[Job]:
        q = query.lower()
        jobs: list[Job] = []
        for r in _fetch_postings(self.slug):
            title = (r.get("text") or "").strip()
            if q and q not in title.lower():
                continue
            categories = r.get("categories") or {}
            jobs.append(
                Job(
                    source=self.name,
                    title=title,
                    company=self.slug,
                    location=categories.get("location", "").strip(),
                    description=html_to_text(r.get("descriptionPlain") or r.get("description", "")),
                    apply_url=r.get("hostedUrl") or r.get("applyUrl", ""),
                    posted_at=str(r.get("createdAt", "")) or None,
                ).ensure_id()
            )
            if len(jobs) >= limit:
                break
        return jobs
