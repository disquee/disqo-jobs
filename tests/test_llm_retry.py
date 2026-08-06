"""Retry/backoff around the Anthropic call (no network, no real client)."""

import httpx
import pytest
from anthropic import APIConnectionError, BadRequestError, OverloadedError

import jobpilot.llm as llm


class _Block:
    type = "text"
    text = "ok"


class _Msg:
    content = [_Block()]


class _FakeClient:
    """Stand-in Anthropic client that fails `fail_n` times before succeeding."""

    def __init__(self, exc, fail_n):
        self._exc = exc
        self._fail_n = fail_n
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_n:
            raise self._exc
        return _Msg()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)


def _conn_error():
    return APIConnectionError(message="boom", request=httpx.Request("POST", "https://x"))


def test_retries_then_succeeds(monkeypatch):
    fake = _FakeClient(_conn_error(), fail_n=2)
    monkeypatch.setattr(llm, "_client", lambda: fake)
    assert llm.complete("hi") == "ok"
    assert fake.calls == 3  # 2 failures + 1 success


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(llm, "_MAX_RETRIES", 3)
    fake = _FakeClient(_conn_error(), fail_n=99)
    monkeypatch.setattr(llm, "_client", lambda: fake)
    with pytest.raises(APIConnectionError):
        llm.complete("hi")
    assert fake.calls == 4  # initial + 3 retries


def test_overloaded_is_retried(monkeypatch):
    resp = httpx.Response(529, request=httpx.Request("POST", "https://x"))
    exc = OverloadedError(message="overloaded", response=resp, body=None)
    fake = _FakeClient(exc, fail_n=1)
    monkeypatch.setattr(llm, "_client", lambda: fake)
    assert llm.complete("hi") == "ok"
    assert fake.calls == 2


def test_non_transient_fails_fast(monkeypatch):
    resp = httpx.Response(400, request=httpx.Request("POST", "https://x"))
    exc = BadRequestError(message="bad", response=resp, body=None)
    fake = _FakeClient(exc, fail_n=99)
    monkeypatch.setattr(llm, "_client", lambda: fake)
    with pytest.raises(BadRequestError):
        llm.complete("hi")
    assert fake.calls == 1  # not retried


def test_retry_delay_honors_retry_after(monkeypatch):
    resp = httpx.Response(429, headers={"retry-after": "7"},
                          request=httpx.Request("POST", "https://x"))
    exc = llm.RateLimitError(message="rl", response=resp, body=None)
    assert llm._retry_delay(exc, attempt=0) == 7.0
