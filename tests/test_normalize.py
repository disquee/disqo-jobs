import jobpilot.pipeline.normalize as normalize
from jobpilot.models import Job


def _cfg():
    return {
        "exclude_title_keywords": ["intern", "clearance"],
        "exclude_company": ["Bad Agency"],
    }


def _job(**kw) -> Job:
    base = dict(source="adzuna", title="Engineer", company="Acme",
                apply_url="https://x/1", description="d")
    base.update(kw)
    return Job(**base).ensure_id()


def test_excludes_title_keyword(monkeypatch):
    monkeypatch.setattr(normalize, "load_config", _cfg)
    jobs = [_job(title="Software Engineering Intern", apply_url="https://x/i"),
            _job(title="Senior Engineer", apply_url="https://x/s")]
    kept = normalize.apply_filters(jobs)
    assert [j.title for j in kept] == ["Senior Engineer"]


def test_excludes_company(monkeypatch):
    monkeypatch.setattr(normalize, "load_config", _cfg)
    jobs = [_job(company="Bad Agency", apply_url="https://x/b"),
            _job(company="Acme", apply_url="https://x/a")]
    kept = normalize.apply_filters(jobs)
    assert [j.company for j in kept] == ["Acme"]


def test_dedupes_by_id(monkeypatch):
    monkeypatch.setattr(normalize, "load_config", _cfg)
    dup_a = _job(apply_url="https://x/same")
    dup_b = _job(apply_url="https://x/same")
    assert dup_a.id == dup_b.id
    kept = normalize.apply_filters([dup_a, dup_b])
    assert len(kept) == 1


def test_drops_missing_url_or_title(monkeypatch):
    monkeypatch.setattr(normalize, "load_config", _cfg)
    jobs = [_job(title="", apply_url="https://x/notitle"),
            _job(title="Engineer", apply_url="")]
    assert normalize.apply_filters(jobs) == []
