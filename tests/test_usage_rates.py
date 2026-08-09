"""Cost-estimate rates follow the model in use, unless the user set their own.

The tracker once hard-coded $3/$15 per million tokens while the default model
was Opus-class ($5/$25) — a fresh install's estimate understated real spend by
nearly half, which misleads badly on a small starter credit.
"""

from __future__ import annotations

import jobpilot.settings as settings_mod
import jobpilot.usage as usage
from jobpilot.settings import Settings


def _api_mode(monkeypatch):
    monkeypatch.setattr(settings_mod, "load_settings", lambda: Settings(llm_mode="api"))


def test_default_rates_follow_the_model(monkeypatch):
    _api_mode(monkeypatch)
    for model, expected in [
        ("claude-opus-4-8", (5.0, 25.0)),
        ("claude-sonnet-4-6", (3.0, 15.0)),
        ("claude-haiku-4-5-20251001", (1.0, 5.0)),
    ]:
        monkeypatch.setenv("JOBPILOT_MODEL", model)
        assert usage.default_rates() == expected


def test_unknown_model_assumes_default_tier_pricing(monkeypatch):
    _api_mode(monkeypatch)
    monkeypatch.setenv("JOBPILOT_MODEL", "some-future-model")
    assert usage.default_rates() == (5.0, 25.0)


def test_subscription_and_local_modes_cost_nothing_per_token(monkeypatch):
    for mode in ("cli", "local", "manual"):
        monkeypatch.setattr(
            settings_mod, "load_settings", lambda m=mode: Settings(llm_mode=m)
        )
        assert usage.default_rates() == (0.0, 0.0)


def test_user_set_rates_always_win(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "USAGE_PATH", tmp_path / "usage.json")
    _api_mode(monkeypatch)
    monkeypatch.setenv("JOBPILOT_MODEL", "claude-opus-4-8")
    usage.record(1_000_000, 0)
    usage.set_rates(7.0, 30.0)
    got = usage.summary()
    assert got["rates_are_custom"] is True
    assert (got["input_rate"], got["output_rate"]) == (7.0, 30.0)
    assert got["total_cost"] == 7.0


def test_legacy_auto_written_rates_are_superseded(monkeypatch, tmp_path):
    """Old usage.json files all carry 3.0/15.0 written by the code, not the
    user. Those defer to model pricing; a differing stored pair was typed
    into the rates form and still wins."""
    import json

    path = tmp_path / "usage.json"
    monkeypatch.setattr(usage, "USAGE_PATH", path)
    _api_mode(monkeypatch)
    monkeypatch.setenv("JOBPILOT_MODEL", "claude-opus-4-8")

    path.write_text(json.dumps({
        "input_tokens": 1_000_000, "output_tokens": 0, "calls": 3,
        "by_day": {}, "input_rate": 3.0, "output_rate": 15.0,
    }))
    assert usage.summary()["total_cost"] == 5.0  # model rate, not the stale 3.0

    path.write_text(json.dumps({
        "input_tokens": 1_000_000, "output_tokens": 0, "calls": 3,
        "by_day": {}, "input_rate": 2.5, "output_rate": 10.0,
    }))
    assert usage.summary()["total_cost"] == 2.5  # deliberately typed → kept
