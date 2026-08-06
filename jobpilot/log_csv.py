"""Append a row to output/applications.csv when a job is applied to."""

from __future__ import annotations

import csv
from datetime import date

from .config import CSV_PATH
from .models import Application, Job

FIELDS = [
    "date_applied",
    "job_title",
    "company",
    "location",
    "posting_text",
    "apply_url",
    "fit_score",
    "resume_path",
    "cover_letter_path",
    "status",
]


def append_application(job: Job, app: Application) -> str:
    """Write one row; create the file with a header if needed. Returns the date."""
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not CSV_PATH.exists()
    today = app.date_applied or date.today().isoformat()

    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(
            {
                "date_applied": today,
                "job_title": job.title,
                "company": job.company,
                "location": job.location,
                "posting_text": job.description,
                "apply_url": job.apply_url,
                "fit_score": job.fit_score if job.fit_score is not None else "",
                "resume_path": app.resume_pdf_path or "",
                "cover_letter_path": app.cover_pdf_path or "",
                "status": "applied",
            }
        )
    return today
