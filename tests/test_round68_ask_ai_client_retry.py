"""Round 68 / Build 42 (C3): pin the client-side error classification +
exponential-backoff retry logic in ``static/js/ask_ai.js``.

These are source-shape pins (not behavioural).  The runtime behaviour
is exercised manually via the ``static/js/ask_ai.js`` flow under
``GET /ask-ai`` -- the JS shape pins guard against accidental regression
(e.g. someone inverts the ``retryable`` flag, drops a kind from the
classification map, or removes the ``_r68MaybeRetry`` call from the
``.catch`` branch -- any of which would re-collapse transient
failures into the bland "Network error: ..." line that pre-R68 users
saw on every flaky-network attempt).
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ASK_AI_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "ask_ai.js"


def _ask_ai_source() -> str:
    assert _ASK_AI_JS.exists(), f"ask_ai.js missing at {_ASK_AI_JS}"
    return _ASK_AI_JS.read_text(encoding="utf-8")


# --- Error classification map ------------------------------------------------


def test_r68_error_kinds_map_present_and_complete() -> None:
    """The ``R68_ERROR_KINDS`` map must enumerate the canonical kinds.

    Removing or renaming a kind breaks the toast / retry contract --
    the dispatcher falls back to ``unknown`` (non-retryable) for any
    unrecognised key, which silently downgrades a retryable transient
    into a hard failure.
    """
    src = _ask_ai_source()
    assert "var R68_ERROR_KINDS = {" in src, "R68_ERROR_KINDS map missing"
    required_kinds = (
        "rate_limit_429",
        "llm_timeout",
        "server_5xx",
        "network",
        "client_abort",
        "content_filter",
        "invalid_input",
        "no_evidence",
        "unknown",
    )
    for kind in required_kinds:
        assert kind + ":" in src, f"R68_ERROR_KINDS missing kind={kind!r}"


def test_r68_retryable_kinds_marked_retryable() -> None:
    """Kinds that the server-side retry helper handles as transient
    MUST also be retryable on the client.  Symmetry with the
    ``_r64_call_llm_with_retry`` server-side classifier (see
    ``app_simple.py``)."""
    src = _ask_ai_source()
    for kind in ("rate_limit_429", "llm_timeout", "server_5xx", "network"):
        # Find the kind line and assert it carries ``retryable: true``.
        idx = src.find(kind + ":")
        assert idx != -1, f"missing kind={kind!r}"
        # The trailing ``retryable: true`` declaration must appear in
        # the same map entry (within the next ~120 chars).
        snippet = src[idx : idx + 200]
        assert "retryable: true" in snippet, (
            f"{kind!r} not marked retryable -- this would silently downgrade a"
            f" transient into a hard failure"
        )


def test_r68_non_retryable_kinds_marked_non_retryable() -> None:
    """Hard-failure kinds (auth/policy/no-evidence) MUST NOT retry --
    retrying a content-policy block would amplify the policy
    violation, retrying invalid input would hammer the server with
    the same bad payload."""
    src = _ask_ai_source()
    for kind in ("client_abort", "content_filter", "invalid_input", "no_evidence", "unknown"):
        idx = src.find(kind + ":")
        assert idx != -1, f"missing kind={kind!r}"
        snippet = src[idx : idx + 200]
        assert "retryable: false" in snippet, (
            f"{kind!r} marked retryable -- this would amplify a non-retryable failure"
        )


# --- Classification function -------------------------------------------------


def test_r68_classify_error_function_present() -> None:
    src = _ask_ai_source()
    assert "function _r68ClassifyError(" in src, "_r68ClassifyError missing"


def test_r68_classify_error_uses_server_hint_first() -> None:
    """Server-side ``error_kind`` field must take priority over
    client-side heuristics -- the server has more context and may
    classify a 200 OK as ``no_evidence`` even though the HTTP status
    is success."""
    src = _ask_ai_source()
    assert "data.error_kind" in src, (
        "client classifier ignores server-side error_kind hint -- this loses"
        " the server's authoritative classification"
    )


def test_r68_classify_error_handles_429_and_408() -> None:
    """HTTP status fallback for rate-limit / request-timeout codes."""
    src = _ask_ai_source()
    assert "httpStatus === 429" in src, "client classifier missing 429 -> rate_limit_429"
    assert "httpStatus === 408" in src, "client classifier missing 408 -> llm_timeout"
    assert ">= 500 && httpStatus < 600" in src, "client classifier missing 5xx -> server_5xx"


def test_r68_classify_error_handles_abort() -> None:
    """``AbortError`` from the 90s timeout MUST classify as
    ``client_abort`` (non-retryable) -- if we mark it retryable
    we'd get into a retry loop on a slow Snowflake fetch."""
    src = _ask_ai_source()
    assert "AbortError" in src, "client classifier missing AbortError handling"
    assert "'client_abort'" in src, "client classifier missing client_abort kind return"


# --- Toast helper ------------------------------------------------------------


def test_r68_show_toast_function_present() -> None:
    src = _ask_ai_source()
    assert "function _r68ShowToast(" in src, "_r68ShowToast missing"


def test_r68_show_toast_uses_textContent_not_innerHTML() -> None:
    """XSS guard -- the toast message comes from server data and must
    never be inserted as HTML.  ``alert.textContent`` is safe;
    ``alert.innerHTML`` would let server-controlled error messages
    inject script."""
    src = _ask_ai_source()
    # Locate the _r68ShowToast body and assert no innerHTML inside.
    body_start = src.find("function _r68ShowToast(")
    assert body_start != -1
    body_end = src.find("\n    }\n", body_start)
    assert body_end != -1
    body = src[body_start:body_end]
    assert "innerHTML" not in body, (
        "_r68ShowToast uses innerHTML -- this is an XSS sink for"
        " server-controlled error messages"
    )
    assert ".textContent" in body, "_r68ShowToast must use textContent (safe)"


def test_r68_show_toast_self_clears() -> None:
    """Toasts must auto-remove after a bounded interval, otherwise
    every retry would stack a permanent banner on the page."""
    src = _ask_ai_source()
    body_start = src.find("function _r68ShowToast(")
    body_end = src.find("\n    }\n", body_start)
    body = src[body_start:body_end]
    assert "setTimeout" in body, "_r68ShowToast doesn't auto-clear"
    assert "alert.remove" in body, "_r68ShowToast doesn't actually remove"


# --- Retry dispatcher --------------------------------------------------------


def test_r68_maybe_retry_function_present() -> None:
    src = _ask_ai_source()
    assert "function _r68MaybeRetry(" in src, "_r68MaybeRetry missing"


def test_r68_max_attempts_default_is_three() -> None:
    """Bounded retries (1 original + 2 retries = 3 total).  Higher
    values would amplify a server outage; lower would fail on
    single-blip transients."""
    src = _ask_ai_source()
    assert "_r68_max_attempts = 3" in src or "_r68_max_attempts == 'number') ? opts._r68_max_attempts : 3" in src or "opts._r68_max_attempts : 3" in src, (
        "default _r68_max_attempts != 3 -- changes the retry budget"
    )


def test_r68_backoff_is_exponential() -> None:
    """Backoff must be exponential (1s, 2s) so a brief burst doesn't
    spawn three back-to-back requests within the same throttling
    window."""
    src = _ask_ai_source()
    assert "Math.pow(2," in src, (
        "_r68MaybeRetry uses non-exponential backoff -- a burst of"
        " retries within the same rate-limit window would re-hit the limit"
    )


def test_r68_maybe_retry_called_on_data_failure() -> None:
    """The ``.then`` branch (HTTP 200 with ``data.ok=false``) must
    call ``_r68MaybeRetry`` so a server-classified retryable
    semantic failure (e.g. ``error_kind='llm_timeout'`` rendered as
    HTTP 200 with ``ok=false``) can recover.  Pre-fix, only the
    ``.catch`` branch retried, leaving server-classified retryables
    surfaced as the legacy banner."""
    src = _ask_ai_source()
    # The data-failure branch lives in the ``else { ... }`` of the
    # ``if (data.ok)`` block.
    idx = src.find("if (_r68MaybeRetry(_r68LastStatus, data, null))")
    assert idx != -1, "_r68MaybeRetry not called from data-failure branch"


def test_r68_maybe_retry_called_on_fetch_error() -> None:
    """The ``.catch`` branch must call ``_r68MaybeRetry`` so a
    transient network error is retried before the user sees the
    bland "Network error" line."""
    src = _ask_ai_source()
    idx = src.find("if (_r68MaybeRetry(_r68LastStatus, null, err))")
    assert idx != -1, "_r68MaybeRetry not called from fetch-error branch"


def test_r68_attempt_counter_threaded_through_recursive_call() -> None:
    """The recursive retry MUST increment ``_r68_attempt`` -- otherwise
    the bounded counter wouldn't bound and we'd loop forever."""
    src = _ask_ai_source()
    assert "nextOpts._r68_attempt = opts._r68_attempt + 1" in src, (
        "retry doesn't increment _r68_attempt -- unbounded retry loop"
    )


# --- HTTP status capture -----------------------------------------------------


def test_r68_last_status_captured_before_body_parse() -> None:
    """The HTTP status must be captured BEFORE ``r.json()`` consumes
    the body, otherwise the classifier loses the 429/408/5xx signal
    and falls back to the body-text heuristic."""
    src = _ask_ai_source()
    assert "_r68LastStatus = r.status" in src, (
        "HTTP status not captured for classifier -- 429/408/5xx hints lost"
    )
    assert "function _askAiJsonWithStatus(" in src, (
        "_askAiJsonWithStatus wrapper missing -- status capture broken"
    )


# --- Toast slot DOM ----------------------------------------------------------


def test_r68_toast_slot_id_is_namespaced() -> None:
    """The toast slot ID must be namespaced (``r68-`` prefix) so
    other scripts on the page can't accidentally collide."""
    src = _ask_ai_source()
    assert "'r68-toast-slot'" in src, (
        "toast slot ID not namespaced -- collision risk with other UI scripts"
    )


# --- ``askAI`` integration ---------------------------------------------------


def test_r68_attempt_initialized_at_top_of_askAI() -> None:
    """``opts._r68_attempt`` must be initialized at the top of the
    synchronous fetch worker before the fetch fires, otherwise the
    recursive call would treat each attempt as the first.

    Round 74 / P3: the original ``askAI`` was renamed to
    ``_r74AskSync`` so the new ``askAI`` could become the
    streaming-vs-sync dispatcher.  The retry attempt counter still
    lives in the same place -- the synchronous worker -- it just
    has a new name now.
    """
    src = _ask_ai_source()
    # Round 74 / P3: probe the renamed sync worker (legacy
    # function-name probe still present as a fallback to surface a
    # clearer failure message if someone re-renames the worker).
    idx = src.find("function _r74AskSync(question, opts) {")
    if idx == -1:
        # Fall back to the legacy name in case some future refactor
        # restores it -- we're checking for the retry counter, not
        # the name per se.
        idx = src.find("function askAI(question, opts) {")
    assert idx != -1, (
        "could not find the synchronous askAI worker (looked for "
        "_r74AskSync(question, opts) and the legacy askAI(question, opts))"
    )
    snippet = src[idx : idx + 1200]
    assert "_r68_attempt" in snippet, "_r68_attempt not initialized in sync worker"
    assert "_r68_max_attempts" in snippet, "_r68_max_attempts not initialized in sync worker"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
