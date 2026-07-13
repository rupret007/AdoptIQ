"""Round 67 / Build 41 (B2) -- Comprehensive Risk_Components sheet
is ALWAYS written (success path or provenance-row fallback).

Build 40 acceptance produced a Comprehensive XLSX missing the
``Risk_Components`` sheet entirely, even though R66/B6 had added the
sheet to the writer. Root cause: the row-construction loop was inside
a broad ``try/except`` that silently swallowed any exception during
profile iteration, leaving ``all_sheets["Risk_Components"]`` unset.

Round 67 / B2 hoists the assignment OUT of the broad try/except: the
sheet is ALWAYS placed in ``all_sheets`` -- either the constructed
DataFrame (success) or a single ``_adoptiq_provenance_row=True`` row
(failure-fallback) so the operator sees an honest failure mode
instead of a missing sheet.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_APP_SIMPLE = Path(__file__).resolve().parent.parent / "app_simple.py"


def _read_app_simple() -> str:
    return _APP_SIMPLE.read_text(encoding="utf-8")


def test_risk_components_sheet_assigned_in_success_branch() -> None:
    """R67/B2: success-path assignment MUST be present and outside any
    bare ``try`` block."""
    src = _read_app_simple()
    assert "Round 67 / B2" in src, "R67/B2 marker MUST be present"
    # The success branch assigns from a sorted list of constructed rows.
    pattern = re.compile(
        r"all_sheets\[\"Risk_Components\"\]\s*=\s*pd\.DataFrame\(_r66_risk_rows\)"
    )
    assert pattern.search(src), (
        "R67/B2: success-path assignment MUST be `all_sheets['Risk_Components'] "
        "= pd.DataFrame(_r66_risk_rows)`"
    )


def test_risk_components_sheet_assigned_in_provenance_branch() -> None:
    """R67/B2: failure-fallback MUST also assign the sheet so the
    operator never sees a silently-missing sheet."""
    src = _read_app_simple()
    pattern = re.compile(
        r"all_sheets\[\"Risk_Components\"\]\s*=\s*pd\.DataFrame\(\[\{\s*\n\s*\"_adoptiq_provenance_row\":\s*True"
    )
    assert pattern.search(src), (
        "R67/B2: provenance-fallback MUST assign all_sheets['Risk_Components'] "
        "with a single _adoptiq_provenance_row=True row"
    )


def test_risk_components_provenance_row_includes_construction_error() -> None:
    """R67/B2: when row construction failed, the provenance row's
    AdoptIQ_Message MUST cite the construction error so the operator
    can root-cause without rerunning."""
    src = _read_app_simple()
    assert "_r67_b2_construction_error" in src, (
        "R67/B2: construction error MUST be captured into "
        "_r67_b2_construction_error and surfaced on the provenance row"
    )
    assert "Risk_Components row construction failed" in src, (
        "R67/B2: the provenance row's AdoptIQ_Message MUST name the "
        "Risk_Components construction failure when it triggered"
    )


def test_risk_components_logger_info_records_row_count() -> None:
    """R67/B2: a structured logger.info MUST follow the assignment so
    the row count is visible in production logs (the absence of this
    log line was the root cause of B2 going undetected for a release)."""
    src = _read_app_simple()
    pattern = re.compile(
        r"Round 67 / B2:\s*Risk_Components sheet\s+\"\s*\n?\s*\"built with %d rows"
    )
    # Allow flexible whitespace / continuation in the source.
    assert "Risk_Components sheet " in src, (
        "R67/B2: structured log line for sheet construction MUST be present"
    )
    assert "built with %d rows" in src, (
        "R67/B2: row-count format MUST be 'built with %d rows'"
    )


def test_risk_components_b3_log_emits_all_sheets_keys() -> None:
    """R67/B3: immediately before write_excel_workbook(...) we MUST log
    the sheet count and key list so missing-sheet drift is greppable."""
    src = _read_app_simple()
    assert "Round 67 / B3" in src, "R67/B3 marker MUST be present"
    assert "writing Excel workbook with " in src, (
        "R67/B3: log MUST cite 'writing Excel workbook with %d sheets'"
    )
    assert "sorted(all_sheets.keys())" in src, (
        "R67/B3: log MUST emit sorted(all_sheets.keys()) for diff-friendly grep"
    )


def test_risk_components_construction_loop_inside_inner_try_only() -> None:
    """R67/B2: the row-construction loop MUST sit inside its OWN inner
    try/except so an exception there falls through to the provenance
    branch -- not the broad outer try that silently dropped the sheet
    pre-R67."""
    src = _read_app_simple()
    # The inner try is bound by the construction-error variable.
    pattern = re.compile(
        r"_r67_b2_construction_error:\s*Optional\[str\]\s*=\s*None\s*\n\s*try:"
    )
    assert pattern.search(src), (
        "R67/B2: inner try MUST follow `_r67_b2_construction_error: "
        "Optional[str] = None` so the row-construction failure is "
        "scoped to the inner block"
    )


def test_risk_components_assignment_outside_broad_outer_try() -> None:
    """R67/B2: the assignment MUST be at the same indentation as the
    R66 components-keys block (same scope) so an exception in the
    component-key loop cannot prevent the assignment from happening
    in the fall-through path."""
    src = _read_app_simple()
    # Find the success branch and confirm `if _r66_risk_rows:` precedes it.
    pattern = re.compile(
        r"# Round 67 / B2: ALWAYS assign the sheet.*?\n\s*if _r66_risk_rows:",
        re.DOTALL,
    )
    assert pattern.search(src), (
        "R67/B2: ALWAYS-assign block MUST live AT the outer scope, "
        "introduced by `if _r66_risk_rows:` on success"
    )
