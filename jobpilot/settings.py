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


#: Every way the app can reach a model. Order is the order they're offered.
LLM_MODES = ("api", "cli", "local", "manual")


class Settings(BaseModel):
    # "api"    -- call the provider directly with a key
    # "cli"    -- shell out to the Claude Code binary (a subscription, no key)
    # "local"  -- an OpenAI-compatible server on this machine (Ollama, LM Studio…)
    # "manual" -- render prompts for the user to paste into a chat UI and paste back
    llm_mode: str = "api"
    model: Optional[str] = None
    local_base_url: str = ""
    local_model: str = ""
    # Also write a full-length CV (complete history, not the one-page resume)
    # for each tailored job. Jobs can override this individually (Job.cv_enabled).
    generate_cv: bool = False
    onboarded: bool = False

    @property
    def is_manual(self) -> bool:
        return self.llm_mode == "manual"

    @property
    def is_automatic(self) -> bool:
        """Anything that can answer without the user carrying text around."""
        return self.llm_mode in ("api", "cli", "local")


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


def cv_enabled_for(job) -> bool:
    """Whether tailoring should write a CV for this job: the job's own toggle
    when one was set, otherwise the generate_cv setting."""
    if getattr(job, "cv_enabled", None) is not None:
        return bool(job.cv_enabled)
    return load_settings().generate_cv


def has_api_key() -> bool:
    return bool(secret("ANTHROPIC_API_KEY"))


ENV_KEYS = {
    "ANTHROPIC_API_KEY": "AI provider key",
    "ADZUNA_APP_ID": "Adzuna app ID",
    "ADZUNA_APP_KEY": "Adzuna app key",
    "JOOBLE_API_KEY": "Jooble API key",
    "JOBPILOT_MODEL": "Model override",
    "JOBPILOT_DATA_DIR": "Where your data is stored",
}


def set_env(key: str, value: str) -> None:
    """Write one variable to .env and make it live for this process."""
    value = (value or "").strip()
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = [ln for ln in ENV_PATH.read_text(encoding="utf-8").splitlines()
                 if not ln.startswith(f"{key}=")]
    if value:
        lines.append(f"{key}={value}")
        os.environ[key] = value
    else:
        os.environ.pop(key, None)
    ENV_PATH.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    ENV_PATH.chmod(0o600)


def env_status() -> dict[str, bool]:
    """Which keys are set, without ever handing the values back to a page."""
    return {k: bool(secret(k)) for k in ENV_KEYS}


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


def claude_cli_path() -> Optional[str]:
    """Where the Claude Code binary is, or None. Used to offer the mode at all."""
    import shutil

    from .llm import CLAUDE_BIN

    return shutil.which(CLAUDE_BIN) or (CLAUDE_BIN if os.path.exists(CLAUDE_BIN) else None)


def local_models(base_url: str = "") -> list[str]:
    """Model names a local server is offering, or [] if it isn't reachable.

    Saves the user typing a name they have to get exactly right.
    """
    import httpx

    from .llm import LOCAL_BASE_URL_DEFAULT

    base = (base_url or LOCAL_BASE_URL_DEFAULT).rstrip("/")
    try:
        resp = httpx.get(f"{base}/models", timeout=4)
        resp.raise_for_status()
        return sorted(m.get("id", "") for m in resp.json().get("data", []) if m.get("id"))
    except Exception:
        return []


def check_llm() -> tuple[bool, str]:
    """Round-trip a tiny completion in whatever mode is set. (ok, message)."""
    settings = load_settings()
    mode = settings.llm_mode

    if mode == "manual":
        return True, "Copy-and-paste mode — nothing to test."
    if mode == "api" and not has_api_key():
        return False, "No API key set."
    if mode == "cli" and not claude_cli_path():
        return False, ("Claude Code isn't installed, or isn't on PATH. Install it, "
                       "or set JOBPILOT_CLAUDE_BIN to its full path.")
    if mode == "local" and not settings.local_model:
        return False, "Pick which local model to use."

    try:
        from .llm import complete

        reply = complete("Reply with the single word: ready", max_tokens=16)
    except Exception as e:  # surface the backend's own wording, trimmed
        msg = str(e)
        low = msg.lower()
        if mode == "api":
            if "authentication" in low or "401" in msg:
                return False, "That key was rejected. Check for a typo or a revoked key."
            if any(w in low for w in ("credit", "billing", "quota")):
                return False, "The key is valid but the account has no credit available."
        if mode == "cli" and ("login" in low or "auth" in low):
            return False, "Claude Code is installed but not logged in. Run `claude` once in a terminal."
        return False, f"Couldn't get an answer: {msg[:180]}"

    where = {"api": "Your key works", "cli": "Claude Code works",
             "local": "Your local model works"}[mode]
    if "ready" in reply.lower():
        return True, f"{where} — a test request came back."
    return True, f"{where}. It replied: {reply[:60]}"


def check_api_key() -> tuple[bool, str]:
    """Back-compat alias from when an API key was the only way in."""
    return check_llm()


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
