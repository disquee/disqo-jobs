#!/usr/bin/env python3
"""Build a distributable jobpilot archive with no personal data in it.

Deliberately allowlist-based: a file ships only if it matches ALLOW below. A
denylist would let any newly-created file leak by default, which is exactly the
failure mode this guards against (the repo has held signed contracts, interview
prep, and a real resume alongside the source).

    python scripts/package.py             # build + scan, writes dist/
    python scripts/package.py --check     # scan only, build nothing
    python scripts/package.py --publish   # sync the public repo, commit, show diff
    python scripts/package.py --publish --push   # ...and push it

Exits non-zero if the staged tree contains anything matching a PII pattern.

Two-repo workflow: this checkout is the private dev repo (personal profile,
output, real config). The public repo receives only the allowlisted, scanned
tree — content can reach it no other way, which is the point.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
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


# Matches that are anonymous by construction, so flagging them is noise.
BENIGN = re.compile(r"noreply|example\.(?:com|org)|@users\.noreply\.", re.IGNORECASE)

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
                    if BENIGN.search(m.group(0)):
                        continue
                    rel = f.relative_to(base)
                    findings.append(f"{rel}:{line_no}  [{why}]  …{m.group(0)}…")
    return findings


PUBLIC_REPO = "disquee/disqo-jobs"


def _git(*args: str, cwd: Path) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def publish(staged_dir: Path, repo: str, message: str, do_push: bool) -> int:
    """Mirror the staged tree into a clone of the public repo and commit it."""
    work = ROOT / "dist" / ".publish"
    work.mkdir(parents=True, exist_ok=True)
    clone = work / repo.split("/")[-1]

    if not (clone / ".git").exists():
        print(f"Cloning {repo} …")
        subprocess.run(["gh", "repo", "clone", repo, str(clone)], check=True,
                       capture_output=True, text=True)
    else:
        _git("fetch", "origin", cwd=clone)
        _git("reset", "--hard", "origin/HEAD", cwd=clone)

    # Replace tracked content wholesale so deletions propagate too.
    for item in clone.iterdir():
        if item.name == ".git":
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    for item in staged_dir.iterdir():
        dst = clone / item.name
        shutil.copytree(item, dst) if item.is_dir() else shutil.copy2(item, dst)

    _git("add", "-A", cwd=clone)
    status = _git("status", "--porcelain", cwd=clone)
    if not status:
        print("\nPublic repo already matches this build — nothing to commit.")
        return 0

    print("\nChanges to publish:\n")
    for line in status.splitlines():
        print(f"    {line}")

    email = _git("log", "-1", "--pretty=%ae", cwd=clone) or "noreply@github.com"
    name = _git("log", "-1", "--pretty=%an", cwd=clone) or "jobpilot"
    _git("-c", f"user.name={name}", "-c", f"user.email={email}",
         "commit", "-q", "-m", message, cwd=clone)
    print(f"\nCommitted to {clone.relative_to(ROOT)} as {name} <{email}>")

    if not do_push:
        print("Dry run — not pushed. Re-run with --push to publish.")
        return 0
    _git("push", "origin", "HEAD", cwd=clone)
    print(f"Pushed to {repo}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="Scan only; don't write an archive.")
    ap.add_argument("--out", default="dist", help="Output directory (default: dist).")
    ap.add_argument("--publish", action="store_true",
                    help="Sync the public repo with this build and commit.")
    ap.add_argument("--push", action="store_true",
                    help="With --publish, actually push. Without it, dry run.")
    ap.add_argument("--repo", default=PUBLIC_REPO, help=f"Public repo (default: {PUBLIC_REPO}).")
    ap.add_argument("-m", "--message", default="", help="Commit message for --publish.")
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

        if args.publish:
            msg = args.message or f"Sync from dev repo (jobpilot {version})"
            return publish(dest, args.repo, msg, args.push)

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
