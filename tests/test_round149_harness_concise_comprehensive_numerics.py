"""Round 149 — harness uncited-numeric false positives on concise comprehensive DOCX."""

from __future__ import annotations

from report_iteration_loop import _numeric_tokens_requiring_source


def test_action_plan_next_action_step_ordinal_not_a_kpi_claim() -> None:
    line = (
        "aGte6000000td7VCAQ — Identity Services Engine - Onboarding. "
        "Next action: 01 Internal sync-up with Account Team, CXM/CXP/CSM (if applicable)"
    )
    assert _numeric_tokens_requiring_source(line) == []


def test_source_data_overflow_row_pointer_not_a_kpi_claim() -> None:
    line = "4 additional team-member row(s) are in the Source Data File."
    assert _numeric_tokens_requiring_source(line) == []


def test_real_kpi_still_requires_source() -> None:
    assert _numeric_tokens_requiring_source("Total Customers: 52") == ["52"]
