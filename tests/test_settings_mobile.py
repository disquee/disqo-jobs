"""Phone access: off unless chosen, and the bind address follows the setting."""

from __future__ import annotations

import jobpilot.settings as settings_mod
from jobpilot.settings import LOCAL_HOST, MOBILE_HOST, Settings, serve_host


def test_mobile_access_defaults_off():
    """Binding beyond localhost must be a choice, never the starting state."""
    assert Settings().mobile_access is False


def test_serve_host_follows_setting(monkeypatch):
    monkeypatch.setattr(
        settings_mod, "load_settings", lambda: Settings(mobile_access=False)
    )
    assert serve_host() == LOCAL_HOST
    monkeypatch.setattr(
        settings_mod, "load_settings", lambda: Settings(mobile_access=True)
    )
    assert serve_host() == MOBILE_HOST


def test_mobile_access_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    settings_mod.save_settings(Settings(mobile_access=True))
    assert settings_mod.load_settings().mobile_access is True


def test_settings_files_from_before_the_field_still_load(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text('{"llm_mode": "api", "onboarded": true}', encoding="utf-8")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", path)
    loaded = settings_mod.load_settings()
    assert loaded.onboarded is True
    assert loaded.mobile_access is False
