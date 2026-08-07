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


# ---------------------------------------------------------- spreadsheets ---

def _xlsx_rows(data: bytes) -> list[list[str]]:
    """Read the first sheet of an .xlsx with the standard library."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
            shared = [html.unescape(re.sub(r"<[^>]+>", "", si))
                      for si in re.findall(r"<si>(.*?)</si>", xml, re.DOTALL)]
        names = [n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n)]
        if not names:
            return []
        sheet = z.read(sorted(names)[0]).decode("utf-8", "ignore")

    rows: list[list[str]] = []
    for row_xml in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.DOTALL):
        cells: list[str] = []
        for cell in re.findall(r"<c\b([^>]*)>(.*?)</c>", row_xml, re.DOTALL):
            attrs, body = cell
            value = ""
            inline = re.search(r"<is>.*?</is>", body, re.DOTALL)
            v = re.search(r"<v>(.*?)</v>", body, re.DOTALL)
            if inline:
                value = html.unescape(re.sub(r"<[^>]+>", "", inline.group(0)))
            elif v:
                raw = html.unescape(v.group(1))
                if 't="s"' in attrs:
                    idx = int(raw) if raw.isdigit() else -1
                    value = shared[idx] if 0 <= idx < len(shared) else ""
                else:
                    value = raw
            cells.append(value.strip())
        if any(cells):
            rows.append(cells)
    return rows


def spreadsheet_rows(filename: str, data: bytes) -> list[list[str]]:
    """Rows from a .csv or .xlsx upload."""
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        return _xlsx_rows(data)
    if name.endswith((".csv", ".tsv", ".txt")):
        import csv as _csv

        text = data.decode("utf-8-sig", "ignore")
        delim = "\t" if name.endswith(".tsv") or "\t" in text.split("\n")[0] else ","
        return [[c.strip() for c in row] for row in _csv.reader(io.StringIO(text), delimiter=delim)
                if any(c.strip() for c in row)]
    raise RuntimeError(f"Unsupported file: {filename}. Use .csv or .xlsx.")


# ------------------------------------------------------- interview loops ---

# "Cross-Functional Collaboration w/ Dana Lee - Manager, Support"
# "Interview with Sam Ortiz, Director of Support"
# "Sam Ortiz (Director of Support)"
_NAME = r"[A-Z][\w.'’-]+(?:\s+[A-Z][\w.'’-]+){1,2}"
_PATTERNS = [
    re.compile(rf"(?P<focus>[^\n|]+?)\s+(?:w/|with)\s+(?P<name>{_NAME})\s*[-–—:,]\s*(?P<role>[^\n|]+)", re.I),
    re.compile(rf"(?:w/|with)\s+(?P<name>{_NAME})\s*[-–—:,]\s*(?P<role>[^\n|]+)", re.I),
    re.compile(rf"(?P<name>{_NAME})\s*\(\s*(?P<role>[^)\n]+)\)"),
    re.compile(rf"(?P<name>{_NAME})\s*[-–—]\s*(?P<role>[A-Z][^\n|]{{3,60}})"),
]

_LINKEDIN = re.compile(r"https?://(?:[\w-]+\.)?linkedin\.com/in/[\w%-]+", re.I)


def parse_interviewers(text: str) -> list[dict]:
    """Best-effort extraction of a loop from a pasted recruiter email.

    Heuristics only, so it costs nothing and works without an API key. Whatever
    it finds is shown for correction — it is never used as-is.
    """
    found: list[dict] = []
    seen: set[str] = set()
    links = _LINKEDIN.findall(text or "")

    for raw_line in (text or "").splitlines():
        line = raw_line.strip(" -•\t")
        if not line or len(line) > 240:
            continue
        for pattern in _PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            name = " ".join(m.group("name").split())
            if name.lower() in seen:
                break
            role = m.groupdict().get("role", "").strip(" .;,")
            focus = (m.groupdict().get("focus") or "").strip(" .;,-–—")
            # Guard against sentences that merely start with two capitalised words
            if len(name.split()) > 3 or any(w in name.lower() for w in ("the", "and", "your")):
                break
            seen.add(name.lower())
            found.append({"name": name, "role": role[:120], "focus": focus[:160],
                          "linkedin": "", "when": ""})
            break

    for i, person in enumerate(found):
        if i < len(links):
            person["linkedin"] = links[i]
    for i, person in enumerate(found, start=1):
        person["when"] = f"Interview {i} of {len(found)}" if len(found) > 1 else "Interview"
    return found


def parse_competencies(text: str) -> list[str]:
    """Bullet lines from the 'we're looking for evidence that you can…' section."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not re.match(r"^[-•*•]\s+|^\d+[.)]\s+", line):
            continue
        line = re.sub(r"^[-•*•]\s+|^\d+[.)]\s+", "", line).strip(" .")
        if 12 <= len(line) <= 180:
            out.append(line)
    return out[:12]
