"""Source abstraction + small HTML->text helper."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from html import unescape

from ..models import Job


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
