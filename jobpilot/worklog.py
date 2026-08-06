"""Work-search log export for unemployment reporting.

Most state agencies want the same handful of columns and accept a spreadsheet or
a printout. This module owns that column spec and renders it to CSV and XLSX.

The XLSX writer is deliberately hand-rolled on the standard library: a workbook
is just a zip of XML, and adding a dependency to emit seven columns isn't worth
it for people who may be installing this on a locked-down machine.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta
from xml.sax.saxutils import escape

from .models import WorkSearchActivity

# (header, attribute) in the order agencies ask for them.
COLUMNS: list[tuple[str, str]] = [
    ("Date Applied / Contacted", "date"),
    ("Company / Employer Name", "company"),
    ("Position Title", "position"),
    ("Contact Information", "contact"),
    ("Type of Work Search", "activity_type"),
    ("Result / Status", "result"),
    ("Notes", "notes"),
]


def _cell(activity: WorkSearchActivity, attr: str) -> str:
    value = getattr(activity, attr, "")
    return getattr(value, "value", value) or ""


def rows(activities: list[WorkSearchActivity]) -> list[list[str]]:
    """Header row followed by one row per activity, oldest first.

    Oldest-first because a log submitted to an agency reads chronologically,
    even though the UI shows newest first.
    """
    ordered = sorted(activities, key=lambda a: (a.date, a.created_at))
    return [[h for h, _ in COLUMNS]] + [
        [_cell(a, attr) for _, attr in COLUMNS] for a in ordered
    ]


def to_csv(activities: list[WorkSearchActivity]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerows(rows(activities))
    return buf.getvalue()


# ------------------------------------------------------------------ xlsx ---

def _col_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Work search log" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


def to_xlsx(activities: list[WorkSearchActivity]) -> bytes:
    """A minimal but valid .xlsx. Values are inline strings, so no shared-string
    table is needed and every cell survives round-tripping as text."""
    data = rows(activities)
    body: list[str] = []
    for r_idx, row in enumerate(data, start=1):
        cells = []
        for c_idx, value in enumerate(row):
            ref = f"{_col_name(c_idx)}{r_idx}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                f"{escape(str(value))}</t></is></c>"
            )
        body.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    widths = "".join(
        f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
        for i, w in enumerate([22, 26, 28, 30, 26, 16, 44])
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<cols>{widths}</cols>"
        f'<sheetData>{"".join(body)}</sheetData>'
        "</worksheet>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", _WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


# ----------------------------------------------------------------- weeks ---

def week_start(iso_date: str) -> str:
    """Sunday of that date's week — the boundary most state claim weeks use."""
    d = date.fromisoformat(iso_date)
    return (d - timedelta(days=(d.weekday() + 1) % 7)).isoformat()


def weekly_counts(activities: list[WorkSearchActivity]) -> list[tuple[str, int]]:
    """(week starting, count), newest first. Agencies require a weekly minimum."""
    tally: dict[str, int] = {}
    for a in activities:
        try:
            key = week_start(a.date)
        except ValueError:
            continue
        tally[key] = tally.get(key, 0) + 1
    return sorted(tally.items(), reverse=True)
