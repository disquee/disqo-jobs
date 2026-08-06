"""Source response parsing with HTTP mocked."""

import jobpilot.sources.adzuna as adzuna_mod
import jobpilot.sources.greenhouse as greenhouse_mod
from jobpilot.sources.base import html_to_text


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_html_to_text_strips_tags():
    out = html_to_text("<p>Hello<br/>world</p><ul><li>a</li></ul>")
    assert "<" not in out
    assert "Hello" in out and "world" in out and "a" in out


def test_adzuna_parses_results(monkeypatch):
    payload = {"results": [{
        "title": "Senior Engineer",
        "company": {"display_name": "Acme"},
        "location": {"display_name": "Remote"},
        "description": "<p>Python</p>",
        "redirect_url": "https://adzuna/job/1",
        "salary_min": 150000, "salary_max": 180000,
        "created": "2026-06-01",
    }]}
    monkeypatch.setattr(adzuna_mod.httpx, "get", lambda *a, **k: _FakeResp(payload))

    src = adzuna_mod.AdzunaSource()
    src.app_id, src.app_key = "id", "key"   # mark available
    jobs = src.search("engineer", "Remote", 10)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Senior Engineer"
    assert job.company == "Acme"
    assert job.salary == "$150,000 - $180,000"
    assert job.id  # ensure_id populated


def test_adzuna_remote_folds_into_keyword(monkeypatch):
    captured = {}

    def fake_get(*a, **k):
        captured.update(k.get("params", {}))
        return _FakeResp({"results": []})

    monkeypatch.setattr(adzuna_mod.httpx, "get", fake_get)
    src = adzuna_mod.AdzunaSource()
    src.app_id, src.app_key = "id", "key"
    src.search("technical writer", "Remote", 10)

    # "Remote" must not be sent as `where` (Adzuna returns nothing for it);
    # it becomes part of the keyword and `where` is cleared.
    assert captured["where"] == ""
    assert captured["what"] == "technical writer remote"


def test_adzuna_keeps_geographic_location(monkeypatch):
    captured = {}
    monkeypatch.setattr(adzuna_mod.httpx, "get",
                        lambda *a, **k: captured.update(k.get("params", {})) or _FakeResp({"results": []}))
    src = adzuna_mod.AdzunaSource()
    src.app_id, src.app_key = "id", "key"
    src.search("technical writer", "Chicago", 10)
    assert captured["where"] == "Chicago"
    assert captured["what"] == "technical writer"


def test_adzuna_unavailable_without_keys(monkeypatch):
    src = adzuna_mod.AdzunaSource()
    src.app_id, src.app_key = "", ""
    assert src.available() is False
    assert src.search("x", "y", 5) == []


def test_greenhouse_filters_by_query(monkeypatch):
    payload = {"jobs": [
        {"title": "Backend Engineer", "location": {"name": "Remote"},
         "content": "desc", "absolute_url": "https://gh/1", "updated_at": "2026"},
        {"title": "Designer", "location": {"name": "NYC"},
         "content": "desc", "absolute_url": "https://gh/2", "updated_at": "2026"},
    ]}
    monkeypatch.setattr(greenhouse_mod.httpx, "get", lambda *a, **k: _FakeResp(payload))

    jobs = greenhouse_mod.GreenhouseSource("acme").search("engineer", "", 10)
    assert [j.title for j in jobs] == ["Backend Engineer"]
    assert jobs[0].company == "acme"
