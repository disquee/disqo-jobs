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


# ---- sponsorship & clearance: detection ------------------------------------

from jobpilot.pipeline.normalize import denies_sponsorship, required_clearance


def test_denies_sponsorship_detection():
    says_no = [
        "We are unable to sponsor visas at this time.",
        "This role cannot currently offer visa sponsorship.",
        "No visa sponsorship available for this position.",
        "Applicants must be authorized to work without sponsorship.",
        "Sponsorship is not available.",
        "We do not sponsor employment visas.",
    ]
    for text in says_no:
        assert denies_sponsorship(text), text
    says_nothing_bad = [
        "H-1B sponsorship available for qualified candidates.",
        "We sponsor visas and support relocation.",
        "Proud corporate sponsor of the annual charity run.",
        "",
    ]
    for text in says_nothing_bad:
        assert not denies_sponsorship(text), text


def test_required_clearance_levels():
    cases = [
        ("Active TS/SCI with CI polygraph required", "ts/sci"),
        ("Top Secret clearance required", "top secret"),
        ("Must hold an active Secret clearance", "secret"),
        ("Requires a Public Trust background investigation", "public trust"),
        ("An active security clearance is required", "any"),
        # Logistics, not national security.
        ("Handles customs clearance required for imported goods", None),
        ("Great team, great culture", None),
    ]
    for text, expected in cases:
        assert required_clearance(text) == expected, text


# ---- sponsorship & clearance: filtering ------------------------------------

def _filter_cfg(**kw):
    base = {"exclude_title_keywords": [], "exclude_company": []}
    base.update(kw)
    return lambda: base


def test_sponsorship_filter_is_opt_in(monkeypatch):
    no_sponsor = _job(description="We are unable to sponsor visas.",
                      apply_url="https://x/ns")
    silent = _job(description="A great role.", apply_url="https://x/ok")

    monkeypatch.setattr(normalize, "load_config", _filter_cfg())
    assert len(normalize.apply_filters([no_sponsor, silent])) == 2

    monkeypatch.setattr(normalize, "load_config",
                        _filter_cfg(needs_sponsorship=True))
    kept = normalize.apply_filters([no_sponsor, silent])
    assert [j.apply_url for j in kept] == ["https://x/ok"]


def test_clearance_filter_respects_level_held(monkeypatch):
    ts = _job(description="Requires an active TS/SCI clearance.",
              apply_url="https://x/ts")
    secret = _job(description="Secret clearance required.", apply_url="https://x/s")
    plain = _job(description="No special requirements.", apply_url="https://x/p")
    jobs = [ts, secret, plain]

    # Blank (the default) filters nothing — the user never said.
    monkeypatch.setattr(normalize, "load_config", _filter_cfg())
    assert len(normalize.apply_filters(jobs)) == 3

    monkeypatch.setattr(normalize, "load_config",
                        _filter_cfg(clearance_held="none"))
    assert [j.apply_url for j in normalize.apply_filters(jobs)] == ["https://x/p"]

    monkeypatch.setattr(normalize, "load_config",
                        _filter_cfg(clearance_held="secret"))
    assert [j.apply_url for j in normalize.apply_filters(jobs)] == [
        "https://x/s", "https://x/p"]

    monkeypatch.setattr(normalize, "load_config",
                        _filter_cfg(clearance_held="ts/sci"))
    assert len(normalize.apply_filters(jobs)) == 3
