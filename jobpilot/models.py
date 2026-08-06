"""Core data models for jobpilot."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    """Lifecycle of a job in the pipeline."""

    discovered = "discovered"   # pulled from a source, normalized
    scored = "scored"           # fit score computed
    tailored = "tailored"       # resume/cover/answers generated
    approved = "approved"       # user approved in dashboard
    applied = "applied"         # submitted (logged to CSV)
    skipped = "skipped"         # user dismissed


class PrepStatus(str, Enum):
    """Interview-prep progress for a job, tracked alongside the application."""

    none = "none"           # no prep page generated yet
    started = "started"     # page generated; content being filled in
    complete = "complete"   # user marked prep done


class Job(BaseModel):
    """A normalized job posting from any source."""

    id: str = ""               # stable hash; filled by ensure_id()
    source: str                # "adzuna" | "jooble" | "greenhouse" | "lever"
    title: str
    company: str
    location: str = ""
    description: str = ""       # full posting text (plain text)
    apply_url: str = ""
    salary: Optional[str] = None
    posted_at: Optional[str] = None
    discovered_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    status: Status = Status.discovered
    fit_score: Optional[int] = None
    fit_rationale: Optional[str] = None

    # Interview prep (see pipeline/prep.py). Paths are relative to the repo root
    # when possible so a moved checkout still resolves them.
    prep_status: PrepStatus = PrepStatus.none
    prep_json_path: Optional[str] = None
    prep_html_path: Optional[str] = None

    def ensure_id(self) -> "Job":
        """Compute a stable dedupe id from source+company+title+url."""
        if not self.id:
            basis = f"{self.source}|{self.company}|{self.title}|{self.apply_url}".lower()
            self.id = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        return self


class ScreeningQA(BaseModel):
    question: str
    answer: str


class Application(BaseModel):
    """Generated artifacts + final record for one job."""

    job_id: str
    tailored_resume_md: str = ""
    cover_letter_md: str = ""
    screening: list[ScreeningQA] = Field(default_factory=list)
    resume_pdf_path: Optional[str] = None
    cover_pdf_path: Optional[str] = None
    date_applied: Optional[str] = None
