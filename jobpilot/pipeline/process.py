"""Orchestration: score discovered jobs and generate tailored artifacts.

Both passes persist each job as it finishes, so interrupting mid-run loses
nothing — the next run picks up whatever is still pending. The progress
callbacks exist so the person watching a multi-minute run knows that.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..config import load_config
from ..models import Application, Job, Status
from ..settings import cv_enabled_for
from ..store import get_application, list_jobs, save_application, save_job
from .fit import score_job
from .render import cover_path, cv_path, render_pdf, resume_path
from .tailor import tailor_job

#: (done, total, label) — called before each job is worked on.
Progress = Callable[[int, int, str], None]


def score_pending(on_progress: Optional[Progress] = None) -> int:
    """Score every job still in 'discovered' status. Returns count scored."""
    jobs = list_jobs(Status.discovered)
    for i, job in enumerate(jobs, start=1):
        if on_progress:
            on_progress(i, len(jobs), f"Scoring {i}/{len(jobs)} · {job.company}")
        score_job(job)
        job.status = Status.scored
        save_job(job)
    return len(jobs)


def tailor_job_full(job: Job) -> Application:
    """Generate resume/cover/answers (+ CV when on) + render PDFs, and persist."""
    app = tailor_job(job, include_cv=cv_enabled_for(job))

    rp = render_pdf(app.tailored_resume_md, resume_path(job))
    cp = render_pdf(app.cover_letter_md, cover_path(job))
    app.resume_pdf_path = str(rp)
    app.cover_pdf_path = str(cp)
    if app.tailored_cv_md:
        app.cv_pdf_path = str(render_pdf(app.tailored_cv_md, cv_path(job)))

    save_application(app)
    job.status = Status.tailored
    save_job(job)
    return app


def tailor_above_threshold(
    limit: Optional[int] = None, on_progress: Optional[Progress] = None
) -> int:
    """Tailor scored jobs at/above the configured fit threshold.

    Best fit first, so a ``limit`` — or an interruption — spends the API
    budget on the strongest matches. Each job takes several LLM calls and a
    minute or more; anything above the threshold can add up.
    """
    threshold = int(load_config().get("fit_threshold", 70))
    jobs = sorted(
        (j for j in list_jobs(Status.scored) if (j.fit_score or 0) >= threshold),
        key=lambda j: j.fit_score or 0, reverse=True,
    )
    if limit is not None:
        jobs = jobs[:max(0, limit)]
    for i, job in enumerate(jobs, start=1):
        if on_progress:
            on_progress(i, len(jobs),
                        f"Tailoring {i}/{len(jobs)} · {job.title[:40]} @ {job.company}")
        if get_application(job.id) is None:
            tailor_job_full(job)
        else:
            job.status = Status.tailored
            save_job(job)
    return len(jobs)
