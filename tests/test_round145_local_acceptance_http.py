"""Contracts for the all-scenario local loopback acceptance runner."""

from __future__ import annotations

from local_acceptance_lab import build_scenario_bundle
from local_acceptance_runtime import _external_intel
from scripts.run_local_acceptance_http import (
    NONCANONICAL_REPORT_ERROR,
    _expected_fixture_portfolio_counts,
    validate_blocked_missing_id_publication,
    validate_noncanonical_report_ai_response,
    validate_provider_response,
)


def _blocked_missing_id_outcome() -> tuple[dict, dict, dict]:
    status = {
        "status": "error",
        "word_available": False,
        "excel_available": False,
        "error_kind": "analysis.unknown",
        "error": "An unexpected error occurred during analysis.",
        "message": "An unexpected error occurred during analysis.",
        "error_detail": (
            "ValueError: Source Data has 1 CSConsole link coverage error(s) "
            "(missing_stable_id=1)"
        ),
    }
    downloads = {
        extension: {
            "status_code": 400,
            "blocked": True,
            "current_status": "error",
            "artifact_bytes": 0,
            "sanitized": True,
        }
        for extension in ("docx", "xlsx")
    }
    workspace = {
        "report_status_code": 200,
        "report_payload_ok": True,
        "report_status": "error",
        "report_completed": False,
        "workbook_loaded": False,
        "artifact_links_exposed": False,
        "raw_path_leaked": False,
        "report_response_sanitized": True,
        "history_status_code": 200,
        "history_payload_ok": True,
        "history_contains_report": False,
        "history_response_sanitized": True,
    }
    return status, downloads, workspace


def test_missing_id_publication_negative_control_accepts_exact_safe_block() -> None:
    status, downloads, workspace = _blocked_missing_id_outcome()

    projection, errors = validate_blocked_missing_id_publication(
        status,
        downloads,
        workspace,
    )

    assert errors == []
    assert projection["publication_blocked"] is True
    assert projection["negative_control_passed"] is True
    assert projection["failure_reason_verified"] is True
    assert "error_detail" not in projection


def test_missing_id_publication_negative_control_rejects_completion() -> None:
    status, downloads, workspace = _blocked_missing_id_outcome()
    status.update(
        {
            "status": "completed",
            "word_available": True,
            "excel_available": True,
        }
    )

    projection, errors = validate_blocked_missing_id_publication(
        status,
        downloads,
        workspace,
    )

    assert projection["negative_control_passed"] is False
    assert "missing-ID report did not terminate in the error state" in errors
    assert "missing-ID report advertised a Word or Source Data artifact" in errors


def test_missing_id_publication_negative_control_rejects_unrelated_error() -> None:
    status, downloads, workspace = _blocked_missing_id_outcome()
    status["error_detail"] = "RuntimeError: unrelated renderer failure"

    projection, errors = validate_blocked_missing_id_publication(
        status,
        downloads,
        workspace,
    )

    assert projection["failure_reason_verified"] is False
    assert projection["negative_control_passed"] is False
    assert "missing-ID report did not expose the expected stable integrity reason" in errors


def test_missing_id_publication_negative_control_rejects_artifact_leakage() -> None:
    status, downloads, workspace = _blocked_missing_id_outcome()
    downloads["docx"].update(
        {
            "status_code": 200,
            "blocked": False,
            "current_status": "completed",
            "artifact_bytes": 4_096,
        }
    )
    workspace.update(
        {
            "report_status": "completed",
            "report_completed": True,
            "workbook_loaded": True,
            "artifact_links_exposed": True,
            "history_contains_report": True,
        }
    )

    projection, errors = validate_blocked_missing_id_publication(
        status,
        downloads,
        workspace,
    )

    assert projection["downloads_blocked"] is False
    assert projection["workspace_blocked"] is False
    assert projection["negative_control_passed"] is False
    assert "missing-ID report download route exposed or accepted an artifact" in errors
    assert "missing-ID report appeared completed or downloadable in the workspace" in errors


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
        "action_plan_rows": 7,
    }
    assert split == {
        "total_customers": 2,
        "total_barriers": 2,
        "total_cases": 2,
        "action_plan_rows": 6,
    }
    assert roster_gap == split
