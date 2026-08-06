"""Orchestration: score discovered jobs and generate tailored artifacts."""

from __future__ import annotations

from ..config import load_config
from ..models import Application, Job, Status
from ..store import get_application, list_jobs, save_application, save_job
from .fit import score_job
from .render import cover_path, render_pdf, resume_path
from .tailor import tailor_job


def score_pending() -> int:
    """Score every job still in 'discovered' status. Returns count scored."""
    jobs = list_jobs(Status.discovered)
    for job in jobs:
        score_job(job)
        job.status = Status.scored
        save_job(job)
    return len(jobs)


def tailor_job_full(job: Job) -> Application:
    """Generate resume/cover/answers + render PDFs for one job, and persist."""
    app = tailor_job(job)

    rp = render_pdf(app.tailored_resume_md, resume_path(job))
    cp = render_pdf(app.cover_letter_md, cover_path(job))
    app.resume_pdf_path = str(rp)
    app.cover_pdf_path = str(cp)

    save_application(app)
    job.status = Status.tailored
    save_job(job)
    return app


def tailor_above_threshold() -> int:
    """Tailor all scored jobs at/above the configured fit threshold."""
    threshold = int(load_config().get("fit_threshold", 70))
    jobs = [
        j for j in list_jobs(Status.scored)
        if (j.fit_score or 0) >= threshold
    ]
    for job in jobs:
        if get_application(job.id) is None:
            tailor_job_full(job)
        else:
            job.status = Status.tailored
            save_job(job)
    return len(jobs)
