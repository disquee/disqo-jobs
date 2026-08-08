"""Render tailored resume / cover letter Markdown to PDF.

Uses a tiny self-contained Markdown subset (headings, bold, lists, paragraphs)
so we avoid an extra markdown dependency. WeasyPrint turns HTML -> PDF.
"""

from __future__ import annotations

import os
import re
import sys
from html import escape
from pathlib import Path

from ..config import OUTPUT_DIR

# WeasyPrint's native libs (pango/gobject) install via Homebrew but aren't on
# macOS's default dyld search path. Add Homebrew's lib dir here — at import time,
# before the lazy `import weasyprint` in render_pdf — so PDF rendering works
# without the caller setting DYLD_FALLBACK_LIBRARY_PATH manually.
if sys.platform == "darwin":
    _brew_libs = [p for p in ("/opt/homebrew/lib", "/usr/local/lib") if os.path.isdir(p)]
    if _brew_libs:
        _existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        _parts = _existing.split(":") if _existing else []
        for _p in _brew_libs:
            if _p not in _parts:
                _parts.append(_p)
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(_parts)

CSS = """
@page { size: Letter; margin: 0.7in; }
body { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 10.5pt;
       line-height: 1.4; color: #1a1a1a; }
h1 { font-size: 20pt; margin: 0 0 2pt; }
h2 { font-size: 12pt; border-bottom: 1px solid #999; padding-bottom: 2pt;
     margin: 14pt 0 6pt; text-transform: uppercase; letter-spacing: 0.5px; }
h3 { font-size: 11pt; margin: 8pt 0 0; }
ul { margin: 4pt 0 4pt 16pt; padding: 0; }
li { margin: 2pt 0; }
p { margin: 4pt 0; }
em { color: #555; }
blockquote { margin: 6pt 0 10pt; padding: 5pt 10pt; border-left: 3px solid #999;
             background: #f5f5f3; font-style: italic; color: #333; }
blockquote p { margin: 2pt 0; }
hr { border: none; border-top: 1px solid #ccc; margin: 12pt 0; }
"""


def _md_to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    in_list = False
    in_quote = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def close_quote() -> None:
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    for line in lines:
        s = line.rstrip()
        if not s.strip():
            close_list(); close_quote()
            continue
        if s.strip() == "---":
            close_list(); close_quote(); out.append("<hr>")
        elif s.startswith("### "):
            close_list(); close_quote(); out.append(f"<h3>{_inline(s[4:])}</h3>")
        elif s.startswith("## "):
            close_list(); close_quote(); out.append(f"<h2>{_inline(s[3:])}</h2>")
        elif s.startswith("# "):
            close_list(); close_quote(); out.append(f"<h1>{_inline(s[2:])}</h1>")
        elif s.lstrip().startswith(("- ", "* ")):
            close_quote()
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline(s.lstrip()[2:])}</li>")
        elif s.startswith(">"):
            close_list()
            if not in_quote:
                out.append("<blockquote>"); in_quote = True
            out.append(f"<p>{_inline(s[1:].lstrip())}</p>")
        else:
            close_list(); close_quote(); out.append(f"<p>{_inline(s)}</p>")
    close_list(); close_quote()
    return "\n".join(out)


def _inline(text: str) -> str:
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<em>\1</em>", text)
    return text


_WARNED = False


def render_pdf(markdown: str, out_path: Path) -> Path:
    """Render Markdown to PDF. Falls back to a styled .html file if WeasyPrint's
    native libraries (pango/gobject) aren't installed, returning the path written."""
    html_doc = (
        f"<html><head><meta charset='utf-8'><style>{CSS}</style></head>"
        f"<body>{_md_to_html(markdown)}</body></html>"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from weasyprint import HTML  # lazy: heavy native dep

        HTML(string=html_doc).write_pdf(str(out_path))
        return out_path
    except (OSError, ImportError) as e:
        global _WARNED
        if not _WARNED:
            print(
                "[jobpilot] WeasyPrint unavailable (%s). Writing .html instead of "
                ".pdf. For PDFs run: brew install pango gdk-pixbuf libffi"
                % type(e).__name__
            )
            _WARNED = True
        fallback = out_path.with_suffix(".html")
        fallback.write_text(html_doc, encoding="utf-8")
        return fallback


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def resume_path(job) -> Path:
    return OUTPUT_DIR / "resumes" / f"{_slug(job.company)}-{job.id}.pdf"


def cv_path(job) -> Path:
    return OUTPUT_DIR / "cvs" / f"{_slug(job.company)}-{job.id}.pdf"


def cover_path(job) -> Path:
    return OUTPUT_DIR / "cover_letters" / f"{_slug(job.company)}-{job.id}.pdf"
