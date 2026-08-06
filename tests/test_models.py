from jobpilot.models import Job, Status


def _job(**kw) -> Job:
    base = dict(source="adzuna", title="Engineer", company="Acme",
                apply_url="https://x/1")
    base.update(kw)
    return Job(**base).ensure_id()


def test_ensure_id_is_deterministic():
    a = _job()
    b = _job()
    assert a.id == b.id
    assert len(a.id) == 16


def test_ensure_id_varies_by_url():
    assert _job(apply_url="https://x/1").id != _job(apply_url="https://x/2").id


def test_ensure_id_varies_by_source():
    assert _job(source="adzuna").id != _job(source="lever").id


def test_ensure_id_idempotent():
    job = _job()
    first = job.id
    job.ensure_id()  # calling again must not change it
    assert job.id == first


def test_default_status_is_discovered():
    assert _job().status is Status.discovered
