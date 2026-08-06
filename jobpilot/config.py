"""Configuration + profile loading and small shared helpers."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "profile"
OUTPUT_DIR = ROOT / "output"
DB_PATH = ROOT / "jobpilot.db"
CSV_PATH = OUTPUT_DIR / "applications.csv"

DEFAULT_MODEL = os.getenv("JOBPILOT_MODEL", "claude-opus-4-8")


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    path = ROOT / "config.yaml"
    with path.open() as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def load_profile() -> dict[str, Any]:
    with (PROFILE_DIR / "profile.yaml").open() as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def load_master_resume() -> str:
    return (PROFILE_DIR / "resume_master.md").read_text()


@lru_cache(maxsize=None)
def _keychain_get(service: str) -> str | None:
    """Read a secret from the macOS Keychain, or None if missing/unavailable.

    Looks up a generic password by service name, e.g. one stored with:
        security add-generic-password -a "$USER" -s ANTHROPIC_API_KEY -w 'sk-...'
    Safe (returns None) on non-macOS hosts or when the item doesn't exist.
    """
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", service],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def secret(key: str) -> str | None:
    """Resolve a secret: environment (incl. .env) first, then macOS Keychain."""
    return os.getenv(key) or _keychain_get(key)


def env(key: str, default: str = "") -> str:
    return secret(key) or default


def require_anthropic_key() -> str:
    key = secret("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Either copy .env.example to .env and fill "
            "it in, or store it in the macOS Keychain:\n"
            '  security add-generic-password -a "$USER" '
            "-s ANTHROPIC_API_KEY -w 'sk-ant-...'"
        )
    return key
