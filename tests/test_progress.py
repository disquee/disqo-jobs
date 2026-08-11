"""job_progress reads a candidacy off its work-search log entries."""

from jobpilot.models import (
    ActivityResult,
    ActivityType,
    WorkSearchActivity,
    job_progress,
)
from jobpilot.store import activities_for_job, save_activity


def _act(date, type_, result, job_id="j1"):
    return WorkSearchActivity(
        date=date, company="Acme", position="Writer",
        activity_type=type_, result=result, job_id=job_id,
    ).ensure_id()


def test_no_activities():
    assert job_progress([]) == ("Nothing logged yet", "")


def test_applied_waiting():
    acts = [_act("2026-08-01", ActivityType.application, ActivityResult.pending)]
    assert job_progress(acts) == ("Applied, waiting to hear", "accent")


def test_counts_interview_rounds():
    acts = [
        _act("2026-08-01", ActivityType.application, ActivityResult.pending),
        _act("2026-08-05", ActivityType.interview, ActivityResult.interviewing),
        _act("2026-08-09", ActivityType.interview, ActivityResult.interviewing),
    ]
    assert job_progress(acts) == ("Interviewing, round 2", "ok")


def test_latest_result_ends_the_story():
    acts = [
        _act("2026-08-01", ActivityType.application, ActivityResult.pending),
        _act("2026-08-05", ActivityType.interview, ActivityResult.rejected),
    ]
    assert job_progress(acts) == ("Declined", "")
    acts[-1].result = ActivityResult.offered
    assert job_progress(acts) == ("Offer", "ok")


def test_activities_for_job_orders_oldest_first(tmp_db):
    save_activity(_act("2026-08-09", ActivityType.interview, ActivityResult.interviewing))
    save_activity(_act("2026-08-01", ActivityType.application, ActivityResult.pending))
    save_activity(_act("2026-08-05", ActivityType.interview, ActivityResult.interviewing, job_id="other"))
    acts = activities_for_job("j1")
    assert [a.date for a in acts] == ["2026-08-01", "2026-08-09"]
