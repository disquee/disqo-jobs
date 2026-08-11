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


# Pipeline states are engineering vocabulary. Users think in terms of what they
# have to do next, so the UI never shows the raw enum.
STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "discovered": ("Saved", ""),
    "scored": ("Saved", ""),
    "tailored": ("Ready to apply", "accent"),
    "approved": ("Applying", "accent"),
    "applied": ("Applied", "ok"),
    "skipped": ("Not pursuing", ""),
}


def display_status(status: "Status", interviewing: bool = False) -> tuple[str, str]:
    """(label, tone) for a job. ``interviewing`` comes from the work-search log,
    which is the only place that fact is recorded."""
    if interviewing:
        return ("Interviewing", "ok")
    return STATUS_DISPLAY.get(getattr(status, "value", str(status)), ("Saved", ""))


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

    # Per-job CV override. None means follow the generate_cv setting; True/False
    # is an explicit choice made on this job's page.
    cv_enabled: Optional[bool] = None

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


class ActivityType(str, Enum):
    """What the job seeker actually did. Values are the labels agencies expect."""

    application = "Submitted application"
    interview = "Interviewed"
    contact = "Contacted employer"
    networking = "Attended networking event"
    job_fair = "Attended job fair"
    workshop = "Attended workshop or training"
    other = "Other"


class ActivityResult(str, Enum):
    pending = "Pending"
    interviewing = "Interviewing"
    rejected = "Rejected"
    offered = "Offered"
    no_response = "No response"
    withdrawn = "Withdrawn"


def job_progress(activities: list["WorkSearchActivity"]) -> tuple[str, str]:
    """(label, tone) for where a candidacy stands, read off its logged activities.

    The latest result decides: an offer or a rejection ends the story, and
    anything else counts the interview rounds so far. Kept as a pure function
    of the log because the log is already the record users maintain.
    """
    if not activities:
        return ("Nothing logged yet", "")
    latest = activities[-1].result
    if latest == ActivityResult.offered:
        return ("Offer", "ok")
    if latest == ActivityResult.rejected:
        return ("Declined", "")
    if latest == ActivityResult.withdrawn:
        return ("Withdrawn", "")
    rounds = sum(1 for a in activities if a.activity_type == ActivityType.interview)
    if rounds:
        return (f"Interviewing, round {rounds}", "ok")
    return ("Applied, waiting to hear", "accent")


class WorkSearchActivity(BaseModel):
    """One dated work-search action, for unemployment reporting.

    Deliberately not tied to a Job: agencies count networking events, job fairs
    and cold outreach too, none of which have a posting behind them.
    """

    id: str = ""
    date: str = Field(default_factory=lambda: datetime.now().date().isoformat())
    company: str = ""
    position: str = ""
    contact: str = ""            # name / phone / email / website, free text
    activity_type: ActivityType = ActivityType.application
    result: ActivityResult = ActivityResult.pending
    notes: str = ""
    job_id: Optional[str] = None  # set when it came from the pipeline
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    def ensure_id(self) -> "WorkSearchActivity":
        if not self.id:
            basis = f"{self.date}|{self.company}|{self.position}|{self.activity_type.value}|{self.created_at}"
            self.id = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        return self


class Interviewer(BaseModel):
    """One person on an interview loop."""

    name: str = ""
    role: str = ""            # their title
    focus: str = ""           # what this conversation covers
    linkedin: str = ""
    when: str = ""            # "Interview 1 of 3", or a date/time


class PrepPlan(BaseModel):
    """What's known about an upcoming loop, before the prep page is built.

    Usually assembled from the email a recruiter sends, which is where this
    information actually lives.
    """

    job_id: str = ""
    interviewers: list[Interviewer] = Field(default_factory=list)
    format: str = ""          # video / onsite / phone
    scheduled: str = ""
    duration: str = ""        # "45 minutes each"
    recruiter: str = ""
    competencies: list[str] = Field(default_factory=list)
    notes: str = ""
    source_text: str = ""     # the pasted email, kept for reference


class ScreeningQA(BaseModel):
    question: str
    answer: str


class Application(BaseModel):
    """Generated artifacts + final record for one job."""

    job_id: str
    tailored_resume_md: str = ""
    tailored_cv_md: str = ""     # full-length CV; empty unless CV is on for the job
    cover_letter_md: str = ""
    screening: list[ScreeningQA] = Field(default_factory=list)
    resume_pdf_path: Optional[str] = None
    cv_pdf_path: Optional[str] = None
    cover_pdf_path: Optional[str] = None
    date_applied: Optional[str] = None
