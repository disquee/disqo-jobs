"""The two backends that aren't the Anthropic SDK: Claude Code, and a local server.

No subprocess is spawned and no socket is opened — the point is the parsing and
the error wording, which is what a stuck user actually sees.
"""

import json

import httpx
import pytest

import jobpilot.llm as llm


class _Settings:
    def __init__(self, **kw):
        self.model = kw.get("model")
        self.local_base_url = kw.get("local_base_url", "")
        self.local_model = kw.get("local_model", "")


@pytest.fixture
def cli_mode(monkeypatch):
    monkeypatch.setattr(llm, "_mode_and_settings", lambda: ("cli", _Settings()))
    monkeypatch.setattr(llm.shutil, "which", lambda _: "/usr/local/bin/claude")


@pytest.fixture
def local_mode(monkeypatch):
    monkeypatch.setattr(
        llm, "_mode_and_settings",
        lambda: ("local", _Settings(local_model="llama3.1:8b")),
    )


def _proc(stdout="", stderr="", code=0):
    class P:
        returncode = code
    P.stdout, P.stderr = stdout, stderr
    return P


# ---- Claude Code ------------------------------------------------------

def test_cli_returns_the_result_field(cli_mode, monkeypatch):
    envelope = {"is_error": False, "result": "hello",
                "usage": {"input_tokens": 5, "output_tokens": 2}}
    monkeypatch.setattr(llm.subprocess, "run",
                        lambda *a, **k: _proc(stdout=json.dumps(envelope)))
    assert llm.complete("hi") == "hello"


def test_cli_runs_with_tools_disabled(cli_mode, monkeypatch):
    """A job posting is untrusted text; the agent must not be able to act on it."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        return _proc(stdout=json.dumps({"result": "ok"}))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm.complete("hi", system="be brief")

    assert "--allowed-tools" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--allowed-tools") + 1] == ""
    assert "--append-system-prompt" in seen["cmd"]
    # An empty cwd, so no CLAUDE.md from wherever the app was started leaks in.
    assert seen["cwd"] and seen["cwd"] != "."


def test_cli_missing_binary_says_so(monkeypatch):
    monkeypatch.setattr(llm, "_mode_and_settings", lambda: ("cli", _Settings()))
    monkeypatch.setattr(llm.shutil, "which", lambda _: None)
    monkeypatch.setattr(llm.os.path, "exists", lambda _: False)
    with pytest.raises(RuntimeError, match="Claude Code not found"):
        llm.complete("hi")


def test_cli_surfaces_an_error_envelope(cli_mode, monkeypatch):
    envelope = {"is_error": True, "result": "not logged in"}
    monkeypatch.setattr(llm.subprocess, "run",
                        lambda *a, **k: _proc(stdout=json.dumps(envelope)))
    with pytest.raises(RuntimeError, match="not logged in"):
        llm.complete("hi")


def test_cli_nonzero_exit_is_reported(cli_mode, monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run",
                        lambda *a, **k: _proc(stderr="boom", code=1))
    with pytest.raises(RuntimeError, match="boom"):
        llm.complete("hi")


# ---- a local OpenAI-compatible server ---------------------------------

def _reply(content, finish="stop", status=200):
    payload = {"choices": [{"message": {"content": content}, "finish_reason": finish}],
               "usage": {"prompt_tokens": 3, "completion_tokens": 4}}
    return httpx.Response(status, json=payload,
                          request=httpx.Request("POST", "http://x/chat/completions"))


def test_local_returns_the_message(local_mode, monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _reply("hello"))
    assert llm.complete("hi") == "hello"


def test_local_strips_inline_reasoning(local_mode, monkeypatch):
    """llama.cpp and LM Studio leave the chain of thought in the content."""
    monkeypatch.setattr(llm.httpx, "post",
                        lambda *a, **k: _reply("<think>hmm</think>Dear hiring manager,"))
    assert llm.complete("hi") == "Dear hiring manager,"


def test_local_empty_after_thinking_explains_why(local_mode, monkeypatch):
    """A reasoning model can spend the whole budget thinking and answer nothing."""
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _reply("", finish="length"))
    with pytest.raises(RuntimeError, match="whole token budget thinking"):
        llm.complete("hi")


def test_local_gives_reasoning_models_headroom(local_mode, monkeypatch):
    seen = {}

    def fake_post(url, json=None, **k):
        seen.update(json)
        return _reply("ok")

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    llm.complete("hi", max_tokens=16)
    assert seen["max_tokens"] >= llm._LOCAL_MIN_TOKENS


def test_local_unreachable_names_the_address(local_mode, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(llm.httpx, "post", boom)
    with pytest.raises(RuntimeError, match="is the server running"):
        llm.complete("hi")


def test_local_without_a_model_asks_for_one(monkeypatch):
    monkeypatch.setattr(llm, "_mode_and_settings", lambda: ("local", _Settings()))
    with pytest.raises(RuntimeError, match="No local model chosen"):
        llm.complete("hi")
