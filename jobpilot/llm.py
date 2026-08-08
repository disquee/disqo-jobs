"""Where a prompt actually goes, plus a JSON-extraction helper.

Three automatic backends behind one ``complete()``:

  api    -- the Anthropic SDK, with a key
  cli    -- the Claude Code binary, for people who have a subscription but no key
  local  -- any OpenAI-compatible server: Ollama, LM Studio, llama.cpp, vLLM

A fourth mode, ``manual``, never reaches this module — it renders the prompt for
the user to paste somewhere themselves.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from typing import Any

import httpx
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


DEFAULT_SYSTEM = "You are a precise, helpful assistant."

#: Ollama's default port. LM Studio uses 1234, llama.cpp 8080 — all the same API.
LOCAL_BASE_URL_DEFAULT = "http://localhost:11434/v1"

#: Where the Claude Code binary lives, if it isn't on PATH.
CLAUDE_BIN = os.getenv("JOBPILOT_CLAUDE_BIN", "claude")
#: Long enough for a slow local model on CPU; short enough to fail eventually.
_CLI_TIMEOUT = float(os.getenv("JOBPILOT_CLI_TIMEOUT", "300"))
_LOCAL_TIMEOUT = float(os.getenv("JOBPILOT_LOCAL_TIMEOUT", "600"))
#: Floor for a local request, so a reasoning model has room to think *and* answer.
_LOCAL_MIN_TOKENS = int(os.getenv("JOBPILOT_LOCAL_MIN_TOKENS", "1024"))


def _record(input_tokens: int, output_tokens: int) -> None:
    """Bookkeeping must never break a real request."""
    try:
        from .usage import record

        record(input_tokens or 0, output_tokens or 0)
    except Exception:
        pass


def _mode_and_settings():
    """Imported late: settings imports llm, so a module-level import would cycle."""
    try:
        from .settings import load_settings

        s = load_settings()
        return s.llm_mode, s
    except Exception:
        return "api", None


def complete(prompt: str, system: str = "", max_tokens: int = 2000,
            model: str | None = None) -> str:
    """Single-turn completion returning the assistant text."""
    mode, settings = _mode_and_settings()
    if mode == "cli":
        return _complete_cli(prompt, system, model, settings)
    if mode == "local":
        return _complete_local(prompt, system, max_tokens, model, settings)
    return _complete_api(prompt, system, max_tokens, model)


def _complete_api(prompt: str, system: str, max_tokens: int, model: str | None) -> str:
    msg = _create_with_retry(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system or DEFAULT_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = getattr(msg, "usage", None)  # absent on some stubs, and on old SDKs
    _record(getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0))
    return "".join(block.text for block in msg.content if block.type == "text")


def _complete_cli(prompt: str, system: str, model: str | None, settings: Any) -> str:
    """Run the prompt through the Claude Code binary.

    Tools are switched off. A job posting is untrusted text the app already
    sanitizes, and handing it to an agent that can read and write files is a
    different risk from handing it to an endpoint that can only reply.

    Runs in an empty directory so no CLAUDE.md or project config from wherever
    the app happens to be started leaks into the prompt.
    """
    if not shutil.which(CLAUDE_BIN) and not os.path.exists(CLAUDE_BIN):
        raise RuntimeError(
            f"Claude Code not found (looked for {CLAUDE_BIN!r}). Install it, or set "
            "JOBPILOT_CLAUDE_BIN to its full path, or switch to another AI mode in Settings."
        )
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json",
           "--max-turns", "1", "--allowed-tools", ""]
    if system:
        cmd += ["--append-system-prompt", system]
    chosen = model or (getattr(settings, "model", None) or "")
    if chosen:
        cmd += ["--model", chosen]

    with tempfile.TemporaryDirectory() as empty:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=_CLI_TIMEOUT, cwd=empty)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Claude Code didn't answer within {int(_CLI_TIMEOUT)}s."
            ) from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise RuntimeError(f"Claude Code failed: {detail or 'no output'}")

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Claude Code returned something unexpected: {proc.stdout[:200]!r}"
        ) from None
    if envelope.get("is_error"):
        raise RuntimeError(f"Claude Code reported an error: {envelope.get('result', '')[:300]}")

    usage = envelope.get("usage") or {}
    _record(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return envelope.get("result", "") or ""


def _complete_local(prompt: str, system: str, max_tokens: int,
                    model: str | None, settings: Any) -> str:
    """Call a local OpenAI-compatible server.

    One code path covers Ollama, LM Studio, llama.cpp and vLLM, because they all
    speak /chat/completions. Nothing leaves the machine.
    """
    base = (getattr(settings, "local_base_url", "") or LOCAL_BASE_URL_DEFAULT).rstrip("/")
    name = model or (getattr(settings, "local_model", "") or "")
    if not name:
        raise RuntimeError(
            "No local model chosen. Set one in Settings — the name your server "
            "reports, like 'llama3.1:8b'."
        )
    payload = {
        "model": name,
        "messages": [{"role": "system", "content": system or DEFAULT_SYSTEM},
                     {"role": "user", "content": prompt}],
        # A reasoning model spends this budget thinking before it writes a word,
        # so a tight limit comes back as an empty answer rather than a short one.
        "max_tokens": max(max_tokens, _LOCAL_MIN_TOKENS),
        "stream": False,
    }
    try:
        resp = httpx.post(f"{base}/chat/completions", json=payload, timeout=_LOCAL_TIMEOUT)
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Couldn't reach the local model at {base} — is the server running? ({exc})"
        ) from None
    if resp.status_code >= 400:
        raise RuntimeError(f"Local model returned {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    usage = data.get("usage") or {}
    _record(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    try:
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"Local model sent no message: {str(data)[:200]}") from None

    text = _strip_reasoning(text)
    if not text.strip():
        if choice.get("finish_reason") == "length":
            raise RuntimeError(
                f"{name} used its whole token budget thinking and never answered. "
                "Raise the limit, or pick a model that doesn't reason out loud."
            )
        raise RuntimeError(f"{name} returned an empty answer.")
    return text


#: llama.cpp and LM Studio leave the chain of thought inline; Ollama returns it
#: in a separate field. Either way it is working-out, not an answer, and it must
#: never reach a cover letter.
_THINK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    text = _THINK.sub("", text)
    # An unclosed opener means the budget ran out mid-thought; nothing after it
    # is an answer either.
    opener = re.search(r"<(?:think|thinking|reasoning)>", text, re.IGNORECASE)
    if opener:
        text = text[: opener.start()]
    return text.strip()


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
