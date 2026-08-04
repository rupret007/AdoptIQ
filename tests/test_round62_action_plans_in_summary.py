"""Round 62 / Phase B: pin the deterministic ``action_plans`` anchor
in the comprehensive XLSX ``Summary`` sheet.

The R58 soak event was the supervisor harness reporting an
``action_plans`` parity drift on the comprehensive scenario because
the LLM emitted ``"there are 0 Action Plans"`` in the narrative and
the canonical KPI extractor missed it (no XLSX cell carried the
value).  R61 / D added a prefix regex to catch the LLM phrasing IF
the LLM uses it, but the Build 34 acceptance run showed the LLM
doesn't always do so.

R62 / B closes the actual root cause: a new
``canonical_metrics.count_open_action_plans(ab_df)`` helper derives
the count from ``AB_Detail_All``'s ``Action Plan Title`` column
(non-empty rows = open APs), and ``report_export_styling.build_
summary_rows`` adds a new ``("Action plans (open)", ...)`` row right
after the AB cluster so every comprehensive XLSX ships a structured
KPI anchor regardless of LLM phrasing.

These tests pin six contracts:

1. The helper returns 0 for empty / None / missing-column frames.
2. The helper counts non-empty ``Action Plan Title`` cells correctly.
3. The helper strips whitespace-only cells (``"   "`` is NOT counted).
4. The helper tolerates the four naming conventions seen in the wild
   (``Action Plan Title`` / ``action_plan_title`` / ``AP_TITLE_C`` /
   ``ACTION_PLAN_TITLE``).
5. ``build_summary_rows`` includes the new row in the expected
   position (right after ``Adoption barriers (open)`` and before
   ``TAC cases (total)``) and carries the helper's value.
6. End-to-end through the Round 19 golden-fixture label order: the
   new row has a known canonical position so future cross-format
   parity checks know where to look.
"""

from __future__ import annotations

import pandas as pd

import canonical_metrics
import report_export_styling


# ---------------------------------------------------------------------------
# count_open_action_plans helper contracts
# ---------------------------------------------------------------------------


def test_count_open_action_plans_returns_zero_for_none():
    assert canonical_metrics.count_open_action_plans(None) == 0


def test_count_open_action_plans_returns_zero_for_empty_frame():
    assert canonical_metrics.count_open_action_plans(pd.DataFrame()) == 0


def test_count_open_action_plans_returns_zero_when_column_missing():
    df = pd.DataFrame({"ID": ["AB-1", "AB-2"], "Customer": ["Acme", "Beta"]})
    assert canonical_metrics.count_open_action_plans(df) == 0


def test_count_open_action_plans_counts_nonempty_rows_in_action_plan_title():
    df = pd.DataFrame({
        "ID": ["AB-1", "AB-2", "AB-3", "AB-4"],
        "Action Plan Title": [
            "Firewall Baseline Plan",
            "",
            "Renewal Outreach Plan",
            None,
        ],
    })
    assert canonical_metrics.count_open_action_plans(df) == 2


def test_count_open_action_plans_strips_whitespace_only_cells():
    """Whitespace-only cells must NOT count as open action plans
    (a stray space from a copy-paste or a stripped CSV cell does
    not represent a real action plan)."""
    df = pd.DataFrame({
        "Action Plan Title": ["Real Plan", "   ", "Another Real Plan", "\t\n", ""],
    })
    assert canonical_metrics.count_open_action_plans(df) == 2


def test_count_open_action_plans_tolerates_alternate_column_names():
    """The helper must accept the four naming conventions seen in
    the wild (CSConsole header, snake_case, raw Salesforce, Snowflake
    upper-case)."""
    for col_name in ("Action Plan Title", "action_plan_title", "AP_TITLE_C", "ACTION_PLAN_TITLE"):
        df = pd.DataFrame({col_name: ["Plan One", "Plan Two", ""]})
        assert canonical_metrics.count_open_action_plans(df) == 2, (
            f"helper failed to recognize column name {col_name!r}"
        )


def test_count_open_action_plans_zero_when_all_cells_empty():
    """Mirrors the Build 34 comprehensive case: AB rows present but
    every Action Plan Title is empty (the actual production data shape
    that triggered the R58 drift event)."""
    df = pd.DataFrame({
        "ID": [f"AB-{i}" for i in range(70)],
        "Action Plan Title": [""] * 70,
    })
    assert canonical_metrics.count_open_action_plans(df) == 0


# ---------------------------------------------------------------------------
# build_summary_rows wiring contracts
# ---------------------------------------------------------------------------


def _ab_with_n_action_plans(n_plans: int, n_total_rows: int = 70) -> pd.DataFrame:
    """Helper: construct an AB_Detail_All-shaped frame with N populated
    Action Plan Title cells (rest empty)."""
    titles = [f"Plan {i}" for i in range(n_plans)] + [""] * (n_total_rows - n_plans)
    return pd.DataFrame({
        "ID": [f"AB-{i}" for i in range(n_total_rows)],
        "Customer Name": ["Acme"] * n_total_rows,
        "Action Plan Title": titles,
    })


def test_build_summary_rows_includes_action_plans_row():
    rows = report_export_styling.build_summary_rows(
        sheets={"AB_Detail_All": _ab_with_n_action_plans(3)},
        manager="Brian Frazier",
        tech="All Contact Center",
        days=90,
    )
    labels = [label for label, _ in rows]
    assert "Action plans (open)" in labels, (
        f"Round 62 / B: 'Action plans (open)' must be in build_summary_rows output; "
        f"got labels={labels}"
    )


def test_build_summary_rows_action_plans_value_matches_helper():
    ab = _ab_with_n_action_plans(5)
    rows = report_export_styling.build_summary_rows(
        sheets={"AB_Detail_All": ab},
        manager="Brian Frazier",
        tech="All Contact Center",
        days=90,
    )
    by_label = dict(rows)
    assert by_label.get("Action plans (open)") == "5", (
        f"Round 62 / B: 'Action plans (open)' value must match count_open_action_plans; "
        f"helper says {canonical_metrics.count_open_action_plans(ab)}, summary row says {by_label.get('Action plans (open)')!r}"
    )


def test_build_summary_rows_action_plans_position_is_after_adoption_barriers():
    """Pin the canonical position so cross-format parity checks know
    where to find the row (canonical sequence: Customers -> Barriers
    cluster -> Action plans -> TAC cluster -> Escalations -> BEMS)."""
    rows = report_export_styling.build_summary_rows(
        sheets={"AB_Detail_All": _ab_with_n_action_plans(2)},
        manager="Test", tech="Test", days=90,
    )
    labels = [label for label, _ in rows]
    ap_idx = labels.index("Action plans (open)")
    open_barriers_idx = labels.index("Adoption barriers (open)")
    tac_total_idx = labels.index("TAC cases (total)")
    assert open_barriers_idx < ap_idx < tac_total_idx, (
        f"Round 62 / B: 'Action plans (open)' must sit between 'Adoption barriers (open)' "
        f"({open_barriers_idx}) and 'TAC cases (total)' ({tac_total_idx}); "
        f"actually at {ap_idx}.  Full sequence: {labels}"
    )


def test_build_summary_rows_action_plans_zero_when_ab_empty():
    """When the AB frame is missing or empty, the new row must still
    appear (so the Summary's row count is stable across all
    scenarios) but carry "0" so the harness sees a deterministic
    anchor instead of "--"."""
    rows = report_export_styling.build_summary_rows(
        sheets={"AB_Detail_All": pd.DataFrame()},
        manager="Test", tech="Test", days=90,
    )
    by_label = dict(rows)
    assert "Action plans (open)" in by_label, (
        "Round 62 / B: row must appear even when AB frame is empty so "
        "the Summary sheet's column-count is stable across scenarios"
    )
    assert by_label["Action plans (open)"] == "0", (
        f"Round 62 / B: empty AB frame must yield '0' open action plans; got {by_label['Action plans (open)']!r}"
    )


def test_harness_keeps_open_action_plans_distinct_from_total_action_plans():
    """Open plans are a subset and must not be compared with total plans."""
    from report_iteration_loop import _normalize_kpi_label, KPI_ALIASES  # noqa: PLC0415
    assert _normalize_kpi_label("Action plans (open)") == "open_action_plans"
    assert _normalize_kpi_label("open action plans") == "open_action_plans"
    assert _normalize_kpi_label("Action plans open") == "open_action_plans"
    assert _normalize_kpi_label("Total Action Plans") == "action_plans"
    assert "open action plans" in KPI_ALIASES["open_action_plans"]


def test_build_summary_rows_action_plans_is_in_round19_golden_fixture():
    """Cross-pin: the Round 19 golden fixture's
    ``excel_summary_label_order`` must include the new row in the
    same position as ``build_summary_rows`` emits it.  Catches
    drift between the canonical fixture and the live builder."""
    from tests.fixtures.round19.golden import EXPECTED_KPIS  # noqa: PLC0415
    assert "Action plans (open)" in EXPECTED_KPIS["excel_summary_label_order"], (
        "Round 62 / B: tests/fixtures/round19/golden.py:excel_summary_label_order "
        "must include 'Action plans (open)' to keep the goldens in sync with the "
        "live build_summary_rows output."
    )
    expected_seq = EXPECTED_KPIS["excel_summary_label_order"]
    ap_idx = expected_seq.index("Action plans (open)")
    open_barriers_idx = expected_seq.index("Adoption barriers (open)")
    tac_total_idx = expected_seq.index("TAC cases (total)")
    assert open_barriers_idx < ap_idx < tac_total_idx, (
        "Round 62 / B: golden fixture position drift -- 'Action plans (open)' must "
        f"sit between 'Adoption barriers (open)' ({open_barriers_idx}) and "
        f"'TAC cases (total)' ({tac_total_idx}); actually at {ap_idx}"
    )
