"""Round 64 / Phase 3 (B3) regression: portfolio LLM retry + honest fallback.

Pre-Round-64, the comprehensive report's portfolio Overview LLM call sat
inside a single ``try/except`` block in ``app_simple.run_comprehensive_analysis``
that classified every failure -- transient timeout, empty body, an
upstream 503, or a genuine prompt regression -- the same way: log a
warning, drop the bland ``"AI analysis encountered an error.  Data
processed successfully."`` paragraph, and move on.  Build 36 manual
acceptance produced exactly that outcome on the Brian Frazier /
Contact Center comprehensive report (P011 of the
``QUALITY_AUDIT.md`` Round 64 handoff): the LLM call returned an
empty body once, never retried, and the operator was left guessing
whether the problem was on the AdoptIQ side, the CircuIT side, or
the prompt itself.

This test pins the Round 64 fix:

1.  ``_r64_call_llm_with_retry`` retries up to N attempts on transient
    failures (empty body, ``ERROR: timeout``, ``ERROR: rate_limited``,
    ``ERROR: server_error``, ``ERROR: network``, raised exception) with
    exponential backoff between attempts.
2.  The helper does NOT retry on non-transient ERRORs
    (``ERROR: content_filter``, ``ERROR: credentials``, ``ERROR: auth``)
    -- a second attempt would just burn budget without changing the
    verdict.
3.  When the helper exhausts its retry budget, it returns a populated
    ``diag`` dict so the caller can render an honest fallback paragraph
    that names the failure mode (LLM call) and the last error kind
    instead of the bland legacy line.

The retry helper is a small library function so the test exercises it
directly with a stub LLM callable; the report-level integration is
pinned indirectly via the diag-dict shape contract.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def app_simple_module():
    """Import the production module once per module invocation."""
    return importlib.import_module("app_simple")


def _no_sleep(_seconds: float) -> None:
    """Inject a no-op sleep so the test suite never blocks on a real wait."""


# ---------------------------------------------------------------------------
# _r64_classify_llm_result -- the discriminator that drives retry vs accept.
# ---------------------------------------------------------------------------

def test_classify_ok_for_non_empty_non_error_body(app_simple_module) -> None:
    assert app_simple_module._r64_classify_llm_result("Real LLM body.") == "ok"


def test_classify_empty_for_none_or_blank(app_simple_module) -> None:
    assert app_simple_module._r64_classify_llm_result(None) == "empty"
    assert app_simple_module._r64_classify_llm_result("") == "empty"


def test_classify_transient_for_known_kinds(app_simple_module) -> None:
    for kind in ("timeout", "rate_limited", "server_error", "network", "llm.timeout"):
        body = f"ERROR: {kind}: upstream message"
        assert app_simple_module._r64_classify_llm_result(body) == "transient", (
            f"Expected transient classification for {body!r}"
        )


def test_classify_hard_for_non_transient_kinds(app_simple_module) -> None:
    for kind in ("content_filter", "credentials", "auth", "unsupported"):
        body = f"ERROR: {kind}: detail"
        assert app_simple_module._r64_classify_llm_result(body) == "hard", (
            f"Expected hard classification for {body!r}"
        )


# ---------------------------------------------------------------------------
# _r64_call_llm_with_retry -- the contract we promise to the report path.
# ---------------------------------------------------------------------------

def test_retry_helper_succeeds_on_first_attempt(app_simple_module) -> None:
    calls: list[int] = []

    def fake_llm(_prompt: str, _briefing: str) -> str:
        calls.append(1)
        return "Healthy portfolio narrative."

    result, diag = app_simple_module._r64_call_llm_with_retry(
        fake_llm,
        "prompt",
        "briefing",
        max_attempts=3,
        backoff_base_seconds=0.0,
        sleep=_no_sleep,
    )

    assert result == "Healthy portfolio narrative."
    assert len(calls) == 1, "First-attempt success must not retry"
    assert diag["attempts"] == 1
    assert diag["final_outcome"] == "ok"
    assert diag["last_error_kind"] is None


def test_retry_helper_recovers_on_second_attempt(app_simple_module) -> None:
    bodies = iter(["ERROR: timeout: upstream", "Recovered narrative."])
    calls: list[int] = []

    def fake_llm(_prompt: str, _briefing: str) -> str:
        calls.append(1)
        return next(bodies)

    result, diag = app_simple_module._r64_call_llm_with_retry(
        fake_llm,
        "prompt",
        "briefing",
        max_attempts=3,
        backoff_base_seconds=0.0,
        sleep=_no_sleep,
    )

    assert result == "Recovered narrative."
    assert len(calls) == 2
    assert diag["attempts"] == 2
    assert diag["final_outcome"] == "ok"


def test_retry_helper_recovers_on_third_attempt_after_two_transients(app_simple_module) -> None:
    bodies = iter([
        "",
        "ERROR: rate_limited: 429",
        "Final-attempt success.",
    ])
    calls: list[int] = []

    def fake_llm(_prompt: str, _briefing: str) -> str:
        calls.append(1)
        return next(bodies)

    result, diag = app_simple_module._r64_call_llm_with_retry(
        fake_llm,
        "prompt",
        "briefing",
        max_attempts=3,
        backoff_base_seconds=0.0,
        sleep=_no_sleep,
    )

    assert result == "Final-attempt success."
    assert len(calls) == 3
    assert diag["attempts"] == 3
    assert diag["final_outcome"] == "ok"


def test_retry_helper_exhausts_retries_on_persistent_transient(app_simple_module) -> None:
    calls: list[int] = []

    def always_timeout(_prompt: str, _briefing: str) -> str:
        calls.append(1)
        return "ERROR: timeout: upstream timed out"

    result, diag = app_simple_module._r64_call_llm_with_retry(
        always_timeout,
        "prompt",
        "briefing",
        max_attempts=3,
        backoff_base_seconds=0.0,
        sleep=_no_sleep,
    )

    assert result.startswith("ERROR:")
    assert len(calls) == 3, "All three attempts should have run"
    assert diag["attempts"] == 3
    assert diag["final_outcome"] == "transient"
    assert diag["last_error_kind"] == "timeout"


def test_retry_helper_does_not_retry_on_hard_error(app_simple_module) -> None:
    calls: list[int] = []

    def hard_failure(_prompt: str, _briefing: str) -> str:
        calls.append(1)
        return "ERROR: content_filter: refused by policy"

    result, diag = app_simple_module._r64_call_llm_with_retry(
        hard_failure,
        "prompt",
        "briefing",
        max_attempts=3,
        backoff_base_seconds=0.0,
        sleep=_no_sleep,
    )

    assert result.startswith("ERROR: content_filter:")
    assert len(calls) == 1, "Hard ERROR must not trigger retries"
    assert diag["attempts"] == 1
    assert diag["final_outcome"] == "hard"
    assert diag["last_error_kind"] == "content_filter"


def test_retry_helper_treats_exception_as_transient(app_simple_module) -> None:
    calls: list[int] = []

    def raises_then_returns(_prompt: str, _briefing: str) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("connection reset")
        return "Recovered after exception."

    result, diag = app_simple_module._r64_call_llm_with_retry(
        raises_then_returns,
        "prompt",
        "briefing",
        max_attempts=3,
        backoff_base_seconds=0.0,
        sleep=_no_sleep,
    )

    assert result == "Recovered after exception."
    assert len(calls) == 2
    assert diag["attempts"] == 2
    assert diag["final_outcome"] == "ok"


def test_retry_helper_records_last_exception_kind_when_all_attempts_raise(app_simple_module) -> None:
    calls: list[int] = []

    def always_raises(_prompt: str, _briefing: str) -> str:
        calls.append(1)
        raise ConnectionError("upstream unreachable")

    result, diag = app_simple_module._r64_call_llm_with_retry(
        always_raises,
        "prompt",
        "briefing",
        max_attempts=3,
        backoff_base_seconds=0.0,
        sleep=_no_sleep,
    )

    assert result == ""
    assert len(calls) == 3
    assert diag["attempts"] == 3
    assert diag["final_outcome"] == "exception"
    assert diag["last_error_kind"] == "ConnectionError"


def test_retry_helper_uses_exponential_backoff(app_simple_module) -> None:
    """Backoff windows: 1s before attempt 2, 2s before attempt 3, etc."""
    sleeps: list[float] = []

    def always_transient(_prompt: str, _briefing: str) -> str:
        return "ERROR: timeout: upstream"

    app_simple_module._r64_call_llm_with_retry(
        always_transient,
        "prompt",
        "briefing",
        max_attempts=3,
        backoff_base_seconds=1.0,
        sleep=lambda seconds: sleeps.append(seconds),
    )

    # Sleeps fire BETWEEN attempts (no sleep after the final attempt),
    # so 3 attempts -> 2 sleep windows: 1.0s and 2.0s.
    assert sleeps == [1.0, 2.0]


def test_retry_helper_rejects_invalid_max_attempts(app_simple_module) -> None:
    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        app_simple_module._r64_call_llm_with_retry(
            lambda *_a, **_kw: "ok",
            max_attempts=0,
        )


# ---------------------------------------------------------------------------
# Honest fallback paragraph contract -- diag dict drives the rendered text.
# ---------------------------------------------------------------------------

def test_diag_dict_carries_fields_required_for_honest_fallback(app_simple_module) -> None:
    """The fallback paragraph composed in run_comprehensive_analysis at the
    portfolio fallback branches reads ``attempts`` and ``last_error_kind``
    from the diag dict.  Pin the keys so a future refactor cannot silently
    rename them and cause the fallback to render ``last_error_kind=None``.
    """

    def hard_failure(_prompt: str, _briefing: str) -> str:
        return "ERROR: credentials: missing CIRCUIT_CLIENT_ID"

    _result, diag = app_simple_module._r64_call_llm_with_retry(
        hard_failure,
        "prompt",
        "briefing",
        max_attempts=3,
        backoff_base_seconds=0.0,
        sleep=_no_sleep,
    )

    for required in ("attempts", "final_outcome", "last_error_kind", "max_attempts"):
        assert required in diag, f"Diag dict missing required field: {required}"
    assert diag["last_error_kind"] == "credentials"
