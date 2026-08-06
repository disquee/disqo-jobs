#!/usr/bin/env python3
"""Build a distributable jobpilot archive with no personal data in it.

Deliberately allowlist-based: a file ships only if it matches ALLOW below. A
denylist would let any newly-created file leak by default, which is exactly the
failure mode this guards against (the repo has held signed contracts, interview
prep, and a real resume alongside the source).

    python scripts/package.py            # build + scan, writes dist/
    python scripts/package.py --check    # scan only, build nothing

Exits non-zero if the staged tree contains anything matching a PII pattern.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Only these ship. Directories are copied recursively, minus PRUNE.
ALLOW: list[str] = [
    "jobpilot",                    # the package (incl. pipeline/templates)
    "tests",
    "scripts/package.py",
    "examples/prep.sample.json",   # anonymized sample loop
    "profile/profile.example.yaml",
    "profile/resume_master.example.md",
    "pyproject.toml",
    "README.md",
    "SETUP_PROMPT.md",
    ".env.example",
    ".gitignore",
]

# Never ship, even if inside an allowed directory.
PRUNE_DIRS = {"__pycache__", ".pytest_cache", ".DS_Store", ".impeccable"}
PRUNE_SUFFIX = {".pyc", ".pyo", ".db", ".docx"}
PRUNE_NAMES = {".pii-local"}

# config.yaml ships, but the personal targeting is replaced with a neutral starter.
GITIGNORE_STARTER = """# Secrets
.env
.env.*
!.env.example

# Personal profile (PII) — keep your real resume and profile out of git.
# The *.example.* templates ARE committed.
profile/profile.yaml
profile/resume_master.md
profile/star_stories.csv
profile/projects/

# Python
__pycache__/
*.pyc
.venv/
venv/
.pytest_cache/

# Local data / generated artifacts
jobpilot.db
output/
dist/
"""

CONFIG_STARTER = """# jobpilot configuration — starter. Edit freely.
#
# Matching notes:
#   - Adzuna/Jooble full-text search query + location.
#   - Greenhouse/Lever substring-match `query` against the job TITLE and ignore
#     `location`, so keep queries to short phrases that appear in titles.

searches:
  - query: "your target role"
    location: "Remote"
  - query: "another target role"
    location: "United States"

# Company ATS boards to pull from (public JSON endpoints). `slug` is the company
# identifier in the board URL; an unknown slug just returns nothing.
ats:
  greenhouse:
    - stripe
    - gitlab
    - datadog
  lever: []

results_per_search: 25      # cap per source per query
fit_threshold: 55           # 0-100; recalibrate for your model (see SETUP_PROMPT.md)
max_apply_per_day: 15       # safety cap on assisted-apply actions

exclude_title_keywords:
  - "intern"
  - "clearance"
exclude_company: []
"""

# Anything matching these in the staged tree is a build failure.
# Generic only — deliberately no names. Owner-specific terms live in .pii-local
# (gitignored, never shipped), because a hard-coded pattern list would itself
# disclose exactly what it's meant to protect.
PII_PATTERNS: list[tuple[str, str]] = [
    (r"sk-ant-[A-Za-z0-9_\-]{8,}", "Anthropic API key"),
    (r"sk-[A-Za-z0-9]{32,}", "OpenAI-style API key"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key"),
    (r"\bghp_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", "Slack token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"\b\d{3}[.\-]\d{3}[.\-]\d{4}\b", "phone number"),
    (r"\b[\w.+-]+@(?!example\.(?:com|org)\b)[\w-]+\.[\w.]{2,}\b", "email address"),
]

PII_LOCAL = ROOT / ".pii-local"


def _local_patterns() -> list[tuple[str, str]]:
    """Extra terms from .pii-local: one regex per line, # comments allowed."""
    if not PII_LOCAL.exists():
        return []
    out = []
    for line in PII_LOCAL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append((line, "local rule"))
    return out


TEXT_SUFFIX = {
    ".py", ".md", ".txt", ".yaml", ".yml", ".json", ".html", ".css", ".js",
    ".toml", ".cfg", ".ini", ".csv", ".example", "",
}


def _prune(path: Path) -> bool:
    if path.name in PRUNE_DIRS or path.name in PRUNE_NAMES or path.suffix in PRUNE_SUFFIX:
        return True
    return any(part in PRUNE_DIRS for part in path.parts)


def stage(dest: Path) -> list[Path]:
    """Copy the allowlist into ``dest``. Returns the staged files."""
    staged: list[Path] = []
    for rel in ALLOW:
        src = ROOT / rel
        if not src.exists():
            print(f"  ! missing, skipped: {rel}")
            continue
        if src.is_dir():
            for f in sorted(src.rglob("*")):
                if f.is_dir() or _prune(f):
                    continue
                out = dest / f.relative_to(ROOT)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out)
                staged.append(out)
        else:
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            staged.append(out)

    cfg = dest / "config.yaml"
    cfg.write_text(CONFIG_STARTER, encoding="utf-8")
    staged.append(cfg)

    gi = dest / ".gitignore"
    gi.write_text(GITIGNORE_STARTER, encoding="utf-8")
    return staged


def scan(files: list[Path], base: Path) -> list[str]:
    """Return a list of human-readable PII findings."""
    compiled = [(re.compile(p, re.IGNORECASE), why)
                for p, why in PII_PATTERNS + _local_patterns()]
    findings: list[str] = []
    for f in files:
        if f.suffix.lower() not in TEXT_SUFFIX:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for rx, why in compiled:
                m = rx.search(line)
                if m:
                    rel = f.relative_to(base)
                    findings.append(f"{rel}:{line_no}  [{why}]  …{m.group(0)}…")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="Scan only; don't write an archive.")
    ap.add_argument("--out", default="dist", help="Output directory (default: dist).")
    args = ap.parse_args()

    version = "0.1.0"
    for line in (ROOT / "pyproject.toml").read_text().splitlines():
        if line.strip().startswith("version"):
            version = line.split("=", 1)[1].strip().strip('"')
            break

    with tempfile.TemporaryDirectory() as tmp:
        stem = f"jobpilot-{version}"
        dest = Path(tmp) / stem
        dest.mkdir(parents=True)
        print(f"Staging {stem} …")
        files = stage(dest)
        print(f"  {len(files)} files staged")

        print("Scanning for personal data …")
        findings = scan(files, dest)
        if findings:
            print(f"\n  FAILED — {len(findings)} finding(s):\n")
            for f in findings[:40]:
                print(f"    {f}")
            if len(findings) > 40:
                print(f"    … and {len(findings) - 40} more")
            print("\nNothing was written. Fix the files or widen ALLOW deliberately.")
            return 1
        print("  clean — no PII patterns matched")

        if args.check:
            print("\n--check: archive not written.")
            return 0

        out_dir = ROOT / args.out
        out_dir.mkdir(parents=True, exist_ok=True)
        archive = out_dir / f"{stem}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(dest, arcname=stem)
        size = archive.stat().st_size // 1024
        print(f"\nWrote {archive.relative_to(ROOT)} ({size} KB)")
        print("\nSend that file. Recipient runs:")
        print(f"  tar xzf {archive.name} && cd {stem}")
        print("  python -m venv .venv && source .venv/bin/activate")
        print('  pip install -e ".[dev]"')
        print("  # then follow SETUP_PROMPT.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
