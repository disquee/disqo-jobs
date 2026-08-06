"""Local FastAPI review dashboard.

Workflow: review tailored jobs, edit the resume/cover/answers, then either
launch assisted-apply (a headed browser opens on your machine) or, after you
submit in that browser, click "Mark applied" to log the row to CSV.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import CSV_PATH, load_config
from ..log_csv import append_application
from ..models import Application, PrepStatus, ScreeningQA, Status
from ..pipeline.prep import ensure_job_prep, prep_html_path
from ..pipeline.process import tailor_job_full
from ..pipeline.render import cover_path, render_pdf, resume_path
from ..store import (
    get_application,
    get_job,
    init_db,
    list_jobs,
    save_application,
    save_job,
)

app = FastAPI(title="jobpilot")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    threshold = int(load_config().get("fit_threshold", 70))
    jobs = sorted(
        list_jobs(Status.tailored) + list_jobs(Status.scored) + list_jobs(Status.approved),
        key=lambda j: j.fit_score or 0,
        reverse=True,
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {"jobs": jobs, "threshold": threshold, "PrepStatus": PrepStatus},
    )


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str):
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)
    application = get_application(job_id)
    screening_json = "[]"
    if application:
        screening_json = json.dumps(
            [qa.model_dump() for qa in application.screening], indent=2
        )
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "job": job,
            "app": application,
            "screening_json": screening_json,
            "PrepStatus": PrepStatus,
            "prep_exists": prep_html_path(job).exists(),
        },
    )


@app.post("/job/{job_id}/prep")
def do_prep(job_id: str, regenerate: str = Form("")):
    """Create (or rebuild) the interview-prep page for this job, then open it."""
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)
    written = ensure_job_prep(job, get_application(job_id), regenerate=bool(regenerate))
    job.prep_json_path = str(written["html"].with_suffix(".json"))
    job.prep_html_path = str(written["html"])
    if job.prep_status == PrepStatus.none:
        job.prep_status = PrepStatus.started
    save_job(job)
    return RedirectResponse(f"/job/{job_id}/prep", status_code=303)


@app.get("/job/{job_id}/prep")
def open_prep(job_id: str):
    """Serve the generated prep page for this job."""
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)
    path = prep_html_path(job)
    if not path.exists():
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    return FileResponse(path, media_type="text/html")


@app.post("/job/{job_id}/prep-status")
def set_prep_status(job_id: str, value: str = Form("started"), back: str = Form("")):
    """Mark prep complete (or roll it back)."""
    job = get_job(job_id)
    if job:
        try:
            job.prep_status = PrepStatus(value)
        except ValueError:
            pass
        save_job(job)
    return RedirectResponse(back or f"/job/{job_id}", status_code=303)


@app.post("/job/{job_id}/tailor")
def do_tailor(job_id: str):
    job = get_job(job_id)
    if job:
        tailor_job_full(job)
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@app.post("/job/{job_id}/save")
def do_save(
    job_id: str,
    resume_md: str = Form(""),
    cover_md: str = Form(""),
    screening_json: str = Form("[]"),
):
    job = get_job(job_id)
    application = get_application(job_id)
    if not job or not application:
        return RedirectResponse(f"/job/{job_id}", status_code=303)

    application.tailored_resume_md = resume_md
    application.cover_letter_md = cover_md
    try:
        pairs = json.loads(screening_json)
        application.screening = [
            ScreeningQA(question=p.get("question", ""), answer=p.get("answer", ""))
            for p in pairs
        ]
    except Exception:
        pass

    # Re-render PDFs from edited content.
    application.resume_pdf_path = str(render_pdf(resume_md, resume_path(job)))
    application.cover_pdf_path = str(render_pdf(cover_md, cover_path(job)))
    save_application(application)
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@app.post("/job/{job_id}/skip")
def do_skip(job_id: str):
    job = get_job(job_id)
    if job:
        job.status = Status.skipped
        save_job(job)
    return RedirectResponse("/", status_code=303)


@app.post("/job/{job_id}/launch")
def do_launch(job_id: str):
    """Open the assisted-apply browser in a background thread (headed)."""
    job = get_job(job_id)
    application = get_application(job_id)
    if job and application:
        job.status = Status.approved
        save_job(job)

        def _run():
            from ..apply.autofill import assisted_apply

            assisted_apply(job, application)

        threading.Thread(target=_run, daemon=True).start()
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@app.post("/job/{job_id}/mark-applied")
def do_mark_applied(job_id: str):
    job = get_job(job_id)
    application = get_application(job_id)
    if job and application:
        application.date_applied = date.today().isoformat()
        save_application(application)
        append_application(job, application)
        job.status = Status.applied
        save_job(job)
    return RedirectResponse("/", status_code=303)


@app.get("/applied", response_class=HTMLResponse)
def applied(request: Request):
    jobs = list_jobs(Status.applied)
    return templates.TemplateResponse(
        request, "applied.html", {"jobs": jobs, "csv_path": str(CSV_PATH)}
    )
