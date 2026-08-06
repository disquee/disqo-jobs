from jobpilot import store
from jobpilot.models import Status


def test_upsert_is_idempotent(tmp_db, sample_job):
    assert store.upsert_job(sample_job) is True   # newly inserted
    assert store.upsert_job(sample_job) is False  # already present
    assert len(store.list_jobs()) == 1


def test_save_job_updates_status_and_score(tmp_db, sample_job):
    store.upsert_job(sample_job)
    sample_job.fit_score = 91
    sample_job.status = Status.scored
    store.save_job(sample_job)

    reloaded = store.get_job(sample_job.id)
    assert reloaded.fit_score == 91
    assert reloaded.status is Status.scored


def test_list_jobs_filters_by_status(tmp_db, sample_job):
    store.upsert_job(sample_job)
    assert len(store.list_jobs(Status.discovered)) == 1
    assert len(store.list_jobs(Status.applied)) == 0


def test_application_roundtrip(tmp_db, sample_job, sample_application):
    store.upsert_job(sample_job)
    store.save_application(sample_application)
    got = store.get_application(sample_job.id)
    assert got is not None
    assert got.screening[0].question == "Work auth?"
    assert got.resume_pdf_path == "/tmp/r.pdf"


def test_save_application_upserts(tmp_db, sample_job, sample_application):
    store.upsert_job(sample_job)
    store.save_application(sample_application)
    sample_application.cover_letter_md = "Updated"
    store.save_application(sample_application)  # must not raise / duplicate
    assert store.get_application(sample_job.id).cover_letter_md == "Updated"


def test_counts_by_status(tmp_db, sample_job):
    store.upsert_job(sample_job)
    assert store.counts_by_status().get("discovered") == 1
