"""Configuration + profile loading and small shared helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "profile"


def data_dir() -> Path:
    """Where the user's own data lives — deliberately OUTSIDE the app folder.

    A job search runs for months and the work-search log can be needed for
    unemployment reporting long after the fact. Keeping it next to the code
    means "download the new version" or a tidy-up of the folder destroys it.
    Override with JOBPILOT_DATA_DIR (useful for pointing at a synced folder).
    """
    override = os.getenv("JOBPILOT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "jobpilot"
    if os.name == "nt":
        return Path(os.getenv("APPDATA") or Path.home()) / "jobpilot"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "jobpilot"


DATA_DIR = data_dir()
OUTPUT_DIR = DATA_DIR / "output"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "jobpilot.db"
CSV_PATH = OUTPUT_DIR / "applications.csv"


def _migrate_legacy_data() -> None:
    """Move data written by older versions out of the app folder, once.

    Idempotent and non-destructive: anything already in the new location wins
    and the legacy copy is left alone rather than merged.
    """
    moves = [(ROOT / "jobpilot.db", DB_PATH), (ROOT / "output", OUTPUT_DIR)]
    if not any(old.exists() for old, _ in moves):
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for old, new in moves:
            if old.exists() and not new.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(new))
                print(f"[jobpilot] moved {old.name} -> {new}", file=sys.stderr)
    except OSError as e:  # never let a migration failure block startup
        print(f"[jobpilot] could not move existing data ({e}); using {DATA_DIR}",
              file=sys.stderr)


_migrate_legacy_data()

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
