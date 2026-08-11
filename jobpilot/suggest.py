"""Suggest job titles to search, and directions to pivot.

One LLM call over the master resume and the current searches, returning two
lists: titles the resume already fits, and pivots — adjacent directions with
an entry title to test the market with. In manual mode the caller renders
``build_prompt()`` for the user to paste into their own chat, and
``parse_reply()`` reads what they paste back, so every AI mode gets the
same feature.

Suggestions are only ever offered; nothing is added to the search list until
the user picks it.
"""

from __future__ import annotations

from typing import Any

from .config import PROFILE_DIR
from .llm import _extract_json, complete_json
from .searchconfig import current as search_config

#: Enough resume to judge fit, small enough to keep the call cheap.
_RESUME_LIMIT = 6000
_MAX_TITLES = 12
_MAX_PIVOTS = 5

SYSTEM = "You are a job-search assistant. Respond with valid JSON only, no prose, no code fences."

_PROMPT = """Here is a job seeker's resume and the searches they already run.

<resume>
{resume}
</resume>

Current search queries: {queries}

Suggest search terms for job boards, in two groups:

1. "titles": up to 8 job titles this resume already fits. Write them the way
   they appear on real postings, 2 to 4 words. Skip anything already searched.
2. "pivots": 3 adjacent directions this person could move toward. For each,
   name the direction, say in one sentence why their experience transfers,
   and give one entry-level-of-that-path title to search to test the market.

Return JSON shaped exactly like this:
{{"titles": [{{"title": "...", "why": "..."}}],
 "pivots": [{{"direction": "...", "why": "...", "entry_title": "..."}}]}}

Every "why" is one short sentence in plain language, written to the job
seeker ("your support background covers half of this job").
"""


def build_prompt() -> str:
    resume = (PROFILE_DIR / "resume_master.md").read_text(encoding="utf-8")[:_RESUME_LIMIT]
    queries = [s.get("query", "") for s in search_config().get("searches", []) if s.get("query")]
    return _PROMPT.format(resume=resume, queries=", ".join(queries) or "none yet")


def parse_reply(raw: Any) -> dict:
    """Normalize a model reply (parsed or pasted text) into the two lists.

    Tolerates fences and stray prose around the JSON, missing keys, and
    entries with blank fields — a manual-mode paste comes from any chat UI
    the user happens to have.
    """
    data = _extract_json(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object with 'titles' and 'pivots'")

    titles = []
    for t in data.get("titles") or []:
        title = str(t.get("title", "")).strip() if isinstance(t, dict) else str(t).strip()
        if title:
            why = str(t.get("why", "")).strip() if isinstance(t, dict) else ""
            titles.append({"title": title, "why": why})

    pivots = []
    for p in data.get("pivots") or []:
        if not isinstance(p, dict):
            continue
        entry = str(p.get("entry_title", "")).strip()
        direction = str(p.get("direction", "")).strip()
        if entry or direction:
            pivots.append({
                "direction": direction or entry,
                "why": str(p.get("why", "")).strip(),
                "entry_title": entry or direction,
            })

    return {"titles": titles[:_MAX_TITLES], "pivots": pivots[:_MAX_PIVOTS]}


def suggest() -> dict:
    """Run the call in whatever automatic AI mode is configured."""
    return parse_reply(complete_json(build_prompt(), system=SYSTEM, max_tokens=1500))
