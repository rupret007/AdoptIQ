"""Round 126 / Build 95 (G3) regression tests.

Two related fixes for the Comprehensive report's LLM rate-limit behaviour:

1. ``adoptiq_backend.CircuitChatClient.complete`` now honours a server-supplied
   ``Retry-After`` header (clamped to ``RATE_LIMIT_RETRY_AFTER_CAP_SECONDS``)
   on a 429 instead of always using the blind jittered-exponential window, and
   ``_r126_parse_retry_after`` extracts that header defensively.

2. ``app_simple._r126_pace_report_llm`` enforces a process-wide minimum spacing
   (``Config.REPORT_LLM_MIN_INTERVAL_SECONDS``) between consecutive *report* LLM
   calls so a 50+ customer Comprehensive run does not burst into the upstream
   429 limit. Default interval 0.0 is a byte-for-byte no-op.
"""
from __future__ import annotations

import datetime as _dt
import logging
import pathlib
from email.utils import format_datetime

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, headers):
        self.headers = headers


class _FakeExc(Exception):
    def __init__(self, headers=None):
        super().__init__("boom")
        if headers is not None:
            self.response = _FakeResp(headers)


def _make_client():
    from adoptiq_backend import CircuitChatClient

    return CircuitChatClient("cid", "secret", "appkey", model_name="m")


# ---------------------------------------------------------------------------
# _r126_parse_retry_after
# ---------------------------------------------------------------------------
def test_parse_retry_after_integer_seconds():
    from adoptiq_backend import _r126_parse_retry_after

    assert _r126_parse_retry_after(_FakeExc({"Retry-After": "10"})) == 10.0


def test_parse_retry_after_float_seconds():
    from adoptiq_backend import _r126_parse_retry_after

    assert _r126_parse_retry_after(_FakeExc({"Retry-After": "2.5"})) == 2.5


def test_parse_retry_after_lowercase_header():
    from adoptiq_backend import _r126_parse_retry_after

    assert _r126_parse_retry_after(_FakeExc({"retry-after": "7"})) == 7.0


def test_parse_retry_after_negative_is_none():
    from adoptiq_backend import _r126_parse_retry_after

    assert _r126_parse_retry_after(_FakeExc({"Retry-After": "-5"})) is None


def test_parse_retry_after_missing_header_is_none():
    from adoptiq_backend import _r126_parse_retry_after

    assert _r126_parse_retry_after(_FakeExc({"X-Other": "1"})) is None


def test_parse_retry_after_no_response_is_none():
    from adoptiq_backend import _r126_parse_retry_after

    assert _r126_parse_retry_after(_FakeExc(headers=None)) is None


def test_parse_retry_after_blank_is_none():
    from adoptiq_backend import _r126_parse_retry_after

    assert _r126_parse_retry_after(_FakeExc({"Retry-After": "   "})) is None


def test_parse_retry_after_http_date_future():
    from adoptiq_backend import _r126_parse_retry_after

    future = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=30)
    val = _r126_parse_retry_after(_FakeExc({"Retry-After": format_datetime(future)}))
    assert val is not None
    # Allow generous slack for clock granularity / HTTP-date second-rounding.
    assert 25.0 <= val <= 31.0


def test_parse_retry_after_garbage_is_none():
    from adoptiq_backend import _r126_parse_retry_after

    assert _r126_parse_retry_after(_FakeExc({"Retry-After": "not-a-date"})) is None


# ---------------------------------------------------------------------------
# CircuitChatClient.complete -- Retry-After honoring
# ---------------------------------------------------------------------------
def test_complete_honors_retry_after(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("ADOPTIQ_TEST_MODE", raising=False)
    import time as _time

    slept: list[float] = []
    monkeypatch.setattr(_time, "sleep", lambda s: slept.append(s))

    client = _make_client()
    calls = {"n": 0}

    def fake_once(_s, _u):
        calls["n"] += 1
        if calls["n"] < 3:
            client._last_retry_after_seconds = 12.0
            return "ERROR: llm.rate_limit_429: rate limited"
        return "body"

    monkeypatch.setattr(client, "_complete_once", fake_once)
    out = client.complete("s", "u")
    assert out == "body"
    assert calls["n"] == 3
    # Two retries, both sleeping the full server-requested cool-off (no jitter).
    assert slept == [12.0, 12.0]


def test_complete_clamps_retry_after_to_cap(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("ADOPTIQ_TEST_MODE", raising=False)
    import time as _time

    slept: list[float] = []
    monkeypatch.setattr(_time, "sleep", lambda s: slept.append(s))

    client = _make_client()
    cap = client.RATE_LIMIT_RETRY_AFTER_CAP_SECONDS
    calls = {"n": 0}

    def fake_once(_s, _u):
        calls["n"] += 1
        client._last_retry_after_seconds = cap + 1000.0
        return "ERROR: llm.rate_limit_429: rate limited"

    monkeypatch.setattr(client, "_complete_once", fake_once)
    out = client.complete("s", "u")
    assert out.startswith("ERROR: llm.rate_limit_429")
    # All retry sleeps clamped to the cap, never the hostile 1000s+ value.
    assert slept and all(s == cap for s in slept)


def test_complete_falls_back_to_jitter_without_retry_after(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("ADOPTIQ_TEST_MODE", raising=False)
    import time as _time

    slept: list[float] = []
    monkeypatch.setattr(_time, "sleep", lambda s: slept.append(s))

    client = _make_client()
    calls = {"n": 0}

    def fake_once(_s, _u):
        calls["n"] += 1
        # No Retry-After header -> _last_retry_after_seconds stays None.
        client._last_retry_after_seconds = None
        return "ERROR: llm.rate_limit_429: rate limited"

    monkeypatch.setattr(client, "_complete_once", fake_once)
    client.complete("s", "u")
    # Jittered backoff: attempt 1 window 0.5s, attempt 2 window 1.0s.
    assert len(slept) == 2
    assert 0.0 <= slept[0] <= client.RATE_LIMIT_RETRY_BASE_SECONDS
    assert 0.0 <= slept[1] <= client.RATE_LIMIT_RETRY_MAX_SECONDS


def test_complete_returns_body_immediately_on_success(monkeypatch):
    import time as _time

    slept: list[float] = []
    monkeypatch.setattr(_time, "sleep", lambda s: slept.append(s))
    client = _make_client()
    monkeypatch.setattr(client, "_complete_once", lambda _s, _u: "great answer")
    assert client.complete("s", "u") == "great answer"
    assert slept == []


# ---------------------------------------------------------------------------
# app_simple._r126_pace_report_llm
# ---------------------------------------------------------------------------
def test_pace_is_noop_when_interval_zero(monkeypatch):
    import app_simple
    from config import Config

    monkeypatch.setattr(Config, "REPORT_LLM_MIN_INTERVAL_SECONDS", 0.0, raising=False)
    app_simple._r126_report_llm_last_call_ts = 0.0
    called = {"sleep": 0}
    slept = app_simple._r126_pace_report_llm(
        sleep=lambda s: called.__setitem__("sleep", called["sleep"] + 1),
        now=lambda: 100.0,
    )
    assert slept == 0.0
    assert called["sleep"] == 0


def test_pace_min_interval_resolver_clamps_negative(monkeypatch):
    import app_simple
    from config import Config

    monkeypatch.setattr(Config, "REPORT_LLM_MIN_INTERVAL_SECONDS", -3.0, raising=False)
    assert app_simple._r126_report_llm_min_interval() == 0.0


def test_pace_spaces_consecutive_calls(monkeypatch):
    import app_simple
    from config import Config

    monkeypatch.setattr(Config, "REPORT_LLM_MIN_INTERVAL_SECONDS", 2.0, raising=False)
    app_simple._r126_report_llm_last_call_ts = 0.0

    clock = {"t": 1000.0}
    slept_log: list[float] = []

    def fake_sleep(s):
        slept_log.append(s)
        clock["t"] += s

    def fake_now():
        return clock["t"]

    # First call: no prior timestamp -> no sleep, stamps last_call_ts = 1000.0.
    s1 = app_simple._r126_pace_report_llm(sleep=fake_sleep, now=fake_now)
    assert s1 == 0.0
    assert slept_log == []

    # Simulate a fast loop iteration: only 0.5s elapsed before the next call.
    clock["t"] += 0.5
    s2 = app_simple._r126_pace_report_llm(sleep=fake_sleep, now=fake_now)
    assert abs(s2 - 1.5) < 1e-9
    assert slept_log == [1.5]


def test_pace_no_sleep_when_interval_already_elapsed(monkeypatch):
    import app_simple
    from config import Config

    monkeypatch.setattr(Config, "REPORT_LLM_MIN_INTERVAL_SECONDS", 2.0, raising=False)
    app_simple._r126_report_llm_last_call_ts = 0.0

    clock = {"t": 500.0}
    slept_log: list[float] = []

    def fake_sleep(s):
        slept_log.append(s)
        clock["t"] += s

    def fake_now():
        return clock["t"]

    app_simple._r126_pace_report_llm(sleep=fake_sleep, now=fake_now)  # stamp 500.0
    clock["t"] += 5.0  # plenty more than the 2.0s interval
    s2 = app_simple._r126_pace_report_llm(sleep=fake_sleep, now=fake_now)
    assert s2 == 0.0
    assert slept_log == []


def test_pace_skipped_under_pytest_without_injection(monkeypatch):
    import app_simple
    from config import Config

    monkeypatch.setattr(Config, "REPORT_LLM_MIN_INTERVAL_SECONDS", 5.0, raising=False)
    app_simple._r126_report_llm_last_call_ts = 0.0
    # PYTEST_CURRENT_TEST is set by pytest; with no injected sleep/now the pacer
    # short-circuits so the suite never blocks on a real wall-clock wait.
    assert app_simple._r126_pace_report_llm() == 0.0


# ---------------------------------------------------------------------------
# Source-shape markers
# ---------------------------------------------------------------------------
def test_g3_markers_present():
    cfg = REPO_ROOT.joinpath("config.py").read_text(encoding="utf-8")
    backend = REPO_ROOT.joinpath("adoptiq_backend.py").read_text(encoding="utf-8")
    app = REPO_ROOT.joinpath("app_simple.py").read_text(encoding="utf-8")

    assert "REPORT_LLM_MIN_INTERVAL_SECONDS" in cfg
    assert "Round 126 / Build 95 (G3)" in cfg

    assert "_r126_parse_retry_after" in backend
    assert "RATE_LIMIT_RETRY_AFTER_CAP_SECONDS" in backend
    assert "_last_retry_after_seconds" in backend

    assert "_r126_pace_report_llm" in app
    assert "_r126_report_llm_min_interval" in app
    # Wired into both report call sites.
    assert app.count("_r126_pace_report_llm()") >= 2
