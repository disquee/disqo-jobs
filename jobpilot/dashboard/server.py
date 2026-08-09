"""Local FastAPI review dashboard.

Workflow: review tailored jobs, edit the resume/cover/answers, then either
launch assisted-apply (a headed browser opens on your machine) or, after you
submit in that browser, click "Mark applied" to log the row to CSV.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import BACKUP_DIR, CSV_PATH, DATA_DIR, DB_PATH, PROFILE_DIR, ROOT, load_config
from ..backup import (
    backed_up_today,
    days_since_backup,
    last_backup,
    list_backups,
    restore_database,
    run_backup,
)
from ..log_csv import append_application
from ..models import (
    ActivityResult,
    Interviewer,
    Job,
    PrepPlan,
    display_status,
    ActivityType,
    Application,
    PrepStatus,
    ScreeningQA,
    Status,
    WorkSearchActivity,
)
from ..worklog import to_csv, to_xlsx, weekly_counts
from ..ingest import (
    fetch_posting, parse_competencies, parse_interviewers, resume_text, spreadsheet_rows,
)
from ..searchconfig import current as config_current, save as config_save
from ..settings import (
    ENV_KEYS,
    check_llm,
    claude_cli_path,
    local_models,
    LLM_MODES,
    env_status,
    set_env,
    has_api_key,
    lan_ip,
    load_settings,
    needs_setup,
    profile_ready,
    save_settings,
    set_api_key,
)
from ..pipeline import fit as fit_mod
from ..pipeline import tailor as tailor_mod
from ..pipeline.discover import discover as run_discover
from ..pipeline.process import score_pending, tailor_above_threshold
from ..usage import estimate_per_job, set_rates, summary as usage_summary
from ..worklog import week_start
from . import tasks
from ..pipeline.prep import (
    ensure_job_prep, load_plan, prep_html_path, prep_json_path, prep_plan_path,
    render_prep_file, save_plan,
)
from ..pipeline.process import tailor_job_full
from ..pipeline.render import cover_path, render_pdf, resume_path
from ..llm import LOCAL_BASE_URL_DEFAULT
from ..store import (
    activity_for_job,
    upsert_job,
    delete_activity,
    delete_jobs,
    get_activity,
    job_ids_with_activity,
    get_application,
    get_job,
    init_db,
    list_activities,
    list_jobs,
    save_activity,
    save_application,
    save_job,
)

app = FastAPI(title="jobpilot")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


def _parse_date(value: str):
    """Sources send several shapes: bare dates, trailing Z, 7-digit fractions."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)  # trim over-long fractions
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10])
        except ValueError:
            return None


def job_date(job) -> dict:
    """When the job was posted, or failing that when we found it.

    Age matters in a job search — a two-month-old posting is often already
    filled — so recent dates read as relative and older ones as absolute.
    """
    raw, posted = job.posted_at, True
    if not raw:
        raw, posted = job.discovered_at, False
    dt = _parse_date(raw)
    if dt is None:
        return {"label": "—", "sort": "", "title": "", "stale": False, "days": None}

    today = date.today()
    days = (today - dt.date()).days
    if days < 0:
        label = dt.strftime("%b %d").replace(" 0", " ")
    elif days == 0:
        label = "Today"
    elif days == 1:
        label = "Yesterday"
    elif days < 7:
        label = f"{days}d ago"
    elif days < 30:
        label = f"{days // 7}w ago"
    elif dt.year == today.year:
        label = dt.strftime("%b %d").replace(" 0", " ")
    else:
        label = dt.strftime("%b %Y")

    kind = "Posted" if posted else "Found by disqo jobs"
    return {
        "label": label,
        "sort": dt.date().isoformat(),
        "title": f"{kind} {dt.date().isoformat()}"
                 + (f" · {days} days ago" if days > 0 else ""),
        "stale": days > 45,
        "posted": posted,
        "days": days,
    }


templates.env.globals["job_date"] = job_date


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # A job search runs for months; back up on every launch so a lost or
    # corrupted database costs at most a day.
    if not backed_up_today():
        threading.Thread(target=run_backup, daemon=True).start()


def _prep_is_placeholder(job) -> bool:
    """True when the page exists but nothing has been written into it yet."""
    path = prep_json_path(job)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    items = [i for s in data.get("sections", []) if s.get("kind") == "qa"
             for i in s.get("items", [])]
    if not items:
        return True
    return all("Add a short memory jog" in (i.get("label") or "") for i in items)


def _interviewing_ids() -> set[str]:
    """Job ids with an interview logged — the only place that fact lives."""
    return {
        a.job_id for a in list_activities()
        if a.job_id and a.result == ActivityResult.interviewing
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """What to do next, rather than a table of everything."""
    threshold = int(load_config().get("fit_threshold", 70))
    scored = list_jobs(Status.scored)
    ready = list_jobs(Status.tailored)
    applying = list_jobs(Status.approved)
    applied = list_jobs(Status.applied)
    interviewing = _interviewing_ids()

    worth_review = sorted(
        [j for j in scored if (j.fit_score or 0) >= threshold],
        key=lambda j: j.fit_score or 0, reverse=True,
    )
    activities = list_activities()
    this_week = week_start(date.today().isoformat())
    week_count = sum(1 for a in activities if a.date >= this_week)
    needs_prep = [j for j in applied + applying
                  if j.prep_status != PrepStatus.complete][:5]

    return templates.TemplateResponse(
        request, "home.html",
        {
            "threshold": threshold,
            "worth_review": worth_review[:5],
            "worth_review_total": len(worth_review),
            "ready": ready[:5], "ready_total": len(ready),
            "applying_total": len(applying),
            "applied_total": len(applied),
            "interviewing_total": len(interviewing),
            "week_count": week_count, "week_start": this_week,
            "needs_prep": needs_prep,
            "total_jobs": len(list_jobs()),
            "usage": usage_summary(),
            "per_job": estimate_per_job(),
            "discovering": tasks.is_running("discover"),
            "settings": load_settings(),
            "display_status": display_status,
            "interviewing": interviewing,
        },
    )


#: Ages offered by "Clear out old jobs" on My jobs, and the one picked by default.
CLEANUP_AGES = (30, 45, 60, 90, 180)
CLEANUP_DEFAULT_AGE = 90


def _queue_jobs() -> list:
    """What My jobs lists: still-open work first, then what's already sent."""
    active = list_jobs(Status.tailored) + list_jobs(Status.scored) + list_jobs(Status.approved)
    done = list_jobs(Status.applied)
    return sorted(active, key=lambda j: j.fit_score or 0, reverse=True) + sorted(
        done, key=lambda j: j.fit_score or 0, reverse=True
    )


def _clearable(jobs: list, days: int) -> list:
    """Queue entries at least `days` old that carry nothing worth keeping.

    A posting is disposable; a record of what you did about it is not. Anything
    applied to, prepped for, or written into the work-search log stays put, and
    so does anything whose date we never learned.
    """
    logged = job_ids_with_activity()
    return [
        job for job in jobs
        if (job_date(job)["days"] or 0) >= days
        and job.status != Status.applied
        and job.prep_status == PrepStatus.none
        and job.id not in logged
    ]


def _remove_jobs(jobs: list) -> int:
    """Delete jobs, their generated applications, and the files behind them.

    Without the files, clearing a few hundred listings would leave a few hundred
    orphaned PDFs behind. A file we can't delete isn't worth failing over — the
    record going away is what the user asked for.
    """
    for job in jobs:
        for path in (resume_path(job), cover_path(job), prep_html_path(job),
                     prep_json_path(job), prep_plan_path(job)):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
    return delete_jobs(job.id for job in jobs)


@app.get("/jobs", response_class=HTMLResponse)
def index(request: Request, q: str = "", removed: int = 0):
    cfg = load_config()
    threshold = int(cfg.get("fit_threshold", 70))
    # Applied jobs stay listed so the ✓ is visible at a glance, but sort under
    # everything still needing action.
    jobs = _queue_jobs()
    cleanup = [(days, len(_clearable(jobs, days))) for days in CLEANUP_AGES]
    return templates.TemplateResponse(
        request,
        "index.html",
        {"jobs": jobs, "threshold": threshold, "PrepStatus": PrepStatus, "Status": Status,
         "settings": load_settings(), "display_status": display_status,
         "interviewing": _interviewing_ids(), "q": q,
         # Searching for jobs lives at the top of this page.
         "task": tasks.status("discover"),
         "searches": cfg.get("searches", []),
         "companies": (cfg.get("ats", {}) or {}).get("greenhouse", []),
         "per_job": estimate_per_job(),
         # …and clearing out what it turned up months ago folds away above the table.
         "cleanup": cleanup,
         "cleanup_age": CLEANUP_DEFAULT_AGE,
         "cleanup_default": dict(cleanup).get(CLEANUP_DEFAULT_AGE, 0),
         "removed": removed},
    )


@app.post("/jobs/cleanup")
def jobs_cleanup(days: int = Form(90)):
    if days not in CLEANUP_AGES:
        return RedirectResponse("/jobs", status_code=303)
    removed = _remove_jobs(_clearable(_queue_jobs(), days))
    return RedirectResponse(f"/jobs?removed={removed}", status_code=303)


@app.post("/job/{job_id}/remove")
def job_remove(job_id: str):
    """Drop one listing. Unlike the bulk clear this takes whatever you point it
    at — you're naming this job specifically — but the work-search log entry
    behind it still survives, so the reportable record is never the casualty."""
    job = get_job(job_id)
    removed = _remove_jobs([job]) if job else 0
    return RedirectResponse(f"/jobs?removed={removed}", status_code=303)


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str, err: str = ""):
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
            "prep_plan": load_plan(job),
            "prep_is_placeholder": _prep_is_placeholder(job),
            "err": err,
            "display_status": display_status,
            "settings": load_settings(),
        },
    )


@app.get("/job/{job_id}/prep/new", response_class=HTMLResponse)
def prep_new(request: Request, job_id: str, err: str = "", parsed: str = ""):
    """Walk through who you're meeting before building the page."""
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)
    plan = load_plan(job)
    if parsed:
        try:
            payload = json.loads(parsed)
            plan.interviewers = [Interviewer(**p) for p in payload.get("interviewers", [])]
            plan.competencies = payload.get("competencies", [])
            plan.source_text = payload.get("source_text", "")
        except Exception:
            pass
    return templates.TemplateResponse(
        request, "prep_new.html",
        {"job": job, "plan": plan, "err": err,
         "existing": prep_html_path(job).exists(), "settings": load_settings()},
    )


@app.post("/job/{job_id}/prep/parse")
async def prep_parse(
    job_id: str, pasted: str = Form(""), file: UploadFile = File(None),
):
    """Pull the loop out of a pasted recruiter email, a document, or a sheet."""
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)

    text, people = pasted or "", []
    if file is not None and file.filename:
        data = await file.read()
        name = file.filename.lower()
        try:
            if name.endswith((".csv", ".tsv", ".xlsx")):
                rows = spreadsheet_rows(file.filename, data)
                header = [c.lower() for c in rows[0]] if rows else []
                has_header = any(h in ("name", "interviewer") for h in header)
                for row in rows[1:] if has_header else rows:
                    if not row or not row[0].strip():
                        continue
                    people.append({
                        "name": row[0].strip(),
                        "role": row[1].strip() if len(row) > 1 else "",
                        "focus": row[2].strip() if len(row) > 2 else "",
                        "linkedin": next((c for c in row if "linkedin.com" in c.lower()), ""),
                        "when": "",
                    })
            else:
                text = resume_text(file.filename, data)  # same extractors
        except RuntimeError as e:
            from urllib.parse import quote

            return RedirectResponse(f"/job/{job_id}/prep/new?err={quote(str(e))}", status_code=303)

    if not people:
        people = parse_interviewers(text)
    if not people and not text.strip():
        return RedirectResponse(
            f"/job/{job_id}/prep/new?err=Paste the email or upload a file first.",
            status_code=303,
        )
    for i, person in enumerate(people, start=1):
        if not person.get("when"):
            person["when"] = f"Interview {i} of {len(people)}" if len(people) > 1 else "Interview"

    from urllib.parse import quote

    payload = json.dumps({"interviewers": people,
                          "competencies": parse_competencies(text),
                          "source_text": text[:8000]})
    return RedirectResponse(
        f"/job/{job_id}/prep/new?parsed={quote(payload)}", status_code=303)


@app.post("/job/{job_id}/prep/plan")
def prep_save_plan(
    job_id: str,
    name: list[str] = Form([]), role: list[str] = Form([]),
    focus: list[str] = Form([]), linkedin: list[str] = Form([]),
    when: list[str] = Form([]),
    format_: str = Form("", alias="format"), scheduled: str = Form(""),
    duration: str = Form(""), recruiter: str = Form(""),
    competencies: str = Form(""), source_text: str = Form(""),
):
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)

    people = []
    for i, n in enumerate(name):
        if not n.strip():
            continue
        people.append(Interviewer(
            name=n.strip(),
            role=role[i].strip() if i < len(role) else "",
            focus=focus[i].strip() if i < len(focus) else "",
            linkedin=linkedin[i].strip() if i < len(linkedin) else "",
            when=when[i].strip() if i < len(when) else "",
        ))
    plan = PrepPlan(
        job_id=job.id, interviewers=people, format=format_.strip(),
        scheduled=scheduled.strip(), duration=duration.strip(),
        recruiter=recruiter.strip(), source_text=source_text[:8000],
        competencies=[c.strip() for c in competencies.splitlines() if c.strip()],
    )
    save_plan(job, plan)
    written = ensure_job_prep(job, get_application(job_id), regenerate=True)
    job.prep_json_path = str(written["html"].with_suffix(".json"))
    job.prep_html_path = str(written["html"])
    if job.prep_status == PrepStatus.none:
        job.prep_status = PrepStatus.started
    save_job(job)
    return RedirectResponse(f"/job/{job_id}/prep", status_code=303)


@app.post("/job/{job_id}/prep")
def do_prep(job_id: str, regenerate: str = Form("")):
    """Create (or rebuild) the interview-prep page for this job, then open it."""
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)
    if not regenerate and not prep_html_path(job).exists():
        return RedirectResponse(f"/job/{job_id}/prep/new", status_code=303)
    written = ensure_job_prep(job, get_application(job_id), regenerate=bool(regenerate))
    job.prep_json_path = str(written["html"].with_suffix(".json"))
    job.prep_html_path = str(written["html"])
    if job.prep_status == PrepStatus.none:
        job.prep_status = PrepStatus.started
    save_job(job)
    return RedirectResponse(f"/job/{job_id}/prep", status_code=303)


@app.get("/job/{job_id}/prep")
def open_prep(job_id: str):
    """Serve the generated prep page, rebuilding it if the app has moved on.

    Pages are files on disk, so a template change would otherwise leave everyone
    on the version generated the day they created it.
    """
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)
    path = prep_html_path(job)
    if not path.exists():
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    try:
        from ..pipeline.prep import TEMPLATE

        if TEMPLATE.stat().st_mtime > path.stat().st_mtime:
            ensure_job_prep(job, get_application(job_id))
    except Exception:
        pass  # a stale page still beats an error page
    return FileResponse(path, media_type="text/html")


@app.get("/job/{job_id}/prep/edit", response_class=HTMLResponse)
def prep_edit(request: Request, job_id: str, err: str = "", msg: str = ""):
    """Edit the parts of a prep page the walkthrough doesn't cover."""
    job = get_job(job_id)
    if not job or not prep_json_path(job).exists():
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    data = json.loads(prep_json_path(job).read_text(encoding="utf-8"))
    panels = [s for s in data["sections"] if s.get("kind") == "qa"]
    numbers = next((s for s in data["sections"] if s.get("kind") == "numbers"), {"rows": []})
    return templates.TemplateResponse(
        request, "prep_edit.html",
        {"job": job, "data": data, "panels": panels,
         "stories": data.get("stories", []), "numbers": numbers.get("rows", []),
         "raw": json.dumps(data, indent=2), "err": err, "msg": msg,
         "settings": load_settings()},
    )


@app.post("/job/{job_id}/prep/edit")
def prep_edit_save(
    job_id: str,
    story_id: list[str] = Form([]), story_label: list[str] = Form([]),
    story_metric: list[str] = Form([]),
    num_v: list[str] = Form([]), num_s: list[str] = Form([]), num_w: list[str] = Form([]),
    q_panel: list[str] = Form([]), q_id: list[str] = Form([]), q_label: list[str] = Form([]),
    q_text: list[str] = Form([]), q_s: list[str] = Form([]), q_t: list[str] = Form([]),
    q_a: list[str] = Form([]), q_r: list[str] = Form([]), q_punch: list[str] = Form([]),
):
    job = get_job(job_id)
    path = prep_json_path(job) if job else None
    if not job or not path.exists():
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    data = json.loads(path.read_text(encoding="utf-8"))

    stories = []
    for i, sid in enumerate(story_id):
        label = story_label[i].strip() if i < len(story_label) else ""
        if not label:
            continue
        stories.append({"id": sid.strip() or f"S{i + 1}", "label": label,
                        "metric": story_metric[i].strip() if i < len(story_metric) else "",
                        "rec": []})
    if stories:
        data["stories"] = stories

    rows = []
    for i, v in enumerate(num_v):
        if not v.strip():
            continue
        rows.append({"v": v.strip(),
                     "s": num_s[i].strip() if i < len(num_s) else "",
                     "w": num_w[i].strip() if i < len(num_w) else ""})
    for section in data["sections"]:
        if section.get("kind") == "numbers" and rows:
            section["rows"] = rows

    by_panel: dict[str, list[dict]] = {}
    for i, panel in enumerate(q_panel):
        text = q_text[i].strip() if i < len(q_text) else ""
        if not text:
            continue
        item = {
            "id": (q_id[i].strip() if i < len(q_id) else "") or f"Q{i + 1}",
            "tag": "STAR", "label": q_label[i].strip() if i < len(q_label) else "",
            "q": text, "stories": [],
            "star": {"s": q_s[i] if i < len(q_s) else "", "t": q_t[i] if i < len(q_t) else "",
                     "a": q_a[i] if i < len(q_a) else "", "r": q_r[i] if i < len(q_r) else ""},
        }
        if i < len(q_punch) and q_punch[i].strip():
            item["punch"] = {"label": "The line", "text": q_punch[i].strip()}
        by_panel.setdefault(panel, []).append(item)
    for section in data["sections"]:
        if section.get("kind") == "qa" and section["id"] in by_panel:
            section["items"] = by_panel[section["id"]]

    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    render_prep_file(path, prep_html_path(job))
    return RedirectResponse(f"/job/{job_id}/prep/edit?msg=Saved and rebuilt.", status_code=303)


@app.post("/job/{job_id}/prep/raw")
def prep_edit_raw(job_id: str, raw: str = Form("")):
    """Escape hatch: edit the whole document, validated before it's kept."""
    job = get_job(job_id)
    path = prep_json_path(job) if job else None
    if not job or not path.exists():
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    try:
        data = json.loads(raw)
        ids = {s["id"] for s in data["sections"]}
        missing = [s for s in data["meta"]["order"] if s not in ids]
        if missing:
            raise ValueError("meta.order lists sections that don't exist: " + ", ".join(missing))
    except Exception as e:
        from urllib.parse import quote

        return RedirectResponse(f"/job/{job_id}/prep/edit?err={quote(str(e)[:200])}", status_code=303)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    render_prep_file(path, prep_html_path(job))
    return RedirectResponse(f"/job/{job_id}/prep/edit?msg=Saved and rebuilt.", status_code=303)


@app.post("/job/{job_id}/prep/delete")
def prep_delete(job_id: str):
    """Start over. Files are renamed rather than deleted, so nothing is lost."""
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)
    from datetime import datetime as _dt

    stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
    for path in (prep_json_path(job), prep_html_path(job), prep_plan_path(job)):
        if path.exists():
            path.rename(path.with_name(f"{path.stem}.removed-{stamp}{path.suffix}"))
    job.prep_status = PrepStatus.none
    job.prep_json_path = job.prep_html_path = None
    save_job(job)
    return RedirectResponse(f"/job/{job_id}", status_code=303)


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
    if not job:
        return RedirectResponse("/", status_code=303)
    if load_settings().is_manual:
        return RedirectResponse(f"/job/{job_id}/manual/tailor", status_code=303)
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
        # Record it in the work-search log unless this job is already logged.
        if not activity_for_job(job.id):
            save_activity(WorkSearchActivity(
                date=application.date_applied,
                company=job.company,
                position=job.title,
                contact=job.apply_url,
                activity_type=ActivityType.application,
                result=ActivityResult.pending,
                job_id=job.id,
            ))
    return RedirectResponse("/", status_code=303)


def write_profile(name="", email="", phone="", location="", work_auth="",
                  salary="", remote="", notice="", skills="", links="") -> None:
    """Persist profile.yaml. Shared by the wizard and Settings so the two can't
    drift — the wizard used to write a thinner version that dropped skills."""
    existing = {}
    pf = PROFILE_DIR / "profile.yaml"
    if pf.exists():
        try:
            existing = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
        except Exception:
            existing = {}
    parsed_skills = [s.strip() for s in (skills or "").replace("\n", ",").split(",") if s.strip()]
    parsed_links = [s.strip() for s in (links or "").splitlines() if s.strip()]
    data = {
        "name": name.strip(), "email": email.strip(), "phone": phone.strip(),
        "location": location.strip(),
        # A step that doesn't ask for these must not erase them.
        "skills": parsed_skills or existing.get("skills", []),
        "links": parsed_links or existing.get("links", []),
        "screening_defaults": {
            "work_authorization": work_auth.strip(),
            "salary_expectation": salary.strip(),
            "remote_preference": remote.strip(),
            "notice_period": notice.strip(),
        },
    }
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pf.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    from ..config import load_profile

    load_profile.cache_clear()


def write_sources(searches: list[dict], greenhouse: list[str],
                  lever: list[str] | None = None) -> None:
    """Persist search targets. ``lever=None`` means leave Lever alone, which is
    what the wizard needs — it doesn't ask about Lever and must not wipe it."""
    cfg = config_current()
    cfg["searches"] = searches or [{"query": "your target role", "location": "Remote"}]
    ats = dict(cfg.get("ats") or {})
    ats["greenhouse"] = greenhouse
    if lever is not None:
        ats["lever"] = lever
    cfg["ats"] = ats
    config_save(cfg)


ROLE_SUGGESTIONS = [
    "technical writer", "content designer", "ux writer", "content strategist",
    "product manager", "program manager", "data analyst", "software engineer",
    "customer success manager", "marketing manager", "recruiter", "designer",
]

COMPANY_SUGGESTIONS = [
    ("stripe", "Stripe"), ("gitlab", "GitLab"), ("datadog", "Datadog"),
    ("figma", "Figma"), ("asana", "Asana"), ("discord", "Discord"),
    ("mongodb", "MongoDB"), ("cloudflare", "Cloudflare"), ("twilio", "Twilio"),
    ("webflow", "Webflow"), ("brex", "Brex"), ("gusto", "Gusto"),
]


@app.middleware("http")
async def _first_run_gate(request: Request, call_next):
    """Send first-time users to the wizard instead of an empty dashboard."""
    path = request.url.path
    if not path.startswith(("/setup", "/static", "/log/export")) and needs_setup():
        return RedirectResponse("/setup", status_code=303)
    return await call_next(request)


@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request, step: str = "", msg: str = "", err: str = ""):
    settings = load_settings()
    # Setup is the first run. Afterwards, changes belong in Settings.
    if not step and settings.onboarded and profile_ready():
        return RedirectResponse("/settings", status_code=303)
    # One probe per page load: when no server is listening this costs a timeout,
    # and asking twice would double the wait for the answer "nothing there".
    offered = local_models(settings.local_base_url)
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "settings": settings,
            "step": step or ("welcome" if not settings.onboarded else "done"),
            "has_key": has_api_key(),
            # Offer the two local-ish modes honestly: say up front whether the
            # thing they depend on is actually on this machine.
            "claude_found": claude_cli_path(),
            "local_base_url": settings.local_base_url or LOCAL_BASE_URL_DEFAULT,
            "local_models": offered,
            "local_found": bool(offered),
            "profile_ready": profile_ready(),
            "resume_text": (PROFILE_DIR / "resume_master.md").read_text(encoding="utf-8")
            if (PROFILE_DIR / "resume_master.md").exists() else "",
            "roles": ROLE_SUGGESTIONS,
            "companies": COMPANY_SUGGESTIONS,
            "mobile_ip": lan_ip(),
            "port": request.url.port or 8000,
            "msg": msg,
            "err": err,
        },
    )


@app.post("/setup/mode")
def setup_mode(llm_mode: str = Form("api")):
    settings = load_settings()
    settings.llm_mode = llm_mode if llm_mode in LLM_MODES else "api"
    save_settings(settings)
    # Copy-and-paste needs no configuring; the other three each have a step.
    nxt = "resume" if settings.llm_mode == "manual" else "key"
    return RedirectResponse(f"/setup?step={nxt}", status_code=303)


@app.post("/setup/local")
def setup_local(base_url: str = Form(""), local_model: str = Form("")):
    settings = load_settings()
    settings.local_base_url = base_url.strip()
    settings.local_model = local_model.strip()
    save_settings(settings)
    ok, message = check_llm()
    step = "resume" if ok else "key"
    param = "msg" if ok else "err"
    return RedirectResponse(f"/setup?step={step}&{param}={message}", status_code=303)


@app.post("/setup/key")
def setup_key(api_key: str = Form("")):
    if api_key.strip():
        set_api_key(api_key)
    ok, message = check_llm()
    if ok:
        return RedirectResponse(f"/setup?step=resume&msg={message}", status_code=303)
    return RedirectResponse(f"/setup?step=key&err={message}", status_code=303)


@app.post("/setup/resume")
async def setup_resume(
    file: UploadFile = File(None), pasted: str = Form(""), text: str = Form("")
):
    """Accept an uploaded resume, pasted text, or edits to the extracted text."""
    content = ""
    if file is not None and file.filename:
        try:
            content = resume_text(file.filename, await file.read())
        except RuntimeError as e:
            return RedirectResponse(f"/setup?step=resume&err={e}", status_code=303)
    elif pasted.strip():
        content = pasted.strip()
    elif text.strip():
        content = text.strip()

    if not content:
        return RedirectResponse(
            "/setup?step=resume&err=Upload a file or paste your resume text.",
            status_code=303,
        )
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    (PROFILE_DIR / "resume_master.md").write_text(content, encoding="utf-8")
    return RedirectResponse(
        "/setup?step=profile&msg=Resume saved. Edit it any time in Settings.",
        status_code=303,
    )


@app.post("/setup/profile")
def setup_profile(
    name: str = Form(""), email: str = Form(""), phone: str = Form(""),
    location: str = Form(""), work_auth: str = Form(""), salary: str = Form(""),
    remote: str = Form(""), notice: str = Form(""),
):
    write_profile(name=name, email=email, phone=phone, location=location,
                  work_auth=work_auth, salary=salary, remote=remote, notice=notice)
    return RedirectResponse("/setup?step=targets", status_code=303)


@app.post("/setup/targets")
def setup_targets(
    roles: list[str] = Form([]), custom_roles: str = Form(""),
    companies: list[str] = Form([]), location: str = Form("Remote"),
):
    wanted = [r.strip() for r in roles if r.strip()]
    wanted += [r.strip() for r in custom_roles.split(",") if r.strip()]
    write_sources(
        [{"query": r, "location": location or "Remote"} for r in wanted],
        [c for c in companies if c],
        lever=None,          # the wizard never asks; Settings owns Lever
    )
    settings = load_settings()
    settings.onboarded = True
    save_settings(settings)
    return RedirectResponse("/setup?step=phone", status_code=303)


@app.post("/setup/phone")
def setup_phone(mobile_access: str = Form("")):
    settings = load_settings()
    settings.mobile_access = bool(mobile_access)
    save_settings(settings)
    return RedirectResponse("/setup?step=done", status_code=303)


# ---- settings: everything that used to need a text editor -----------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str = "", err: str = ""):
    cfg = config_current()
    profile = {}
    pf = PROFILE_DIR / "profile.yaml"
    if pf.exists():
        try:
            profile = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
        except Exception:
            profile = {}
    current = load_settings()
    offered = local_models(current.local_base_url)   # one probe, see /setup
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "cfg": cfg, "settings": current, "env": env_status(),
            "claude_found": claude_cli_path(),
            "local_base_url": current.local_base_url or LOCAL_BASE_URL_DEFAULT,
            "local_models": offered, "local_found": bool(offered),
            "env_labels": ENV_KEYS, "profile": profile,
            "screening": profile.get("screening_defaults") or {},
            "mobile_ip": lan_ip(),
            "port": request.url.port or 8000,
            "data_dir": str(DATA_DIR),
            "resume_chars": len((PROFILE_DIR / "resume_master.md").read_text(encoding="utf-8"))
            if (PROFILE_DIR / "resume_master.md").exists() else 0,
            "msg": msg, "err": err,
        },
    )


@app.post("/settings/sources")
def settings_sources(
    query: list[str] = Form([]), location: list[str] = Form([]),
    greenhouse: str = Form(""), lever: str = Form(""),
):
    """Roles and where to look, each with its own location."""
    searches = []
    for i, q in enumerate(query):
        if q.strip():
            searches.append({"query": q.strip(),
                             "location": (location[i].strip() if i < len(location) else "") or "Remote"})
    write_sources(
        searches,
        [s.strip() for s in greenhouse.replace(",", "\n").splitlines() if s.strip()],
        [s.strip() for s in lever.replace(",", "\n").splitlines() if s.strip()],
    )
    return RedirectResponse("/settings?msg=Search targets saved.", status_code=303)


@app.post("/settings/behaviour")
def settings_behaviour(
    fit_threshold: str = Form("55"), results_per_search: str = Form("25"),
    max_apply_per_day: str = Form("15"), exclude_title_keywords: str = Form(""),
    exclude_company: str = Form(""),
):
    def as_int(value: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(float(value))))
        except (TypeError, ValueError):
            return default

    cfg = config_current()
    cfg["fit_threshold"] = as_int(fit_threshold, 55, 0, 100)
    cfg["results_per_search"] = as_int(results_per_search, 25, 1, 200)
    cfg["max_apply_per_day"] = as_int(max_apply_per_day, 15, 1, 100)
    cfg["exclude_title_keywords"] = [s.strip() for s in exclude_title_keywords.splitlines() if s.strip()]
    cfg["exclude_company"] = [s.strip() for s in exclude_company.splitlines() if s.strip()]
    config_save(cfg)
    return RedirectResponse("/settings?msg=Search behaviour saved.", status_code=303)


@app.post("/settings/mobile")
def settings_mobile(mobile_access: str = Form("")):
    """Serve to the local network so a phone can open the dashboard.

    The bind address is picked when the server starts, so flipping this takes
    effect on the next launch — say so instead of letting the user wonder why
    their phone still can't connect."""
    settings = load_settings()
    settings.mobile_access = bool(mobile_access)
    save_settings(settings)
    if settings.mobile_access:
        msg = "Phone access on — it starts working the next time you start disqo jobs."
    else:
        msg = "Phone access off — applies the next time you start disqo jobs."
    return RedirectResponse(f"/settings?msg={msg}", status_code=303)


@app.post("/settings/keys")
def settings_keys(
    ANTHROPIC_API_KEY: str = Form(""), ADZUNA_APP_ID: str = Form(""),
    ADZUNA_APP_KEY: str = Form(""), JOOBLE_API_KEY: str = Form(""),
    JOBPILOT_MODEL: str = Form(""), llm_mode: str = Form(""),
    local_base_url: str = Form(""), local_model: str = Form(""),
):
    """Blank means leave alone, so a page can show status without echoing secrets."""
    for key, value in [("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
                       ("ADZUNA_APP_ID", ADZUNA_APP_ID),
                       ("ADZUNA_APP_KEY", ADZUNA_APP_KEY),
                       ("JOOBLE_API_KEY", JOOBLE_API_KEY)]:
        if value.strip():
            set_env(key, value)
    set_env("JOBPILOT_MODEL", JOBPILOT_MODEL)
    current = load_settings()
    if llm_mode in LLM_MODES:
        current.llm_mode = llm_mode
    # These two are typed, not secret, so blank genuinely means "clear it".
    current.local_base_url = local_base_url.strip()
    current.local_model = local_model.strip()
    save_settings(current)
    return RedirectResponse("/settings?msg=Keys and AI settings saved.", status_code=303)


@app.post("/settings/keys/clear")
def settings_keys_clear(key: str = Form("")):
    if key in ENV_KEYS:
        set_env(key, "")
    return RedirectResponse(f"/settings?msg=Removed {key}.", status_code=303)


@app.post("/settings/profile")
def settings_profile(
    name: str = Form(""), email: str = Form(""), phone: str = Form(""),
    location: str = Form(""), work_auth: str = Form(""), salary: str = Form(""),
    remote: str = Form(""), notice: str = Form(""), skills: str = Form(""),
    links: str = Form(""),
):
    write_profile(name=name, email=email, phone=phone, location=location,
                  work_auth=work_auth, salary=salary, remote=remote,
                  notice=notice, skills=skills, links=links)
    return RedirectResponse("/settings?msg=Profile saved.", status_code=303)


# ---- search for jobs, with progress --------------------------------------

@app.get("/discover")
def discover_page():
    """Searching moved onto My jobs; keep old links and bookmarks working."""
    return RedirectResponse("/jobs", status_code=303)


@app.post("/discover")
def discover_start(score: str = Form("1"), tailor: str = Form("")):
    """Run the whole find-and-prepare pipeline in the background."""
    manual = load_settings().is_manual
    want_score = bool(score) and not manual
    want_tailor = bool(tailor) and not manual

    def job(progress):
        summary = run_discover(on_progress=lambda d, t_, label: progress(
            d, t_ + (2 if want_score else 0), f"Searching · {label}"))
        if want_score:
            progress(0, 0, "Scoring new jobs")
            summary["scored"] = score_pending(on_progress=progress)
            if want_tailor:
                progress(0, 0, "Writing applications for the best matches")
                summary["tailored"] = tailor_above_threshold(on_progress=progress)
        return summary

    tasks.start("discover", job, label="Starting")
    return RedirectResponse("/jobs", status_code=303)


@app.get("/discover/status")
def discover_status():
    return JSONResponse(tasks.status("discover") or {"state": "idle"})


# ---- interview prep, inside the app ---------------------------------------

@app.get("/prep", response_class=HTMLResponse)
def prep_index(request: Request):
    jobs = [j for j in list_jobs() if j.prep_status != PrepStatus.none]
    jobs.sort(key=lambda j: (j.prep_status != PrepStatus.started, j.company.lower()))
    return templates.TemplateResponse(
        request, "prep.html",
        {"jobs": jobs, "PrepStatus": PrepStatus, "settings": load_settings(),
         "candidates": [j for j in list_jobs(Status.applied) + list_jobs(Status.approved)
                        if j.prep_status == PrepStatus.none][:8]},
    )


@app.post("/job/{job_id}/prep/state")
async def prep_state(job_id: str, request: Request):
    """Persist prep-page notes server-side so they're in the database and the
    backups, not just one browser's local storage."""
    job = get_job(job_id)
    if not job:
        return JSONResponse({"ok": False}, status_code=404)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    path = prep_json_path(job).with_name(prep_json_path(job).stem + "-state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return JSONResponse({"ok": True})


@app.get("/job/{job_id}/prep/state")
def prep_state_get(job_id: str):
    job = get_job(job_id)
    if not job:
        return JSONResponse({})
    path = prep_json_path(job).with_name(prep_json_path(job).stem + "-state.json")
    if not path.exists():
        return JSONResponse({})
    try:
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return JSONResponse({})


# ---- your data: where it lives, backups, restore --------------------------

@app.get("/data", response_class=HTMLResponse)
def data_page(request: Request, msg: str = "", err: str = ""):
    activities = list_activities()
    dates = sorted(a.date for a in activities)
    return templates.TemplateResponse(
        request, "data.html",
        {
            "data_dir": str(DATA_DIR),
            "backup_dir": str(BACKUP_DIR),
            "db_size_mb": round(DB_PATH.stat().st_size / 1_048_576, 1) if DB_PATH.exists() else 0,
            "job_count": len(list_jobs()),
            "activity_count": len(activities),
            "first_activity": dates[0] if dates else None,
            "last_activity": dates[-1] if dates else None,
            "backups": list_backups()[:20],
            "last_backup": last_backup(),
            "days_since": days_since_backup(),
            "usage": usage_summary(),
            "settings": load_settings(),
            "msg": msg, "err": err,
        },
    )


@app.post("/data/backup")
def data_backup():
    result = run_backup()
    if result["error"]:
        return RedirectResponse(f"/data?err=Backup failed: {result['error']}", status_code=303)
    parts = ["database"] + [p.suffix.lstrip(".") for p in result["exports"]]
    return RedirectResponse(
        f"/data?msg=Backed up {', '.join(parts)}.", status_code=303
    )


@app.post("/data/rates")
def data_rates(input_rate: str = Form("0"), output_rate: str = Form("0")):
    try:
        set_rates(float(input_rate), float(output_rate))
    except ValueError:
        return RedirectResponse("/data?err=Rates must be numbers.", status_code=303)
    return RedirectResponse("/data?msg=Cost estimate updated.", status_code=303)


@app.post("/data/restore")
async def data_restore(file: UploadFile = File(None)):
    if file is None or not file.filename:
        return RedirectResponse("/data?err=Choose a backup file first.", status_code=303)
    tmp = BACKUP_DIR / f"upload-{file.filename}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(await file.read())
    try:
        restore_database(tmp)
    except RuntimeError as e:
        tmp.unlink(missing_ok=True)
        return RedirectResponse(f"/data?err={e}", status_code=303)
    finally:
        tmp.unlink(missing_ok=True)
    init_db()
    return RedirectResponse(
        "/data?msg=Restored. Your previous database was saved alongside the backups.",
        status_code=303,
    )


# ---- add a posting by URL or pasted text ----------------------------------

@app.get("/jobs/add", response_class=HTMLResponse)
def add_job_form(request: Request, err: str = "", url: str = "",
                 title: str = "", company: str = "", description: str = ""):
    return templates.TemplateResponse(
        request, "add_job.html",
        {"err": err, "url": url, "title": title, "company": company,
         "description": description, "settings": load_settings()},
    )


@app.post("/jobs/fetch")
def fetch_job(url: str = Form("")):
    """Pull a posting from a URL so the user only has to confirm it."""
    if not url.strip():
        return RedirectResponse("/jobs/add?err=Paste a link first.", status_code=303)
    try:
        found = fetch_posting(url)
    except RuntimeError as e:
        return RedirectResponse(
            f"/jobs/add?err={e}&url={url}", status_code=303
        )
    from urllib.parse import urlencode

    return RedirectResponse(
        "/jobs/add?" + urlencode({
            "url": found["url"], "title": found["title"],
            "company": found["company"], "description": found["description"],
        }),
        status_code=303,
    )


@app.post("/jobs/add")
def add_job(
    title: str = Form(""), company: str = Form(""), location: str = Form(""),
    url: str = Form(""), description: str = Form(""),
):
    if not title.strip() or not description.strip():
        return RedirectResponse(
            "/jobs/add?err=A title and the posting text are both required.",
            status_code=303,
        )
    job = Job(
        source="manual", title=title.strip(), company=company.strip() or "Unknown",
        location=location.strip(), description=description.strip(),
        apply_url=url.strip(), status=Status.discovered,
    ).ensure_id()
    upsert_job(job)
    save_job(job)

    # Score it now — an unscored job gives the user nothing to judge it by.
    if load_settings().is_manual:
        return RedirectResponse(f"/job/{job.id}/manual/fit", status_code=303)
    try:
        fit_mod.score_job(job)
        job.status = Status.scored
        save_job(job)
    except Exception as e:
        from urllib.parse import quote

        return RedirectResponse(
            f"/job/{job.id}?err={quote('Saved, but scoring failed: ' + str(e)[:120])}",
            status_code=303)
    return RedirectResponse(f"/job/{job.id}", status_code=303)


# ---- manual (no API key) LLM steps ---------------------------------------

@app.get("/job/{job_id}/manual/{kind}", response_class=HTMLResponse)
def manual_prompt(request: Request, job_id: str, kind: str, err: str = ""):
    job = get_job(job_id)
    if not job or kind not in ("fit", "tailor"):
        return RedirectResponse("/", status_code=303)
    prompt = (
        fit_mod.build_prompt(job) if kind == "fit" else tailor_mod.build_manual_prompt(job)
    )
    return templates.TemplateResponse(
        request, "manual.html",
        {"job": job, "kind": kind, "prompt": prompt, "err": err},
    )


@app.post("/job/{job_id}/manual/{kind}")
def manual_submit(job_id: str, kind: str, response: str = Form("")):
    job = get_job(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)
    if not response.strip():
        return RedirectResponse(
            f"/job/{job_id}/manual/{kind}?err=Paste the assistant's reply first.",
            status_code=303,
        )
    try:
        if kind == "fit":
            from ..llm import _extract_json

            fit_mod.apply_result(job, _extract_json(response))
            job.status = Status.scored
            save_job(job)
        else:
            application = tailor_mod.parse_manual_response(job, response)
            application.resume_pdf_path = str(
                render_pdf(application.tailored_resume_md, resume_path(job))
            )
            application.cover_pdf_path = str(
                render_pdf(application.cover_letter_md, cover_path(job))
            )
            save_application(application)
            job.status = Status.tailored
            save_job(job)
    except Exception as e:
        return RedirectResponse(
            f"/job/{job_id}/manual/{kind}?err=Couldn't read that reply: {str(e)[:140]}",
            status_code=303,
        )
    return RedirectResponse(f"/job/{job_id}", status_code=303)


def _range(date_from: str, date_to: str) -> tuple[str, str]:
    return (date_from or "").strip(), (date_to or "").strip()


@app.get("/applied", response_class=HTMLResponse)
def applied(request: Request, date_from: str = "", date_to: str = ""):
    """Work-search log: every dated action, exportable for unemployment reporting."""
    date_from, date_to = _range(date_from, date_to)
    activities = list_activities(date_from or None, date_to or None)
    jobs = {j.id: j for j in list_jobs(Status.applied)}
    return templates.TemplateResponse(
        request,
        "applied.html",
        {
            "activities": activities,
            "weekly": weekly_counts(activities),
            "jobs": jobs,
            "csv_path": str(CSV_PATH),
            "date_from": date_from,
            "date_to": date_to,
            "types": list(ActivityType),
            "results": list(ActivityResult),
            "today": date.today().isoformat(),
            "last_week": (date.today() - timedelta(days=7)).isoformat(),
        },
    )


@app.post("/log/add")
def log_add(
    date_: str = Form("", alias="date"),
    company: str = Form(""),
    position: str = Form(""),
    contact: str = Form(""),
    activity_type: str = Form(ActivityType.application.value),
    result: str = Form(ActivityResult.pending.value),
    notes: str = Form(""),
):
    """Manual entry — networking events and cold outreach have no posting behind them."""
    save_activity(WorkSearchActivity(
        date=date_ or date.today().isoformat(),
        company=company.strip(),
        position=position.strip(),
        contact=contact.strip(),
        activity_type=ActivityType(activity_type),
        result=ActivityResult(result),
        notes=notes.strip(),
    ))
    return RedirectResponse("/applied", status_code=303)


@app.post("/log/{activity_id}/update")
def log_update(
    activity_id: str,
    result: str = Form(""),
    notes: str = Form(""),
    contact: str = Form(""),
):
    activity = get_activity(activity_id)
    if activity:
        if result:
            activity.result = ActivityResult(result)
        activity.notes = notes.strip()
        activity.contact = contact.strip()
        save_activity(activity)
    return RedirectResponse("/applied", status_code=303)


@app.post("/log/{activity_id}/delete")
def log_delete(activity_id: str):
    delete_activity(activity_id)
    return RedirectResponse("/applied", status_code=303)


@app.get("/log/export.csv")
def export_csv(date_from: str = "", date_to: str = ""):
    date_from, date_to = _range(date_from, date_to)
    body = to_csv(list_activities(date_from or None, date_to or None))
    name = f"work-search-log{'-' + date_from if date_from else ''}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/log/export.xlsx")
def export_xlsx(date_from: str = "", date_to: str = ""):
    date_from, date_to = _range(date_from, date_to)
    body = to_xlsx(list_activities(date_from or None, date_to or None))
    name = f"work-search-log{'-' + date_from if date_from else ''}.xlsx"
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
