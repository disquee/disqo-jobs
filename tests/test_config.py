"""Secret resolution: environment first, then macOS Keychain fallback."""

import pytest

import jobpilot.config as config


@pytest.fixture(autouse=True)
def _clear_keychain_cache():
    # _keychain_get is lru_cached; reset between tests so monkeypatch takes effect.
    config._keychain_get.cache_clear()
    yield
    config._keychain_get.cache_clear()


def test_secret_prefers_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    monkeypatch.setattr(config, "_keychain_get", lambda s: "from-keychain")
    assert config.secret("ANTHROPIC_API_KEY") == "from-env"


def test_secret_falls_back_to_keychain(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "_keychain_get", lambda s: "from-keychain")
    assert config.secret("ANTHROPIC_API_KEY") == "from-keychain"


def test_require_anthropic_key_raises_when_absent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "_keychain_get", lambda s: None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        config.require_anthropic_key()


def test_keychain_get_returns_none_on_failure(monkeypatch):
    class _Result:
        returncode = 44
        stdout = ""

    monkeypatch.setattr(config.subprocess, "run", lambda *a, **k: _Result())
    config._keychain_get.cache_clear()
    assert config._keychain_get("NOPE") is None
