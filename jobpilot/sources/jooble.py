"""Jooble job search API client. Docs: https://jooble.org/api/about"""

from __future__ import annotations

import httpx

from ..config import env
from ..models import Job
from .base import JobSource, html_to_text

BASE = "https://jooble.org/api"


class JoobleSource(JobSource):
    name = "jooble"

    def __init__(self) -> None:
        self.api_key = env("JOOBLE_API_KEY")

    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, location: str, limit: int) -> list[Job]:
        if not self.available():
            return []
        payload = {"keywords": query, "location": location}
        try:
            resp = httpx.post(f"{BASE}/{self.api_key}", json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []

        jobs: list[Job] = []
        for r in data.get("jobs", [])[:limit]:
            jobs.append(
                Job(
                    source=self.name,
                    title=(r.get("title") or "").strip(),
                    company=(r.get("company") or "").strip(),
                    location=(r.get("location") or "").strip(),
                    description=html_to_text(r.get("snippet", "")),
                    apply_url=r.get("link", ""),
                    salary=r.get("salary") or None,
                    posted_at=r.get("updated"),
                ).ensure_id()
            )
        return jobs
