"""User-facing settings written by the setup wizard.

Kept separate from config.yaml because that file is hand-editable and full of
comments — round-tripping YAML would strip them. This is machine-owned state.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .config import ROOT, secret

SETTINGS_PATH = ROOT / "settings.json"
ENV_PATH = ROOT / ".env"


class Settings(BaseModel):
    # "api"    -- call the provider directly with a key
    # "manual" -- render prompts for the user to paste into a chat UI and paste back
    llm_mode: str = "api"
    model: Optional[str] = None
    onboarded: bool = False

    @property
    def is_manual(self) -> bool:
        return self.llm_mode == "manual"


def load_settings() -> Settings:
    if SETTINGS_PATH.exists():
        try:
            return Settings.model_validate_json(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return Settings()


def save_settings(settings: Settings) -> Settings:
    SETTINGS_PATH.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    return settings


def has_api_key() -> bool:
    return bool(secret("ANTHROPIC_API_KEY"))


def set_api_key(key: str) -> None:
    """Persist a key to .env and make it live for this process.

    .env rather than the macOS Keychain so the same path works on every OS; the
    Keychain remains supported for anyone who prefers it (see config.secret).
    """
    key = key.strip()
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = [
            ln for ln in ENV_PATH.read_text(encoding="utf-8").splitlines()
            if not ln.startswith("ANTHROPIC_API_KEY=")
        ]
    lines.append(f"ANTHROPIC_API_KEY={key}")
    ENV_PATH.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    ENV_PATH.chmod(0o600)

    os.environ["ANTHROPIC_API_KEY"] = key
    # The SDK client is cached with the old key baked in.
    try:
        from .llm import _client

        _client.cache_clear()
    except Exception:
        pass


def check_api_key() -> tuple[bool, str]:
    """Round-trip a tiny completion. Returns (ok, message) for the UI."""
    if not has_api_key():
        return False, "No API key set."
    try:
        from .llm import complete

        reply = complete("Reply with the single word: ready", max_tokens=16)
        if "ready" in reply.lower():
            return True, "Key works — a test request came back."
        return True, f"Key works. Model replied: {reply[:60]}"
    except Exception as e:  # surface the provider's own wording, trimmed
        msg = str(e)
        if "authentication" in msg.lower() or "401" in msg:
            return False, "That key was rejected. Check for a typo or a revoked key."
        if "credit" in msg.lower() or "billing" in msg.lower() or "quota" in msg.lower():
            return False, "The key is valid but the account has no credit available."
        return False, f"Couldn't reach the provider: {msg[:160]}"


def profile_ready() -> bool:
    from .config import PROFILE_DIR

    return (PROFILE_DIR / "profile.yaml").exists() and (
        PROFILE_DIR / "resume_master.md"
    ).exists()


def needs_setup() -> bool:
    """True only when there's genuinely nothing to work with.

    Keyed on the profile rather than the onboarded flag: installs that predate
    the wizard have a resume and profile but no settings.json, and pushing those
    users through setup would be wrong.
    """
    return not profile_ready()
