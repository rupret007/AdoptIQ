"""Contracts for the all-scenario local loopback acceptance runner."""

from __future__ import annotations

from local_acceptance_lab import build_scenario_bundle
from local_acceptance_runtime import _external_intel
from scripts.run_local_acceptance_http import (
    NONCANONICAL_REPORT_ERROR,
    _expected_fixture_portfolio_counts,
    validate_noncanonical_report_ai_response,
    validate_provider_response,
)


def test_external_intelligence_adapter_preserves_declared_source_state() -> None:
    stale = build_scenario_bundle("stale")
    payload = _external_intel(stale, 90)

    assert payload["source_states"] == {
        "incidents": "stale",
        "bugs": "available",
        "maintenances": "available",
    }
    assert payload["live_validation_performed"] is False


def test_provider_failure_contract_requires_exact_http_and_safe_state() -> None:
    assert not validate_provider_response(
        "timeout",
        504,
        {"ok": False, "error": "The AI provider timed out. Retry the request."},
    )
    assert validate_provider_response(
        "timeout",
        503,
        {"ok": False, "error": "The AI provider timed out. Retry the request."},
    ) == ["provider timeout returned HTTP 503, expected 504"]


def test_provider_failure_contract_rejects_secret_or_trace_markers() -> None:
    errors = validate_provider_response(
        "unavailable",
        503,
        {
            "ok": False,
            "error": "Provider unavailable; Authorization: Bearer secret",
        },
    )

    assert "response exposed a forbidden secret/error marker" in errors


def test_noncanonical_report_ask_ai_contract_requires_exact_fail_closed_response() -> None:
    assert not validate_noncanonical_report_ai_response(
        409,
        {
            "ok": False,
            "success": False,
            "error": NONCANONICAL_REPORT_ERROR,
        },
    )


def test_noncanonical_report_ask_ai_contract_rejects_answer_or_fallback() -> None:
    errors = validate_noncanonical_report_ai_response(
        200,
        {
            "ok": True,
            "error": "",
            "answer": "A widened legacy answer",
            "fallback_available": True,
        },
    )

    assert "noncanonical report returned HTTP 200, expected 409" in errors
    assert "noncanonical report response included an answer" in errors
    assert "noncanonical report response exposed a legacy fallback" in errors


def test_manager_scoped_oracle_does_not_reuse_portfolio_wide_counts() -> None:
    healthy = _expected_fixture_portfolio_counts(
        build_scenario_bundle("healthy"),
        "Local Fixture Manager",
    )
    split = _expected_fixture_portfolio_counts(
        build_scenario_bundle("multi_manager"),
        "Local Fixture Manager",
    )
    roster_gap = _expected_fixture_portfolio_counts(
        build_scenario_bundle("roster_gap"),
        "Local Fixture Manager",
    )

    assert healthy == {
        "total_customers": 3,
        "total_barriers": 3,
        "total_cases": 3,
        "action_plan_rows": 6,
    }
    assert split == {
        "total_customers": 2,
        "total_barriers": 2,
        "total_cases": 2,
        "action_plan_rows": 5,
    }
    assert roster_gap == split
