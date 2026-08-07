"""Thin wrapper around the Anthropic SDK with a JSON-extraction helper."""

from __future__ import annotations

import json
import os
import random
import re
import time
from functools import lru_cache
from typing import Any

from anthropic import (
    Anthropic,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OverloadedError,
    RateLimitError,
)

from .config import DEFAULT_MODEL, require_anthropic_key

# Transient failures worth retrying: connection blips, timeouts, 429 rate limits,
# 5xx server errors, and 529 "overloaded". Auth/bad-request errors are NOT here,
# so they fail fast instead of looping.
_TRANSIENT = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
    OverloadedError,
)
_MAX_RETRIES = int(os.getenv("JOBPILOT_MAX_RETRIES", "5"))
_BASE_DELAY = float(os.getenv("JOBPILOT_RETRY_BASE_DELAY", "1.0"))  # seconds
_MAX_DELAY = 60.0


@lru_cache(maxsize=1)
def _client() -> Anthropic:
    # max_retries=0: retries are managed by _create_with_retry below so backoff
    # behavior is explicit and testable (avoids double-retrying with the SDK).
    return Anthropic(api_key=require_anthropic_key(), max_retries=0)


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Backoff for the given attempt (0-based). Honors server Retry-After."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_DELAY)
            except ValueError:
                pass
    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
    return delay + random.uniform(0, delay * 0.25)  # full-ish jitter


def _create_with_retry(**kwargs: Any):
    """Call messages.create, retrying transient errors with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return _client().messages.create(**kwargs)
        except _TRANSIENT as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES:
                break
            time.sleep(_retry_delay(exc, attempt))
    assert last_exc is not None
    raise last_exc


def complete(prompt: str, system: str = "", max_tokens: int = 2000,
            model: str | None = None) -> str:
    """Single-turn completion returning the assistant text."""
    msg = _create_with_retry(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system or "You are a precise, helpful assistant.",
        messages=[{"role": "user", "content": prompt}],
    )
    try:  # bookkeeping must never break a real request
        from .usage import record

        record(getattr(msg.usage, "input_tokens", 0), getattr(msg.usage, "output_tokens", 0))
    except Exception:
        pass
    return "".join(block.text for block in msg.content if block.type == "text")


def complete_json(prompt: str, system: str = "", max_tokens: int = 2000) -> Any:
    """Completion that must return JSON. Tolerates code fences / stray prose."""
    raw = complete(
        prompt,
        system=(system or "Respond with valid JSON only, no prose, no code fences."),
        max_tokens=max_tokens,
    )
    return _extract_json(raw)


def sanitize_untrusted(text: str, limit: int = 6000) -> str:
    """Neutralize untrusted text before embedding it in a prompt.

    Strips delimiter-like tags so a malicious posting can't close the
    <job_posting> wrapper and inject instructions, and truncates to ``limit``.
    """
    # Remove anything that looks like our delimiter tags (any casing/spacing).
    text = re.sub(r"<\s*/?\s*job_posting\s*>", "", text, flags=re.IGNORECASE)
    return text[:limit]


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: grab the outermost {...} or [...]
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise
