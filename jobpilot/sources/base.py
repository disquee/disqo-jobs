"""Source abstraction + small HTML->text helper."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from html import unescape

from ..models import Job


# Aggregators sometimes send a navigation label where a company should be.
# These names would end up on a cover letter, so better an honest "Unknown".
_JUNK_COMPANY = {
    "search our", "search our jobs", "careers", "jobs", "hiring",
    "apply now", "confidential", "n/a", "unknown",
}


def clean_company(name: str) -> str:
    """Repair aggregator damage to a company name.

    Adzuna strips ", Inc" from names it serves, which turns
    "American Institute of Physics, Incorporated" into
    "American Institute of Physicsorporated" — and that string flows into
    cover letters and filenames, where a mangled employer name can sink a
    real application. Undo the known artifact, and replace obvious
    non-names with "Unknown" so a human notices and fixes it in review.
    """
    name = (name or "").strip()
    # "…Xorporated" where X isn't the C of a real "Corporated"/"Incorporated":
    # the leftover tail of "Incorporated" after the ", Inc" strip. Drop it.
    if re.search(r"(?i)(?<![ci])orporated$", name):
        name = name[: -len("orporated")].rstrip(" ,.-")
    if name.lower() in _JUNK_COMPANY or not name:
        return "Unknown"
    return name


def html_to_text(html: str) -> str:
    """Cheap, dependency-free HTML -> plain text for posting descriptions."""
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class JobSource(ABC):
    """A source of job postings. Implementations must be ToS-compliant."""

    name: str = "base"

    @abstractmethod
    def search(self, query: str, location: str, limit: int) -> list[Job]:
        """Return normalized jobs. Should never raise on empty results."""
        raise NotImplementedError

    def available(self) -> bool:
        """Whether this source is configured (e.g. has API keys)."""
        return True
