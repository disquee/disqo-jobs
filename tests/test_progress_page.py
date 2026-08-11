"""The Progress card and its two routes, through the real app."""

from fastapi.testclient import TestClient

from jobpilot.dashboard.server import app
from jobpilot.models import ActivityResult, ActivityType, Status
from jobpilot.store import activities_for_job, save_job, upsert_job


def _client():
    return TestClient(app)


def _applied_job(sample_job, client):
    upsert_job(sample_job)
    sample_job.status = Status.applied
    save_job(sample_job)
    return sample_job


def test_interview_rounds_accumulate(tmp_db, sample_job):
    client = _client()
    job = _applied_job(sample_job, client)

    for label in ("Recruiter screen", "Hiring manager", "Panel"):
        r = client.post(f"/job/{job.id}/interview",
                        data={"date": "2026-08-10", "label": label},
                        follow_redirects=False)
        assert r.status_code == 303

    acts = activities_for_job(job.id)
    assert len(acts) == 3
    assert all(a.activity_type == ActivityType.interview for a in acts)
    assert acts[0].notes == "Recruiter screen"

    page = client.get(f"/job/{job.id}").text
    assert "Progress" in page
    assert "Interviewing, round 3" in page
    assert "Add an interview" in page


def test_outcome_marks_latest_activity(tmp_db, sample_job):
    client = _client()
    job = _applied_job(sample_job, client)
    client.post(f"/job/{job.id}/interview", data={"label": "Onsite"},
                follow_redirects=False)

    client.post(f"/job/{job.id}/outcome", data={"outcome": "declined"},
                follow_redirects=False)
    assert activities_for_job(job.id)[-1].result == ActivityResult.rejected
    assert "Declined" in client.get(f"/job/{job.id}").text

    # Declined isn't final if the company comes back around.
    client.post(f"/job/{job.id}/outcome", data={"outcome": "progress"},
                follow_redirects=False)
    assert activities_for_job(job.id)[-1].result == ActivityResult.interviewing
