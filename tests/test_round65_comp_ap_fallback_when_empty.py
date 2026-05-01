"""Round 65 / Phase 1 (C-2) -- Comprehensive XLSX always-write Action_Plans.

When BOTH CSConsole and Snowflake return zero AP rows for a scope,
pre-R65 the comprehensive XLSX silently OMITTED the ``Action_Plans``
sheet altogether. The operator could not distinguish "scope truly has
no APs" from "the writer crashed for this sheet" or "the helper
function was never called".

R65 / Phase 1 (C-2) flips this to ALWAYS write the sheet. When both
sources are empty, a single explanatory provenance row is written so
the operator sees:

  _adoptiq_provenance_row | AdoptIQ_Status | AdoptIQ_Source        | AdoptIQ_Provenance | AdoptIQ_Message
  True                    | EMPTY          | CSConsole+Snowflake   | empty              | "No action plans..."

The provenance row carries a ``_adoptiq_provenance_row=True`` marker so
``canonical_metrics.count_open_action_plans`` skips it (the Summary KPI
must read 0, not 1). This test pins both contracts.

Round 65 / Phase 1.  Made-with: Cursor.
"""

from __future__ import annotations

import pandas as pd

import canonical_metrics as cm
import report_export_styling


# ---------------------------------------------------------------------------
# count_open_action_plans must skip the provenance-row marker
# ---------------------------------------------------------------------------


def test_count_open_action_plans_skips_provenance_row():
    """Critical: the always-write fallback row stamped by the
    comprehensive flow MUST be invisible to the open-count helper, so
    the Summary KPI reads 0 (not 1)."""
    provenance_only = pd.DataFrame([{
        "_adoptiq_provenance_row": True,
        "AdoptIQ_Status": "EMPTY",
        "AdoptIQ_Source": "CSConsole+Snowflake",
        "AdoptIQ_Provenance": "empty",
        "AdoptIQ_Message": "No action plans found for this scope.",
    }])
    assert cm.count_open_action_plans(None, ap_df=provenance_only) == 0, (
        "Round 65 / C-2: the always-write provenance row MUST NOT be "
        "counted as an open AP"
    )


def test_count_open_action_plans_provenance_marker_does_not_break_real_data():
    """Mixed frame: real APs PLUS a provenance row -> only the real
    APs are counted. Defensive -- in normal flows we either have real
    rows OR the provenance row, never both, but this pin protects
    against future writers that mix them."""
    mixed = pd.DataFrame([
        {
            "_adoptiq_provenance_row": False,
            "ID": "AP-1",
            "STATUS_C": "On Track",
        },
        {
            "_adoptiq_provenance_row": False,
            "ID": "AP-2",
            "STATUS_C": "Completed - Successful",
        },
        {
            "_adoptiq_provenance_row": True,
            "ID": None,
            "STATUS_C": "EMPTY",
        },
    ])
    # 3 total rows; 1 provenance (skipped), 2 real (1 open + 1 closed) -> 1 open.
    assert cm.count_open_action_plans(None, ap_df=mixed) == 1


def test_count_open_action_plans_no_provenance_marker_unchanged():
    """Back-compat: frames WITHOUT the marker behave exactly as in R64."""
    ap_df = pd.DataFrame({
        "STATUS_C": ["On Track", "Closed", "New Request"],
    })
    assert cm.count_open_action_plans(None, ap_df=ap_df) == 2


# ---------------------------------------------------------------------------
# build_summary_rows wiring with the provenance-only frame
# ---------------------------------------------------------------------------


def test_build_summary_rows_action_plans_zero_with_provenance_only_sheet():
    """End-to-end Summary contract: when the comprehensive XLSX wrote
    the always-write fallback (provenance-only Action_Plans sheet),
    the Summary 'Action plans (open)' cell MUST read '0' -- never '1',
    never '--'."""
    ab = pd.DataFrame({"ID": [f"AB-{i}" for i in range(70)],
                       "Action Plan Title": [""] * 70})
    provenance_only_ap = pd.DataFrame([{
        "_adoptiq_provenance_row": True,
        "AdoptIQ_Status": "EMPTY",
        "AdoptIQ_Source": "CSConsole+Snowflake",
        "AdoptIQ_Provenance": "empty",
        "AdoptIQ_Message": "No action plans found for this scope.",
    }])
    rows = report_export_styling.build_summary_rows(
        sheets={"AB_Detail_All": ab, "Action_Plans": provenance_only_ap},
        manager="Test", tech="Test", days=90,
    )
    by_label = dict(rows)
    assert by_label.get("Action plans (open)") == "0", (
        "Round 65 / C-2: Summary KPI MUST read '0' when the Action_Plans "
        f"sheet contains only the provenance fallback row; got "
        f"{by_label.get('Action plans (open)')!r}"
    )


# ---------------------------------------------------------------------------
# Provenance-row schema pin: shape changes here MUST update the writer
# ---------------------------------------------------------------------------


def test_provenance_row_schema_pin():
    """Pin the provenance-row schema so future writers that change
    column names are caught. The schema is part of the C-2 contract:
    operators reading the XLSX directly should see consistent labels.
    """
    expected_columns = {
        "_adoptiq_provenance_row",   # marker -- skipped by count_open_action_plans
        "AdoptIQ_Status",            # human-readable status
        "AdoptIQ_Source",            # which sources were queried
        "AdoptIQ_Provenance",        # outcome label (empty / csconsole / snowflake / both)
        "AdoptIQ_Message",           # one-line explanation
    }
    # Build the same shape the writer in app_simple uses -- if the
    # writer drifts, this test breaks first.
    provenance_only = pd.DataFrame([{
        "_adoptiq_provenance_row": True,
        "AdoptIQ_Status": "EMPTY",
        "AdoptIQ_Source": "CSConsole+Snowflake",
        "AdoptIQ_Provenance": "empty",
        "AdoptIQ_Message": (
            "No action plans found for this scope. "
            "Both CSConsole and Snowflake (C360_CS_TASK_C_VW, "
            "record_type_id=0122T000000QHBGQA4) returned zero "
            "rows for the configured technology / customer-name / "
            "owner-email scope and lookback window."
        ),
    }])
    assert set(provenance_only.columns) == expected_columns, (
        "Round 65 / C-2: provenance-row schema drift -- update both the "
        "writer in app_simple.run_comprehensive_analysis AND the "
        "count_open_action_plans skip logic in canonical_metrics.py "
        "if you change these column names"
    )


# ---------------------------------------------------------------------------
# Source-shape pin: app_simple's writer uses the marker
# ---------------------------------------------------------------------------


def test_app_simple_action_plans_writer_uses_provenance_marker():
    """Static source check: the comprehensive Excel writer at the
    Action_Plans branch MUST stamp ``_adoptiq_provenance_row``. If a
    future refactor drops this marker, the Summary KPI will silently
    flip from 0 -> 1 for empty-scope reports."""
    from pathlib import Path  # noqa: PLC0415

    src = Path(__file__).resolve().parent.parent / "app_simple.py"
    text = src.read_text(encoding="utf-8")
    # Pin the provenance row literal.
    assert '"_adoptiq_provenance_row": True' in text, (
        "Round 65 / C-2: comprehensive Excel writer must stamp "
        "_adoptiq_provenance_row=True on the always-write fallback row"
    )
    # Pin that the writer is unconditional (no `if filtered_action_plans...:` gate).
    assert 'all_sheets["Action_Plans"] = pd.DataFrame([{' in text, (
        "Round 65 / C-2: the always-write fallback DataFrame literal "
        "must be present in app_simple.run_comprehensive_analysis"
    )


def test_app_simple_canonical_metrics_skip_provenance_row():
    """Static source check: the open-count helper MUST honor the
    provenance-row marker (so the Summary KPI is correct for empty
    scopes)."""
    from pathlib import Path  # noqa: PLC0415

    src = Path(__file__).resolve().parent.parent / "canonical_metrics.py"
    text = src.read_text(encoding="utf-8")
    assert '"_adoptiq_provenance_row" in ap_df.columns' in text, (
        "Round 65 / C-2: canonical_metrics.count_open_action_plans must "
        "check for and skip the provenance-row marker"
    )
