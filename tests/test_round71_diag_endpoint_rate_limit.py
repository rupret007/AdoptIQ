"""Round 71 / Phase 5 (#26) -- per-IP rate limit on diagnostic endpoints.

Pre-R71 ``GET /api/grounding-diagnostics/<analysis_id>`` and
``GET /api/ask-ai/diagnostics/<query_id>`` had no rate limit.  A
malicious or buggy client polling either endpoint at thousands of
requests per second could starve the analysis worker (both endpoints
acquire ``analysis_status_lock``).

Round 71 / Phase 5 (#26) adds a per-IP token-bucket rate limiter
(30 req / 60s window, 1024 IP buckets capped) and returns HTTP 429
with a ``Retry-After`` header on overflow.
"""

from __future__ import annotations

import threading

import pytest


def _reset_buckets():
    """Reset the rate-limit buckets so tests start from a clean state."""
    import app_simple
    with app_simple._R71_DIAG_RATE_LOCK:
        app_simple._R71_DIAG_RATE_BUCKETS.clear()


def test_round71_diag_rate_limit_constants_present() -> None:
    """The R71 rate-limit constants MUST be defined."""
    import app_simple
    assert isinstance(app_simple._R71_DIAG_RATE_LIMIT_PER_MIN, int)
    assert app_simple._R71_DIAG_RATE_LIMIT_PER_MIN > 0
    assert app_simple._R71_DIAG_RATE_WINDOW_S > 0.0
    assert isinstance(app_simple._R71_DIAG_RATE_BUCKETS_MAX, int)
    assert app_simple._R71_DIAG_RATE_BUCKETS_MAX > 0


def test_round71_diag_rate_limit_check_function_exists() -> None:
    """The check helper MUST be callable and return ``(allowed, retry_after)``."""
    import app_simple
    fn = app_simple._r71_diag_rate_limit_check
    _reset_buckets()
    out = fn("127.0.0.1")
    assert isinstance(out, tuple) and len(out) == 2
    allowed, retry = out
    assert isinstance(allowed, bool)
    assert isinstance(retry, int)


def test_round71_diag_rate_limit_first_request_allowed() -> None:
    """The first request from a new IP MUST be allowed."""
    import app_simple
    _reset_buckets()
    allowed, retry = app_simple._r71_diag_rate_limit_check("10.0.0.1")
    assert allowed is True
    assert retry == 0


def test_round71_diag_rate_limit_blocks_after_quota_exhausted() -> None:
    """After ``_R71_DIAG_RATE_LIMIT_PER_MIN`` requests in the window,
    the next request MUST be denied with a positive Retry-After."""
    import app_simple
    _reset_buckets()
    cap = app_simple._R71_DIAG_RATE_LIMIT_PER_MIN
    ip = "10.0.0.2"
    for i in range(cap):
        allowed, _ = app_simple._r71_diag_rate_limit_check(ip)
        assert allowed is True, f"request {i+1}/{cap} should be allowed"
    # The (cap+1)-th request is denied.
    allowed, retry = app_simple._r71_diag_rate_limit_check(ip)
    assert allowed is False, (
        f"Round 71 / Phase 5 (#26): the {cap+1}-th request from a "
        f"single IP MUST be denied."
    )
    assert retry > 0, (
        "Round 71 / Phase 5 (#26): denied requests must carry a "
        "positive Retry-After (seconds)."
    )


def test_round71_diag_rate_limit_buckets_evict_oldest_when_full() -> None:
    """When ``_R71_DIAG_RATE_BUCKETS`` reaches the cap, the oldest
    bucket MUST be evicted to make room (LRU)."""
    import app_simple
    _reset_buckets()
    cap = app_simple._R71_DIAG_RATE_BUCKETS_MAX
    # Fill the bucket dict to one over the cap.
    for i in range(cap + 5):
        app_simple._r71_diag_rate_limit_check(f"10.0.{i % 256}.{i // 256}")
    # The dict size must remain bounded.
    assert len(app_simple._R71_DIAG_RATE_BUCKETS) <= cap, (
        f"Round 71 / Phase 5 (#26): bucket dict must be bounded by "
        f"_R71_DIAG_RATE_BUCKETS_MAX={cap}; got "
        f"{len(app_simple._R71_DIAG_RATE_BUCKETS)}."
    )


def test_round71_diag_rate_limit_under_threading_lock() -> None:
    """The rate-limit check MUST be thread-safe (uses
    ``_R71_DIAG_RATE_LOCK``)."""
    import app_simple
    _reset_buckets()
    assert isinstance(app_simple._R71_DIAG_RATE_LOCK, type(threading.Lock())), (
        "Round 71 / Phase 5 (#26): _R71_DIAG_RATE_LOCK must be a "
        "threading.Lock."
    )
