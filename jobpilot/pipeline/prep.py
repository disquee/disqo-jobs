"""Render an interview-prep data file into a self-contained HTML page.

The page is a single file with no external requests, so it works offline from
``file://``. Content lives in a JSON document; this module only injects it into
the template shell (nav, search, story tracker, rehearse mode, cheat card).
"""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).parent / "templates" / "prep.html"


def render_prep(data: dict, out_path: Path) -> Path:
    """Write the prep page for ``data`` to ``out_path`` and return the path."""
    for key in ("meta", "panels", "stories", "sections"):
        if key not in data:
            raise ValueError(f"prep data is missing required key: {key!r}")

    meta = data["meta"]
    known = {s["id"] for s in data["sections"]}
    missing = [s for s in meta.get("order", []) if s not in known]
    if missing:
        raise ValueError(f"meta.order references unknown sections: {', '.join(missing)}")
    unordered = [s for s in known if s not in meta.get("order", [])]
    if unordered:
        raise ValueError(f"sections missing from meta.order: {', '.join(sorted(unordered))}")

    title = f"{meta.get('title', 'Interview')} — Interview Prep"
    # </script> inside the payload would close the tag early.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__TITLE__", title).replace("__PREP_DATA__", payload)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def render_prep_file(
    data_path: Path, out_path: Path | None = None, docs: bool = True
) -> dict[str, Path]:
    """Render a prep JSON file into a page and, by default, Markdown + PDF.

    Returns a dict of {"html": ..., "md": ..., "pdf": ...} for what was written.
    The Markdown basename comes from ``meta.doc_file`` (default ``<file>-doc``) so
    it can never collide with the generated page. All three land in the same
    directory as ``out_path``, so ``-o`` moves the whole set.
    """
    data = json.loads(data_path.read_text(encoding="utf-8"))
    meta = data.get("meta", {})
    name = meta.get("file") or data_path.stem
    if out_path is None:
        out_path = data_path.with_name(f"{name}.html")

    written = {"html": render_prep(data, out_path)}
    if not docs:
        return written

    doc_name = meta.get("doc_file") or f"{name}-doc"
    if doc_name == out_path.stem:
        raise ValueError("meta.doc_file must differ from meta.file (it would overwrite the page)")

    md_path = out_path.with_name(f"{doc_name}.md")
    markdown = render_prep_markdown(data)
    md_path.write_text(markdown, encoding="utf-8")
    written["md"] = md_path

    try:
        from .render import render_pdf

        written["pdf"] = render_pdf(markdown, out_path.with_name(f"{doc_name}.pdf"))
    except Exception:  # PDF is a convenience; never fail the page render over it
        pass
    return written


# ---------------------------------------------------------------- markdown ---
# The prep JSON is the single source of truth. The Markdown (and the PDF rendered
# from it) is generated, so the two can't drift.

import re as _re


def _html_to_md(html: str) -> str:
    """Callout blocks carry small HTML fragments; flatten them to Markdown."""
    text = html.replace("</p><p>", "\n\n")
    text = _re.sub(r"<br\s*/?>", "\n", text)
    text = _re.sub(r"</?(?:strong|b)>", "**", text)
    text = _re.sub(r"</?(?:em|i)>", "*", text)
    text = _re.sub(r"</?code>", "`", text)
    text = _re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _blockquote(text: str) -> list[str]:
    return [f"> {line}" if line.strip() else ">" for line in text.splitlines()]


def _md_prose(sec: dict) -> list[str]:
    out: list[str] = []
    for b in sec.get("blocks", []):
        kind = b.get("type")
        if kind == "h3":
            out += [f"### {b['text']}", ""]
        elif kind == "p":
            out += [b["text"], ""]
        elif kind == "ul":
            out += [f"- {i}" for i in b["items"]] + [""]
        elif kind == "callout":
            out += _blockquote(_html_to_md(b["html"])) + [""]
    return out


def _md_checklist(sec: dict) -> list[str]:
    out: list[str] = []
    for g in sec.get("groups", []):
        out += [f"### {g['title']}", ""]
        if g.get("note"):
            out += [f"*{g['note']}*", ""]
        for it in g["items"]:
            line = f"- **[ ]** {it['text']}"
            if it.get("detail"):
                line += f" — {it['detail']}"
            out.append(line)
        out.append("")
    return out


def _md_tracker(sec: dict, data: dict) -> list[str]:
    panels = data["panels"]
    out: list[str] = []
    for s in data["stories"]:
        rec = ", ".join(panels[p]["name"] for p in s.get("rec", []) if p in panels)
        line = f"- **{s['id']} · {s['label']}** — {s['metric']}"
        if rec:
            line += f" *(lead with: {rec})*"
        out.append(line)
    return out + [""]


def _md_qa(sec: dict) -> list[str]:
    out: list[str] = []
    for it in sec.get("items", []):
        out += [f"### {it['id']}. {it['q']}", ""]
        if it.get("label"):
            out += [f"**Story: {it['label']}**", ""]
        if it.get("tag"):
            tags = " · ".join([it["tag"], *it.get("stories", [])])
            out += [f"*{tags}*", ""]
        if it.get("note"):
            out += [f"*{it['note']}*", ""]
        star = it.get("star")
        if star:
            for key, label in (("s", "Situation"), ("t", "Task"), ("a", "Action"), ("r", "Result")):
                if star.get(key):
                    out.append(f"**{label}:** {star[key]}")
            out.append("")
        for b in it.get("beats", []):
            if b.get("l"):
                out += [f"**{b['l']}**", ""]
            if b.get("d"):
                out += [f"*{b['d']}*", ""]
            if b.get("t"):
                out += [b["t"], ""]
            if b.get("items"):
                out += [f"- {i}" for i in b["items"]] + [""]
        if it.get("punch"):
            out += _blockquote(f"**{it['punch']['label']}:** {it['punch']['text']}") + [""]
    return out


def _md_people(sec: dict, data: dict) -> list[str]:
    out: list[str] = []
    for c in sec.get("cards", []):
        p = data["panels"][c["panel"]]
        out += [f"### {p['name']} — {p['role']}", "", f"*{c['when']} · {p['focus']}*", ""]
        out += ["**What they're scoring**", ""] + [f"- {i}" for i in c["scoring"]] + [""]
        out += ["**Lead with**", ""] + [f"- **{l['id']}** — {l['label']}" for l in c["lead"]] + [""]
        out += [f"**Watch for:** {c['watch']}", ""]
        if c.get("questions"):
            out += ["**Ask them**", ""] + [f"- {q}" for q in c["questions"]] + [""]
    if sec.get("anyGroup"):
        g = sec["anyGroup"]
        out += [f"### {g['title']}", ""] + [f"- {i}" for i in g["items"]] + [""]
    return out


def _md_numbers(sec: dict) -> list[str]:
    return [f"- **{r['v']}** — {r['s']}. *{r['w']}*" for r in sec.get("rows", [])] + [""]


def _md_resume(sec: dict) -> list[str]:
    out: list[str] = []
    for tab in sec.get("tabs", []):
        out += [f"### {tab['label']}", ""]
        if tab.get("note"):
            out += [f"*{tab['note']}*", ""]
        for b in tab.get("blocks", []):
            t, text = b.get("t"), b.get("text", "")
            if t == "name":
                out += [f"**{text}**", ""]
            elif t in ("title", "contact"):
                out += [_html_to_md(text), ""]
            elif t == "head":
                out += [f"**{text.upper()}**", ""]
            elif t == "role":
                out += [f"**{text}**", ""]
            elif t == "meta":
                out += [f"*{text}*", ""]
            elif t == "p":
                out += [_html_to_md(text), ""]
            elif t == "ul":
                out += [f"- {i}" for i in b["items"]] + [""]
        out.append("---")
        out.append("")
    return out


def render_prep_markdown(data: dict) -> str:
    """Render the prep data as Markdown (input for the PDF renderer)."""
    meta = data["meta"]
    by_id = {s["id"]: s for s in data["sections"]}
    lines = [
        f"# {meta.get('title', 'Interview prep')} — {meta.get('subtitle', '')}".rstrip(" —"),
        "",
        "*Generated by `jobpilot prep` from the prep JSON. Edit the JSON, not this file.*",
        "",
    ]
    for sec_id in meta["order"]:
        sec = by_id[sec_id]
        lines += ["---", "", f"## {sec['title']}", ""]
        if sec.get("lede"):
            lines += [sec["lede"], ""]
        kind = sec["kind"]
        if kind == "prose":
            lines += _md_prose(sec)
        elif kind == "checklist":
            lines += _md_checklist(sec)
        elif kind == "tracker":
            lines += _md_tracker(sec, data)
        elif kind == "qa":
            lines += _md_qa(sec)
        elif kind == "people":
            lines += _md_people(sec, data)
        elif kind == "numbers":
            lines += _md_numbers(sec)
        elif kind == "resume":
            lines += _md_resume(sec)
    # collapse runs of blank lines
    out, blank = [], False
    for line in lines:
        if not line.strip():
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------- scaffold from a job ---
# Ties the scoring/posting data to interview prep: a job's prep page opens
# pre-loaded with its posting text, fit score and rationale, and (once tailored)
# the resume that was actually generated for it.

from ..config import OUTPUT_DIR  # noqa: E402


def _job_slug(job) -> str:
    base = _re.sub(r"[^a-z0-9]+", "-", (job.company or "job").lower()).strip("-")[:40]
    return f"{base}-{job.id}"


def prep_json_path(job) -> Path:
    return OUTPUT_DIR / "interviews" / f"{_job_slug(job)}-prep.json"


def prep_html_path(job) -> Path:
    return OUTPUT_DIR / "interviews" / f"{_job_slug(job)}-prep.html"


def _posting_blocks(job) -> list[dict]:
    """The posting, plus the score jobpilot gave it, as prose blocks."""
    meta = [f"**Company:** {job.company}", f"**Role:** {job.title}"]
    if job.location:
        meta.append(f"**Location:** {job.location}")
    if job.salary:
        meta.append(f"**Salary:** {job.salary}")
    if job.source:
        meta.append(f"**Source:** {job.source}")
    if job.apply_url:
        meta.append(f"**Posting:** {job.apply_url}")
    if job.fit_score is not None:
        meta.append(f"**jobpilot fit score:** {job.fit_score}")

    blocks: list[dict] = [
        {"type": "callout", "tone": "accent",
         "html": "<p>" + "<br>".join(
             _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", m) for m in meta) + "</p>"}
    ]
    if job.fit_rationale:
        blocks += [
            {"type": "h3", "text": "Why it scored this way"},
            {"type": "p", "text": job.fit_rationale},
        ]
    blocks.append({"type": "h3", "text": "Posting"})
    for para in (job.description or "").split("\n\n"):
        para = para.strip()
        if not para:
            continue
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if lines and all(ln.startswith(("-", "•", "*")) for ln in lines):
            blocks.append({"type": "ul", "items": [ln.lstrip("-•* ").strip() for ln in lines]})
        else:
            blocks.append({"type": "p", "text": " ".join(lines)})
    return blocks


def _resume_tab(application) -> list[dict]:
    """Turn the tailored resume Markdown into resume-tab blocks."""
    if not application or not application.tailored_resume_md:
        return [{"t": "p", "text": "*No tailored resume yet — run tailoring from the dashboard.*"}]
    blocks: list[dict] = []
    bullets: list[str] = []

    def flush():
        nonlocal bullets
        if bullets:
            blocks.append({"t": "ul", "items": bullets})
            bullets = []

    for raw in application.tailored_resume_md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("# "):
            flush(); blocks.append({"t": "name", "text": line[2:]})
        elif line.startswith("## "):
            flush(); blocks.append({"t": "head", "text": line[3:]})
        elif line.startswith("### "):
            flush(); blocks.append({"t": "role", "text": line[4:]})
        elif line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            flush(); blocks.append({"t": "meta", "text": line.strip("*")})
        elif line.lstrip().startswith(("- ", "* ")):
            bullets.append(line.lstrip()[2:])
        else:
            flush(); blocks.append({"t": "p", "text": line})
    flush()
    return blocks


def scaffold_from_job(job, application=None) -> dict:
    """Build a starter prep document for ``job``, pre-loaded with its real data."""
    panel = "panel1"
    return {
        "meta": {
            "title": f"{job.company} · Prep",
            "subtitle": job.title,
            "file": f"{_job_slug(job)}-prep",
            "doc_file": f"{_job_slug(job)}-prep-doc",
            "order": ["brief", "checklist", "stories", panel, "people",
                      "numbers", "posting", "resume"],
        },
        "panels": {
            panel: {"name": "Interviewer", "role": "Add their title",
                    "focus": "What this conversation covers"},
        },
        "stories": [
            {"id": "S1", "label": "Add your first story", "metric": "The number it turns on",
             "rec": [panel]},
        ],
        "sections": [
            {"id": "brief", "nav": "The Loop", "title": "How to read this loop",
             "lede": "Scaffolded by jobpilot from the posting. Replace this with what you learn from the recruiter.",
             "kind": "prose",
             "blocks": [
                 {"type": "callout", "tone": "accent",
                  "html": f"<p><strong>{job.title}</strong> at <strong>{job.company}</strong>"
                          + (f" · fit score <strong>{job.fit_score}</strong>" if job.fit_score is not None else "")
                          + "</p>"},
                 {"type": "h3", "text": "Fill these in first"},
                 {"type": "ul", "items": [
                     "Who you're meeting, their title, and **the order** you meet them.",
                     "What each one is **scoring** — recruiters often say this outright in the brief.",
                     "Which **story** you lead with for each, so you don't repeat one across a panel.",
                 ]},
             ]},
            {"id": "checklist", "nav": "Checklist", "title": "Before the loop",
             "lede": "Decisions first, rehearsal second.", "kind": "checklist",
             "groups": [{"title": "1 · Decide and confirm", "items": [
                 {"id": "k1", "text": "Confirm **who** you're meeting and in **what order**."},
                 {"id": "k2", "text": "Decide your **salary number** and say it without hedging."},
                 {"id": "k3", "text": "Re-read the posting and mark the competencies you have **no story for**.",
                  "go": {"sec": "posting", "id": None}},
                 {"id": "k4", "text": "Skim your **tailored resume** — that's what they're reading.",
                  "go": {"sec": "resume", "id": None}},
             ]}]},
            {"id": "stories", "nav": "Story Rotation", "title": "Story rotation tracker",
             "lede": "One story per interviewer. Panels compare notes.", "kind": "tracker"},
            {"id": panel, "nav": "Interviewer", "panel": panel, "title": "Add this conversation",
             "lede": "One card per anticipated question. Give each a short label you can read live.",
             "kind": "qa",
             "items": [{
                 "id": "Q1", "tag": "STAR", "label": "Add a short memory jog",
                 "q": "Tell me about a time you…",
                 "note": "Replace with a real question. Keep the label verb-first and concrete.",
                 "stories": ["S1"],
                 "star": {"s": "The situation, with the constraint that made it hard.",
                          "t": "What you had to change.",
                          "a": "What you actually did — the mechanism, not just the outcome.",
                          "r": "The result, with the number."},
                 "punch": {"label": "The line", "text": "The one sentence worth repeating in a debrief."},
             }]},
            {"id": "people", "nav": "People", "title": "Who you're meeting",
             "lede": "One card per interviewer, in the order you meet them.", "kind": "people",
             "cards": [{"panel": panel, "when": "Interview 1",
                        "scoring": ["Add what this person is evaluating"],
                        "lead": [{"id": "Q1", "sec": panel, "label": "Add a short memory jog"}],
                        "watch": "Anything that changes how you pitch to this person.",
                        "questions": ["What does success look like at six months?"]}],
             "anyGroup": {"title": "For any of them", "items": [
                 "Is this a **new role or a backfill** — and what did the last person get furthest on?"]}},
            {"id": "numbers", "nav": "Numbers", "title": "Numbers cheat sheet",
             "lede": "Lead with the number when they're moving fast.", "kind": "numbers",
             "rows": [{"v": "Add a metric", "s": "Where it came from", "w": "What it proves."}]},
            {"id": "posting", "nav": "Posting", "title": "The job posting",
             "lede": "Pulled from the posting jobpilot scored. **The competency language here is what your answers are scored against.**",
             "kind": "prose", "blocks": _posting_blocks(job)},
            {"id": "resume", "nav": "Resume", "title": "Resume",
             "lede": "The tailored resume generated for this job — **what the interviewer is reading from.**",
             "kind": "resume",
             "tabs": [{"id": "sent", "label": "Tailored for this role",
                       "note": "Generated by jobpilot for this posting.",
                       "blocks": _resume_tab(application)}]},
        ],
    }


def ensure_job_prep(job, application=None, regenerate: bool = False) -> dict[str, Path]:
    """Create the prep JSON for ``job`` if absent, then render the page."""
    json_path = prep_json_path(job)
    if regenerate or not json_path.exists():
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(scaffold_from_job(job, application), indent=2), encoding="utf-8"
        )
    return render_prep_file(json_path, prep_html_path(job))
