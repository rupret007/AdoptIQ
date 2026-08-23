"""Round 168 fail-closed quality regressions.

Pins three tip-of-main defects that silently turned missing/failed sources
into honest zeros or false customer-scope mismatches, plus the PR CI gate
that ``build.yml`` does not run on pull requests.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import app_simple
import canonical_metrics as cm
import canonical_report_adapter as adapter
from report_completeness_audit import audit_source_data_frames
from source_shape_utils import assert_in_source


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_declared_available_empty_sheet_is_partial_not_zero() -> None:
    """Inverse of R166: Available + no substantive rows is not a real zero."""
    frame = pd.DataFrame(columns=["Customer", "Overall_Risk_Score"])
    result = adapter._strip_placeholder_rows(
        frame,
        declared_state="available",
        declared_detail="legacy Report_Info stamped Available",
    )
    state = cm.source_data_state(result)
    assert result.empty
    assert state["state"] == "partial"
    assert "Round 168" in str(state.get("detail") or "")


def test_declared_available_placeholder_only_sheet_is_partial_not_zero() -> None:
    frame = pd.DataFrame(
        [
            {
                "Dataset": "Subscriptions",
                "Message": "No records were returned in the analysis window",
                "SourceState": "Available",
            }
        ]
    )
    result = adapter._strip_placeholder_rows(
        frame,
        declared_state="available",
        declared_detail="legacy Report_Info stamped Available",
    )
    state = cm.source_data_state(result)
    assert result.empty
    assert state["state"] == "partial"
    assert "Round 168" in str(result.attrs.get("source_mode_detail") or "")


def test_true_zero_empty_sheet_still_stays_zero() -> None:
    frame = pd.DataFrame(columns=["Customer", "Overall_Risk_Score"])
    result = adapter._strip_placeholder_rows(
        frame,
        declared_state="zero",
        declared_detail="successful source returned zero records",
    )
    state = cm.source_data_state(result)
    assert result.empty
    assert state["state"] == "zero"
    assert result.attrs.get("partial") is not True


def test_compact_csone_processing_failure_is_not_honest_zero() -> None:
    warnings: list[dict] = []
    frame = app_simple._r168_record_csone_processing_failure(
        warnings,
        RuntimeError("CSOne parse exploded"),
        kind="runtime",
    )
    state = cm.source_data_state(frame)
    assert frame.empty
    assert state["state"] in {"failed", "unavailable", "partial"}
    assert state["state"] != "zero"
    assert frame.attrs.get("fetch_error")
    assert frame.attrs.get("source_unavailable") is True
    assert warnings
    assert warnings[0]["dataset"] == "csone"
    assert warnings[0]["kind"] == "fetch_failed"
    assert "unavailable, not zero" in warnings[0]["effect"]


def test_compact_csone_timeout_kind_is_timeout() -> None:
    frame = app_simple._r168_csone_processing_failure_frame(
        "CSOne data processing timed out after 60 seconds",
        kind="timeout",
    )
    assert frame.attrs.get("fetch_error_kind") == "timeout"
    assert cm.source_data_state(frame)["state"] != "zero"


def test_compact_csone_failure_paths_are_wired() -> None:
    source = Path("app_simple.py").read_text(encoding="utf-8")
    assert_in_source(source, "# Round 168:", label="app_simple.py")
    assert source.count("_r168_record_csone_processing_failure(") >= 4
    assert source.count("_r168_csone_processing_failure_frame(") >= 2


def _customer_scope_sheets(scope_value: str, customer_name: str) -> dict[str, pd.DataFrame]:
    return {
        "Report_Info": pd.DataFrame(
            [
                {"Item": "Scope_Type", "Value": "customer"},
                {"Item": "Scope_Value", "Value": scope_value},
            ]
        ),
        "Action_Plans": pd.DataFrame(
            [
                {
                    "ID": "AP-NYU-1",
                    "Customer Name": customer_name,
                    "AdoptIQ_Status_Bucket": "Open",
                    "AdoptIQ_Data_Quality": "OK",
                }
            ]
        ),
    }


def test_completeness_audit_accepts_alias_sibling_customer_name() -> None:
    audit = audit_source_data_frames(
        _customer_scope_sheets("NYU LANGONE HEALTH SYSTEMS", "NYU MEDICAL CENTER")
    )
    assert audit["customer_scope_mismatch_rows"] == 0
    assert not any("out-of-scope" in error for error in audit["errors"])


def test_completeness_audit_still_rejects_unrelated_customer_name() -> None:
    audit = audit_source_data_frames(
        _customer_scope_sheets("NYU LANGONE HEALTH SYSTEMS", "CISCO SYSTEMS")
    )
    assert audit["ok"] is False
    assert audit["customer_scope_mismatch_rows"] >= 1
    assert audit["customer_scope_mismatch_by_sheet"]["Action_Plans"] >= 1


def test_pr_quality_workflow_file_exists() -> None:
    workflow = PROJECT_ROOT.joinpath(".github", "workflows", "quality.yml")
    assert workflow.is_file()
    text = workflow.read_text(encoding="utf-8")
    assert "Round 168" in text
    assert "pull_request:" in text
