"""Round 152 / A1 -- default-deny completeness for ``_SENSITIVE_ENDPOINTS``.

``tests/test_round71_sensitive_endpoints_complete.py`` pins a hardcoded
tuple of endpoint names.  That is an *allowlist* pin: it proves the R71
sweep has not been undone, but it says nothing about routes registered
afterwards.  Because of that, 14 data-bearing / state-mutating routes
added in R110 / R113 / R118 / R119 / R132 / R146 were never re-listed and
therefore bypassed all three layers of
``restrict_sensitive_routes_to_localhost``:

  1. the loopback-peer check,
  2. the ``Host`` allow-list (the anti-DNS-rebinding layer), and
  3. the ``no-store`` / ``nosniff`` / ``DENY`` response headers.

The clearest example was ``/api/ask-ai-portfolio/stream``, whose own
docstring promises "same request schema, same throttle, same CSRF" as
the gated ``/api/ask-ai-portfolio`` -- but which was not gated.

These tests invert the maintenance burden.  Every registered endpoint
must be classified in exactly one of ``_SENSITIVE_ENDPOINTS`` or
``_INTENTIONALLY_PUBLIC_ENDPOINTS``, so adding a route without making
that decision fails the build rather than silently opening a hole.
"""

from __future__ import annotations

import pytest

from app_simple import (
    _INTENTIONALLY_PUBLIC_ENDPOINTS,
    _SENSITIVE_ENDPOINTS,
    _UI_SHELL_NOSTORE_ENDPOINTS,
)


# Endpoints that Round 152 found unclassified and moved under the gate.
# Pinned individually so a future "cleanup" that drops one is loud.
R152_NEWLY_GATED_ENDPOINTS = (
    "ask_ai_portfolio_stream",
    "ask_ai_evidence_lookup",
    "get_ask_ai_suggestions",
    "leader_scope_options",
    "decision_workspace_report_evidence",
    "customer_360_page",
    "playbook_page",
    "api_diag_dsm_columns",
    "api_llm_ping",
    "api_update_status",
    "api_update_apply",
    "api_settings_auto_update_mode",
    "api_settings_report_defaults",
    "api_settings_customer_aliases",
)


def _registered_endpoints(app) -> set:
    return {rule.endpoint for rule in app.url_map.iter_rules()}


def test_round152_every_registered_endpoint_is_classified(app) -> None:
    """Default-deny: no route may be left unclassified.

    This is the check that would have caught all 14 R152 findings at the
    moment each route was added.
    """
    classified = set(_SENSITIVE_ENDPOINTS) | set(_INTENTIONALLY_PUBLIC_ENDPOINTS)
    unclassified = sorted(_registered_endpoints(app) - classified)
    assert not unclassified, (
        "Round 152 / A1: the following endpoints are registered but appear in "
        "neither _SENSITIVE_ENDPOINTS nor _INTENTIONALLY_PUBLIC_ENDPOINTS: "
        f"{unclassified}.  Add each one to _SENSITIVE_ENDPOINTS (the default for "
        "anything returning customer/roster/evidence/corpus/analysis data, "
        "mutating state, or spending LLM budget), or to "
        "_INTENTIONALLY_PUBLIC_ENDPOINTS with a comment justifying why it is safe "
        "to serve to a non-loopback origin."
    )


def test_round152_classification_sets_are_disjoint() -> None:
    """A route cannot be both gated and intentionally public."""
    overlap = sorted(set(_SENSITIVE_ENDPOINTS) & set(_INTENTIONALLY_PUBLIC_ENDPOINTS))
    assert not overlap, (
        f"Round 152 / A1: endpoints classified as both sensitive and public: {overlap}"
    )


def test_round152_no_stale_names_in_sensitive_set(app) -> None:
    """Every gated name must correspond to a real route.

    Round 6 / Phase 6.16 removed ``get_status_alias`` for exactly this
    reason: a stale entry hides the fact that no route uses it.
    """
    stale = sorted(set(_SENSITIVE_ENDPOINTS) - _registered_endpoints(app))
    assert not stale, (
        f"Round 152 / A1: _SENSITIVE_ENDPOINTS lists endpoints with no registered "
        f"route: {stale}.  Remove them so the set keeps reflecting reality."
    )


def test_round152_no_stale_names_in_public_set(app) -> None:
    """Same reality check for the intentionally-public complement."""
    stale = sorted(set(_INTENTIONALLY_PUBLIC_ENDPOINTS) - _registered_endpoints(app))
    assert not stale, (
        f"Round 152 / A1: _INTENTIONALLY_PUBLIC_ENDPOINTS lists endpoints with no "
        f"registered route: {stale}."
    )


@pytest.mark.parametrize("endpoint", R152_NEWLY_GATED_ENDPOINTS)
def test_round152_newly_gated_endpoints_are_sensitive(endpoint: str) -> None:
    """Pin each Round 152 finding so a regression is attributable."""
    assert endpoint in _SENSITIVE_ENDPOINTS, (
        f"Round 152 / A1: {endpoint!r} returns customer/roster/evidence data, "
        "mutates operator state, or spends LLM budget, so it must stay in "
        "_SENSITIVE_ENDPOINTS."
    )


def test_round152_stream_route_matches_its_sync_sibling() -> None:
    """``/api/ask-ai-portfolio/stream`` must inherit the sync route's posture.

    Its docstring already claims parity ("Same request schema, same
    throttle, same CSRF"); this makes the localhost gate part of that
    promise instead of an accident of ordering.
    """
    assert ("ask_ai_portfolio" in _SENSITIVE_ENDPOINTS) == (
        "ask_ai_portfolio_stream" in _SENSITIVE_ENDPOINTS
    ), "Ask AI sync and SSE routes must share the same localhost posture."


def test_round152_decision_workspace_family_is_uniformly_gated() -> None:
    """All five decision-workspace read routes share one posture."""
    family = (
        "decision_workspace_scope_preview",
        "decision_workspace_report",
        "decision_workspace_history",
        "decision_workspace_compare",
        "decision_workspace_report_evidence",
    )
    missing = [name for name in family if name not in _SENSITIVE_ENDPOINTS]
    assert not missing, (
        f"Round 152 / A1: decision-workspace routes missing from the gate: {missing}"
    )


def test_round152_server_rendered_data_pages_are_no_store() -> None:
    """Pages that embed customer/operator state must not be cacheable.

    ``customer_360_page`` and ``playbook_page`` render corpus content and
    ``preferences`` renders model settings, so all three need the
    ``Cache-Control: no-store`` posture the other shells already have.
    """
    for name in ("preferences", "customer_360_page", "playbook_page"):
        assert name in _UI_SHELL_NOSTORE_ENDPOINTS, (
            f"Round 152 / A1: {name!r} renders sensitive state and must be in "
            "_UI_SHELL_NOSTORE_ENDPOINTS."
        )


def test_round152_public_set_stays_minimal() -> None:
    """Thermometer: the public complement should stay small and boring.

    If this trips, something data-bearing was probably classified public
    to make the completeness test go green.  Re-read the justification
    comments before raising the ceiling.
    """
    assert len(_INTENTIONALLY_PUBLIC_ENDPOINTS) <= 12, (
        f"_INTENTIONALLY_PUBLIC_ENDPOINTS grew to "
        f"{len(_INTENTIONALLY_PUBLIC_ENDPOINTS)} entries.  Every addition must be "
        "a route that returns no customer/roster/evidence/corpus/analysis data, "
        "mutates no state, and spends no LLM budget."
    )
