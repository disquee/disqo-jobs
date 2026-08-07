"""Local FastAPI review dashboard.

Workflow: review tailored jobs, edit the resume/cover/answers, then either
launch assisted-apply (a headed browser opens on your machine) or, after you
submit in that browser, click "Mark applied" to log the row to CSV.
"""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path

import yaml
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
)
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
from ..settings import (
    check_api_key,
    has_api_key,
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
    ensure_job_prep, load_plan, prep_html_path, prep_json_path, save_plan,
)
from ..pipeline.process import tailor_job_full
from ..pipeline.render import cover_path, render_pdf, resume_path
from ..store import (
    activity_for_job,
    upsert_job,
    delete_activity,
    get_activity,
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


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # A job search runs for months; back up on every launch so a lost or
    # corrupted database costs at most a day.
    if not backed_up_today():
        threading.Thread(target=run_backup, daemon=True).start()


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


@app.get("/jobs", response_class=HTMLResponse)
def index(request: Request):
    threshold = int(load_config().get("fit_threshold", 70))
    # Applied jobs stay listed so the ✓ is visible at a glance, but sort under
    # everything still needing action.
    active = list_jobs(Status.tailored) + list_jobs(Status.scored) + list_jobs(Status.approved)
    done = list_jobs(Status.applied)
    jobs = sorted(active, key=lambda j: j.fit_score or 0, reverse=True) + sorted(
        done, key=lambda j: j.fit_score or 0, reverse=True
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {"jobs": jobs, "threshold": threshold, "PrepStatus": PrepStatus,
         "settings": load_settings(), "display_status": display_status,
         "interviewing": _interviewing_ids()},
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
    if not path.startswith(("/setup", "/log/export")) and needs_setup():
        return RedirectResponse("/setup", status_code=303)
    return await call_next(request)


@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request, step: str = "", msg: str = "", err: str = ""):
    settings = load_settings()
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "settings": settings,
            "step": step or ("welcome" if not settings.onboarded else "done"),
            "has_key": has_api_key(),
            "profile_ready": profile_ready(),
            "resume_text": (PROFILE_DIR / "resume_master.md").read_text(encoding="utf-8")
            if (PROFILE_DIR / "resume_master.md").exists() else "",
            "roles": ROLE_SUGGESTIONS,
            "companies": COMPANY_SUGGESTIONS,
            "msg": msg,
            "err": err,
        },
    )


@app.post("/setup/mode")
def setup_mode(llm_mode: str = Form("api")):
    settings = load_settings()
    settings.llm_mode = "manual" if llm_mode == "manual" else "api"
    save_settings(settings)
    nxt = "key" if settings.llm_mode == "api" else "resume"
    return RedirectResponse(f"/setup?step={nxt}", status_code=303)


@app.post("/setup/key")
def setup_key(api_key: str = Form("")):
    if api_key.strip():
        set_api_key(api_key)
    ok, message = check_api_key()
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
    data = {
        "name": name.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
        "location": location.strip(),
        "screening_defaults": {
            "work_authorization": work_auth.strip(),
            "salary_expectation": salary.strip(),
            "remote_preference": remote.strip(),
            "notice_period": notice.strip(),
        },
    }
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    (PROFILE_DIR / "profile.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return RedirectResponse("/setup?step=targets", status_code=303)


@app.post("/setup/targets")
def setup_targets(
    roles: list[str] = Form([]), custom_roles: str = Form(""),
    companies: list[str] = Form([]), location: str = Form("Remote"),
):
    wanted = [r.strip() for r in roles if r.strip()]
    wanted += [r.strip() for r in custom_roles.split(",") if r.strip()]
    cfg = load_config().copy()
    cfg["searches"] = [{"query": r, "location": location or "Remote"} for r in wanted] or [
        {"query": "your target role", "location": "Remote"}
    ]
    cfg["ats"] = {"greenhouse": [c for c in companies if c], "lever": []}
    (ROOT / "config.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    load_config.cache_clear()
    settings = load_settings()
    settings.onboarded = True
    save_settings(settings)
    return RedirectResponse("/setup?step=done", status_code=303)


# ---- search for jobs, with progress --------------------------------------

@app.get("/discover", response_class=HTMLResponse)
def discover_page(request: Request):
    cfg = load_config()
    return templates.TemplateResponse(
        request, "discover.html",
        {
            "task": tasks.status("discover"),
            "searches": cfg.get("searches", []),
            "companies": (cfg.get("ats", {}) or {}).get("greenhouse", []),
            "settings": load_settings(),
            "per_job": estimate_per_job(),
        },
    )


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
            summary["scored"] = score_pending()
            if want_tailor:
                progress(0, 0, "Writing applications for the best matches")
                summary["tailored"] = tailor_above_threshold()
        return summary

    tasks.start("discover", job, label="Starting")
    return RedirectResponse("/discover", status_code=303)


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
