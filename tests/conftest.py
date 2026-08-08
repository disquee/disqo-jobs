"""Shared fixtures: isolate the SQLite store, CSV log, and profile to temp paths."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import jobpilot.config as config
import jobpilot.store as store
import jobpilot.log_csv as log_csv
from jobpilot.models import Application, Job, ScreeningQA, Status


@pytest.fixture(autouse=True)
def example_profile(tmp_path):
    """Point profile loading at the committed *.example.* templates.

    Two reasons this is autouse. It keeps the suite hermetic — otherwise tests
    read whatever real resume the developer happens to have, so results differ
    per machine. And it makes `pytest` pass on a fresh install, where
    profile/profile.yaml and profile/resume_master.md don't exist yet.

    Restores PROFILE_DIR by hand rather than via monkeypatch: requesting the
    monkeypatch fixture here would make it set up first and therefore tear down
    last, which breaks teardown ordering for tests that patch cached functions.
    """
    src = Path(config.__file__).resolve().parent.parent / "profile"
    dest = tmp_path / "profile"
    dest.mkdir()
    shutil.copy(src / "profile.example.yaml", dest / "profile.yaml")
    shutil.copy(src / "resume_master.example.md", dest / "resume_master.md")

    original = config.PROFILE_DIR
    config.PROFILE_DIR = dest
    config.load_profile.cache_clear()
    config.load_master_resume.cache_clear()
    try:
        yield
    finally:
        config.PROFILE_DIR = original
        config.load_profile.cache_clear()
        config.load_master_resume.cache_clear()


@pytest.fixture(autouse=True)
def api_mode():
    """Pin the LLM backend to the API path for the whole suite.

    complete() dispatches on the developer's own settings.json, so without this
    a machine set to a local model or Claude Code would run these tests against
    a real backend — or fail, since the fakes stub the Anthropic client only.
    A test that wants another backend patches _mode_and_settings itself.

    Restored by hand rather than via monkeypatch, for the same teardown-ordering
    reason spelled out in example_profile above.
    """
    import jobpilot.llm as llm

    original = llm._mode_and_settings
    llm._mode_and_settings = lambda: ("api", None)
    try:
        yield
    finally:
        llm._mode_and_settings = original


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point the store at a throwaway DB and initialize the schema."""
    db = tmp_path / "test.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    store.init_db()
    return db


@pytest.fixture
def tmp_csv(tmp_path, monkeypatch):
    csv_path = tmp_path / "applications.csv"
    monkeypatch.setattr(log_csv, "CSV_PATH", csv_path)
    return csv_path


@pytest.fixture
def sample_job() -> Job:
    return Job(
        source="greenhouse",
        title="Senior Backend Engineer",
        company="Acme",
        location="Remote",
        description="Python, FastAPI, Postgres. Build APIs at scale.",
        apply_url="https://example.com/apply",
    ).ensure_id()


@pytest.fixture
def sample_application(sample_job) -> Application:
    return Application(
        job_id=sample_job.id,
        tailored_resume_md="# Jane\n\n## Skills\n- Python",
        cover_letter_md="Dear Acme,",
        screening=[ScreeningQA(question="Work auth?", answer="Yes")],
        resume_pdf_path="/tmp/r.pdf",
        cover_pdf_path="/tmp/c.pdf",
    )
