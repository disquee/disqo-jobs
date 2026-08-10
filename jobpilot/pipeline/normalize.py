"""Filter + dedupe normalized jobs against config rules."""

from __future__ import annotations

import re
from typing import Optional

from ..config import load_config
from ..models import Job

# ---- what a posting says about sponsorship and clearance -------------------
# Classified by phrase, not by an AI call, so these filters behave identically
# in all four LLM modes and cost nothing. Deliberately conservative: a
# phrasing the patterns don't recognize keeps the job, and the worst case is
# reviewing a posting the filter could have skipped.

_NO_SPONSOR = re.compile(
    r"(?:cannot|can\s?not|unable\s+to|will\s+not|won'?t|do(?:es)?\s+not|not\s+able\s+to)"
    r"[^.]{0,40}?sponsor"
    r"|no\s+(?:visa\s+|work\s+)?sponsorship"
    r"|without\s+(?:visa\s+)?sponsorship"
    r"|sponsorship\s+(?:is\s+)?(?:not\s+available|unavailable|not\s+offered|not\s+provided)",
    re.IGNORECASE,
)


def denies_sponsorship(text: str) -> bool:
    """True when the posting says visa sponsorship is not on offer."""
    return bool(_NO_SPONSOR.search(text or ""))


#: The clearance ladder, least to most exclusive. "any" marks postings that
#: require a clearance without naming which one.
CLEARANCE_RANK = {
    "none": 0, "any": 1, "public trust": 1,
    "secret": 2, "top secret": 3, "ts/sci": 4,
}

#: How each detected level reads on a page.
CLEARANCE_LABELS = {
    "any": "Clearance required",
    "public trust": "Public Trust clearance",
    "secret": "Secret clearance",
    "top secret": "Top Secret clearance",
    "ts/sci": "TS/SCI clearance",
}

# Most exclusive first, so "Top Secret clearance" never reads as plain Secret.
_CLEARANCE_PATTERNS = (
    ("ts/sci", re.compile(
        r"ts\s*/\s*sci|\bts-sci\b|\bsci\b[^.]{0,20}clearance|polygraph", re.IGNORECASE)),
    ("top secret", re.compile(r"top\s+secret", re.IGNORECASE)),
    ("secret", re.compile(
        r"\bsecret\b[^.]{0,30}clearance|clearance[^.]{0,30}\bsecret\b", re.IGNORECASE)),
    ("public trust", re.compile(r"public\s+trust", re.IGNORECASE)),
    # "customs clearance" is logistics, not security — don't flag it.
    ("any", re.compile(
        r"(?:security|government|active|current)\s+clearance"
        r"|(?<!customs )clearance\s+(?:is\s+)?required"
        r"|must\s+(?:hold|have|possess)[^.]{0,30}clearance", re.IGNORECASE)),
)


def required_clearance(text: str) -> Optional[str]:
    """The clearance level a posting asks for, or None when none is mentioned."""
    for level, rx in _CLEARANCE_PATTERNS:
        if rx.search(text or ""):
            return level
    return None


def dealbreaker_filters_active(cfg: dict) -> bool:
    """Whether either posting-text filter has been switched on at all."""
    return bool(cfg.get("needs_sponsorship")) or bool(
        str(cfg.get("clearance_held") or "").strip()
    )


def hits_dealbreaker(job: Job, cfg: dict) -> bool:
    """True when the configured filters would drop this posting.

    One decision shared by discovery and the queue sweep, so the two can
    never disagree about what counts as a dealbreaker.
    """
    posting = f"{job.title}\n{job.description}"
    if cfg.get("needs_sponsorship") and denies_sponsorship(posting):
        return True
    # Blank means the user never said — filter nothing rather than guess.
    held = str(cfg.get("clearance_held") or "").strip().lower()
    if held:
        required = required_clearance(posting)
        if required and CLEARANCE_RANK[required] > CLEARANCE_RANK.get(held, 0):
            return True
    return False


def apply_filters(jobs: list[Job]) -> list[Job]:
    cfg = load_config()
    bad_titles = [k.lower() for k in cfg.get("exclude_title_keywords", [])]
    bad_company = [c.lower() for c in cfg.get("exclude_company", [])]

    kept: list[Job] = []
    seen: set[str] = set()
    for job in jobs:
        if not job.title or not job.apply_url:
            continue
        title_l = job.title.lower()
        company_l = job.company.lower()
        if any(k in title_l for k in bad_titles):
            continue
        if any(c in company_l for c in bad_company):
            continue
        if hits_dealbreaker(job, cfg):
            continue
        if job.id in seen:
            continue
        seen.add(job.id)
        kept.append(job)
    return kept
