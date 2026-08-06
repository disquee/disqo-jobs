"""Assisted apply: open the posting in a real browser and pre-fill known fields.

SAFETY: this NEVER clicks the final submit button. It fills what it can, then
hands control to the human in a visible (headed) browser window. The human
reviews, fixes anything (CAPTCHA, unusual fields), and submits themselves.
"""

from __future__ import annotations

from ..config import load_profile
from ..models import Application, Job

# Best-effort field matching. ATS forms vary, so we match on common label /
# name / placeholder substrings and fill text inputs accordingly.
FIELD_HINTS: dict[str, list[str]] = {
    "name": ["full name", "name"],
    "email": ["email"],
    "phone": ["phone", "mobile"],
    "linkedin": ["linkedin"],
    "website": ["website", "portfolio"],
}


def assisted_apply(job: Job, app: Application) -> None:
    """Open job.apply_url, autofill, and block until the user closes the page."""
    from playwright.sync_api import sync_playwright

    profile = load_profile()
    values = {
        "name": profile.get("name", ""),
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "linkedin": profile.get("linkedin", ""),
        "website": profile.get("website", ""),
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(job.apply_url, wait_until="domcontentloaded", timeout=60000)

        _fill_basic_fields(page, values)
        _maybe_upload_resume(page, app)

        print("\n" + "=" * 64)
        print(f"Assisted apply: {job.title} @ {job.company}")
        print("Basic fields pre-filled where detected. Review the form,")
        print("answer remaining questions, and SUBMIT manually in the browser.")
        if app.screening:
            print("\nDrafted screening answers (copy as needed):")
            for qa in app.screening:
                print(f"  Q: {qa.question}\n  A: {qa.answer}\n")
        print("Close the browser window when you're done to continue.")
        print("=" * 64 + "\n")

        # Block until the user closes the page/window.
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _fill_basic_fields(page, values: dict[str, str]) -> None:
    for key, hints in FIELD_HINTS.items():
        val = values.get(key)
        if not val:
            continue
        for hint in hints:
            selector = (
                f"input[name*='{hint}' i], input[placeholder*='{hint}' i], "
                f"input[aria-label*='{hint}' i]"
            )
            try:
                loc = page.locator(selector).first
                if loc.count() > 0:
                    loc.fill(val, timeout=2000)
                    break
            except Exception:
                continue


def _maybe_upload_resume(page, app: Application) -> None:
    if not app.resume_pdf_path:
        return
    try:
        file_input = page.locator("input[type='file']").first
        if file_input.count() > 0:
            file_input.set_input_files(app.resume_pdf_path, timeout=3000)
    except Exception:
        pass
