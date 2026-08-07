"""Turn things users already have into things jobpilot can use.

Two jobs: pull text out of an uploaded resume (PDF / DOCX / Markdown / plain
text), and pull a job posting out of a URL. Both are best-effort by design —
whatever comes out is shown to the user for editing before anything is saved.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from typing import Optional

import httpx

# --------------------------------------------------------------- resumes ---

def _docx_text(data: bytes) -> str:
    """Read a .docx with the standard library — it's a zip of XML."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(xml)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - depends on install
        raise RuntimeError(
            "Reading PDFs needs the pypdf package. Install it, or paste your "
            "resume text instead."
        ) from e
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def resume_text(filename: str, data: bytes) -> str:
    """Extract text from an uploaded resume. Raises RuntimeError with a message
    suitable for showing the user."""
    name = (filename or "").lower()
    try:
        if name.endswith(".docx"):
            text = _docx_text(data)
        elif name.endswith(".pdf"):
            text = _pdf_text(data)
        elif name.endswith((".md", ".markdown", ".txt", ".text")):
            text = data.decode("utf-8", "ignore")
        else:
            raise RuntimeError(
                f"Unsupported file type: {filename or 'unnamed'}. "
                "Upload a PDF, Word (.docx), Markdown, or text file."
            )
    except zipfile.BadZipFile as e:
        raise RuntimeError("That .docx looks corrupted or isn't really a Word file.") from e

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 120:
        raise RuntimeError(
            "Barely any text came out of that file — it may be a scan or image-only "
            "PDF. Paste your resume text instead."
        )
    return text


# -------------------------------------------------------------- postings ---

_STRIP_TAGS = re.compile(r"<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>",
                         re.IGNORECASE | re.DOTALL)
_BLOCK_END = re.compile(r"</(p|div|li|tr|h[1-6]|section|article|br)\s*>", re.IGNORECASE)


def html_to_text(markup: str) -> str:
    text = _STRIP_TAGS.sub(" ", markup)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = _BLOCK_END.sub("\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _meta(markup: str, *names: str) -> Optional[str]:
    for name in names:
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)',
            markup, re.IGNORECASE,
        )
        if m:
            return html.unescape(m.group(1)).strip()
    return None


def fetch_posting(url: str, timeout: float = 20.0) -> dict:
    """Fetch a posting URL and guess title/company/text.

    Everything here is a guess shown to the user for correction — a job board's
    markup is not a stable contract.
    """
    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    try:
        resp = httpx.get(
            url, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; jobpilot/0.1; +local)"},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"The site returned {e.response.status_code}. Many boards block automated "
            "fetches — open the posting and paste its text instead."
        ) from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"Couldn't load that URL: {e}") from e

    markup = resp.text
    page_title = _meta(markup, "og:title", "twitter:title")
    if not page_title:
        m = re.search(r"<title[^>]*>(.*?)</title>", markup, re.IGNORECASE | re.DOTALL)
        page_title = html.unescape(m.group(1)).strip() if m else ""

    title, company = page_title, _meta(markup, "og:site_name") or ""
    # "Senior Writer - Acme" / "Senior Writer at Acme | Careers"
    split = re.split(r"\s+[-–—|]\s+|\s+\bat\b\s+", page_title, maxsplit=1)
    if len(split) == 2:
        title = split[0].strip()
        if not company:
            company = re.split(r"\s*[|–—-]\s*", split[1])[0].strip()

    text = html_to_text(markup)
    if len(text) < 200:
        raise RuntimeError(
            "That page had almost no readable text — it's probably rendered by "
            "JavaScript. Open the posting and paste its text instead."
        )
    return {"title": title[:200], "company": company[:120], "description": text[:20000],
            "url": str(resp.url)}
