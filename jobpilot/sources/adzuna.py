"""Adzuna job search API client. Docs: https://developer.adzuna.com/"""

from __future__ import annotations

import httpx

from ..config import env
from ..models import Job
from .base import JobSource, clean_company, html_to_text

BASE = "https://api.adzuna.com/v1/api/jobs"


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(self, country: str = "us") -> None:
        self.country = country
        self.app_id = env("ADZUNA_APP_ID")
        self.app_key = env("ADZUNA_APP_KEY")

    def available(self) -> bool:
        return bool(self.app_id and self.app_key)

    def search(self, query: str, location: str, limit: int) -> list[Job]:
        if not self.available():
            return []
        # Adzuna's `where` expects a geographic place and returns nothing for
        # "Remote". Fold a remote intent into the keyword instead.
        what, where = query, location
        if location.strip().lower() == "remote":
            what, where = f"{query} remote".strip(), ""
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": what,
            "where": where,
            "results_per_page": min(limit, 50),
            "content-type": "application/json",
        }
        url = f"{BASE}/{self.country}/search/1"
        try:
            resp = httpx.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []

        jobs: list[Job] = []
        for r in data.get("results", [])[:limit]:
            jobs.append(
                Job(
                    source=self.name,
                    title=r.get("title", "").strip(),
                    company=clean_company((r.get("company") or {}).get("display_name", "")),
                    location=(r.get("location") or {}).get("display_name", "").strip(),
                    description=html_to_text(r.get("description", "")),
                    apply_url=r.get("redirect_url", ""),
                    salary=_salary(r),
                    posted_at=r.get("created"),
                ).ensure_id()
            )
        return jobs


def _salary(r: dict) -> str | None:
    lo, hi = r.get("salary_min"), r.get("salary_max")
    if lo and hi:
        return f"${int(lo):,} - ${int(hi):,}"
    return None
