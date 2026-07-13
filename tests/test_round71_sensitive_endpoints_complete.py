"""Round 71 / Phase 1 (#6) -- Sensitive endpoints expansion.

Pre-R71 the ``_SENSITIVE_ENDPOINTS`` set in ``app_simple.py`` was missing
15 routes that had been added in subsequent rounds (R17 / R26 / R32 /
R39 / R60 / R65 / R66 / R69) without ever being re-listed.  When an
operator flipped on ``ADOPTIQ_BIND_PUBLIC=1`` for a LAN demo, those
routes were effectively publicly reachable and unauthenticated --
including ``/api/shutdown`` (process-kill) and ``/api/intel/upload``
(file upload).

Round 71 / Phase 1 (#6) extends the set to cover all of them.  These
tests pin every endpoint name so a future cleanup that drops one trips
the lint immediately.
"""

from __future__ import annotations

from app_simple import _SENSITIVE_ENDPOINTS


REQUIRED_R71_ENDPOINTS = (
    "api_shutdown",
    "api_corpus_status",
    "api_corpus_refresh",
    "api_corpus_reset",
    "api_corpus_bootstrap_shortcut",
    "api_intel_status",
    "api_intel_refresh",
    "api_intel_reset",
    "api_intel_upload",
    "api_settings_intelligence",
    "api_settings_ask_ai_model",
    "api_settings_report_model",
    "get_grounding_diagnostics",
    "get_ask_ai_diagnostics",
)


def test_round71_sensitive_endpoints_includes_api_shutdown() -> None:
    """``/api/shutdown`` MUST be localhost-gated -- killing the process
    remotely is the highest-blast-radius miss in the pre-R71 set."""
    assert "api_shutdown" in _SENSITIVE_ENDPOINTS, (
        "Round 71 / Phase 1 (#6): api_shutdown must be in "
        "_SENSITIVE_ENDPOINTS so a LAN attacker cannot remotely "
        "terminate the AdoptIQ process."
    )


def test_round71_sensitive_endpoints_covers_corpus_apis() -> None:
    """Corpus status / refresh / reset endpoints must all be gated."""
    for name in (
        "api_corpus_status",
        "api_corpus_refresh",
        "api_corpus_reset",
        "api_corpus_bootstrap_shortcut",
    ):
        assert name in _SENSITIVE_ENDPOINTS, (
            f"Round 71 / Phase 1 (#6): {name} must be in _SENSITIVE_ENDPOINTS."
        )


def test_round71_sensitive_endpoints_covers_intel_apis() -> None:
    """User-facing intel aliases must be gated alongside the corpus APIs."""
    for name in ("api_intel_status", "api_intel_refresh", "api_intel_reset", "api_intel_upload"):
        assert name in _SENSITIVE_ENDPOINTS, (
            f"Round 71 / Phase 1 (#6): {name} must be in _SENSITIVE_ENDPOINTS."
        )


def test_round71_sensitive_endpoints_covers_settings_apis() -> None:
    """Settings POST endpoints (R32 + R69) must be gated."""
    for name in (
        "api_settings_intelligence",
        "api_settings_ask_ai_model",
        "api_settings_report_model",
    ):
        assert name in _SENSITIVE_ENDPOINTS, (
            f"Round 71 / Phase 1 (#6): {name} must be in _SENSITIVE_ENDPOINTS."
        )


def test_round71_sensitive_endpoints_covers_diagnostic_endpoints() -> None:
    """Diagnostic endpoints (R65/C-3 + R66) must be gated -- they leak
    operator-level signal even with PII redaction."""
    for name in ("get_grounding_diagnostics", "get_ask_ai_diagnostics"):
        assert name in _SENSITIVE_ENDPOINTS, (
            f"Round 71 / Phase 1 (#6): {name} must be in _SENSITIVE_ENDPOINTS."
        )


def test_round71_all_required_endpoints_present_in_one_assertion() -> None:
    """Defense-in-depth: a single set-difference assertion catches any
    future drop in a single failure message."""
    missing = set(REQUIRED_R71_ENDPOINTS) - _SENSITIVE_ENDPOINTS
    assert not missing, (
        f"Round 71 / Phase 1 (#6) requires the following endpoints in "
        f"_SENSITIVE_ENDPOINTS but they are missing: {sorted(missing)}"
    )


def test_round71_sensitive_endpoints_set_minimum_size() -> None:
    """Thermometer: the set must have grown by at least 13 entries vs
    pre-R71.  If a future cleanup shrinks it, this lint trips."""
    # Pre-R71 the set had 30 entries; R71 added 13 + kept the existing
    # ones.  Pin a conservative floor of 35 so reorgs don't false-trip.
    assert len(_SENSITIVE_ENDPOINTS) >= 35, (
        f"_SENSITIVE_ENDPOINTS shrank to {len(_SENSITIVE_ENDPOINTS)} "
        "entries; the Round 71 floor is 35.  If a cleanup intentionally "
        "retired endpoints, lower this floor with a comment explaining why."
    )


def test_round71_existing_sensitive_endpoints_preserved() -> None:
    """The Round 71 expansion must NOT drop any pre-existing entries.
    Sample a few high-priority pre-R71 endpoints to confirm preservation."""
    legacy_must_keep = (
        "start_analysis",
        "start_compact_analysis",
        "start_leader_report",
        "download_result",
        "ask_ai_portfolio",
        "import_intel",
    )
    for name in legacy_must_keep:
        assert name in _SENSITIVE_ENDPOINTS, (
            f"Round 71 expansion accidentally dropped pre-R71 entry "
            f"{name!r}.  This was a regression check failsafe."
        )
