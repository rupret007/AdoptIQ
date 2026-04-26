"""Round 3 / Phase 2.5 regression test.

When the underlying ``chat.completions.create`` call returns a choice
with ``finish_reason == 'length'``, the answer was cut short by
max_tokens. ``CircuitChatClient.complete`` must:

1. log a WARNING containing the substring "LLM truncated"
2. append a user-visible truncation marker to the returned content
   so downstream renderers cannot ship a length-truncated answer as
   if it were complete.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class _FakeChoice:
    def __init__(self, content: str, finish_reason: str):
        self.message = SimpleNamespace(content=content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content: str, finish_reason: str):
        self.choices = [_FakeChoice(content, finish_reason)]


class _FakeCompletions:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeChat:
    def __init__(self, response):
        self.completions = _FakeCompletions(response)


class _FakeAzureClient:
    def __init__(self, response):
        self.chat = _FakeChat(response)


def _patch_client(monkeypatch, response):
    import adoptiq_backend as ab

    def _factory(*args, **kwargs):
        return _FakeAzureClient(response)

    monkeypatch.setattr(ab, "AzureOpenAI", _factory)


def test_finish_reason_length_appends_truncation_marker(monkeypatch, caplog):
    import adoptiq_backend as ab

    fake_response = _FakeResponse(
        '{"executive_summary": "Acme Corp is doing',
        finish_reason="length",
    )
    _patch_client(monkeypatch, fake_response)

    client = ab.CircuitChatClient(
        client_id="cid",
        client_secret="csec",
        app_key="appkey",
        model_name="gpt-5-nano",
    )
    monkeypatch.setattr(client, "_get_token", lambda: "fake-token")

    caplog.set_level(logging.WARNING, logger="adoptiq_backend")
    out = client.complete("sys", "user")

    assert isinstance(out, str)
    assert "[TRUNCATED" in out, (
        "length-truncated response must carry a visible TRUNCATED marker"
    )
    assert any(
        "LLM truncated" in rec.message for rec in caplog.records
    ), "expected a WARN log mentioning 'LLM truncated'"


def test_finish_reason_stop_does_not_modify_content(monkeypatch, caplog):
    import adoptiq_backend as ab

    fake_response = _FakeResponse(
        '{"executive_summary": "Acme Corp is healthy."}',
        finish_reason="stop",
    )
    _patch_client(monkeypatch, fake_response)

    client = ab.CircuitChatClient(
        client_id="cid",
        client_secret="csec",
        app_key="appkey",
        model_name="gpt-5-nano",
    )
    monkeypatch.setattr(client, "_get_token", lambda: "fake-token")

    caplog.set_level(logging.WARNING, logger="adoptiq_backend")
    out = client.complete("sys", "user")
    assert out == '{"executive_summary": "Acme Corp is healthy."}'
    assert "[TRUNCATED" not in (out or "")
    assert not any(
        "LLM truncated" in rec.message for rec in caplog.records
    )
