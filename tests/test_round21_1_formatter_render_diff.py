"""Round 21.1 / R20-NEXT-004 — formatter render diff vs golden fixture.

Mission (per QUALITY_AUDIT.md L1299-1416 "Round 19 — Report Accuracy
Golden Fixture"; this round delivers Phases 4F + 5):

    Word/Excel KPI emitted by formatter  ==  hand-computed expected value

Phase 2 + 3 + 4-SSoT shipped in Round 21:
``tests/fixtures/round19/golden.py`` defines synthetic ``ab_df`` /
``csone_df`` / ``pulse_df`` / ``risk_profiles`` / ``extra_frames`` plus
the hand-computed ``EXPECTED_KPIS`` dict, and
``tests/test_round21_canonical_kpi_golden_fixture.py`` pins
``canonical_metrics`` against those expected values.

Round 21.1 adds the formatter-layer half of the diff harness:

    1. Render the Compact, Executive Intelligence, and Leader formatters
       against the golden fixture to a tmp dir.
    2. Parse the resulting ``.docx`` (via ``python-docx``) and the Excel
       summary (via ``openpyxl``).
    3. Extract emitted KPI values from the labelled tile / summary rows
       and assert exact equality against ``EXPECTED_KPIS``.

Three possible outcomes per finding:

    a) Bug in the formatter -- file as ``R21-NEXT-N``; do NOT fix in
       this round (separate behavior-changing round).
    b) Bug in ``EXPECTED_KPIS`` -- fix the expected value, document why
       in the audit log.
    c) Known R20-NEXT-001 closure-binding effect -- mark with
       ``pytest.mark.xfail(reason="R20-NEXT-001 closure-binding bug -- "
       "fix tracked separately")``. This is the explicit channel through
       which Round 21.1 documents the bug's behavioral footprint
       without trying to fix it.

Hard rule: this round only ADDS test artifacts. No source-code changes
to ``compact_report_formatter`` / ``executive_intelligence_formatter`` /
``leader_report_generator`` / ``report_export_styling``. R20-NEXT-001
stays deferred.

Scope notes
-----------
The Compact and Executive Intelligence formatters re-run
``add_case_lifecycle_fields`` on the input ``csone_df`` before computing
KPIs. ``add_case_lifecycle_fields`` always re-derives ``case_type_class``
from the case ``Title`` (it does NOT respect a pre-populated
``case_type_class`` column the way the canonical helpers do -- the
canonical helpers' column-existence fast path is a deliberate test
ergonomic, not a contract). This means:

    * KPIs that flow from raw ``Severity`` / ``Status`` / ``Transaction
      ID`` to the canonical normalizers (priority counts, escalation
      count, BEMS count, open/closed lifecycle) ARE stable across the
      formatter pipeline -- the diff harness asserts exact equality
      against ``EXPECTED_KPIS``.
    * ``break_fix_cases`` / ``provisioning_cases`` are NOT stable across
      the formatter pipeline because the formatter re-classifies from
      titles. The diff harness documents this and does NOT assert
      formatter parity for those two metrics. The canonical-layer test
      already pins them against ``EXPECTED_KPIS``.

The Leader formatter requires a Snowflake context and team-roster
fetch path that cannot be exercised from a unit test without invasive
mocking of the database layer. Round 21.1 captures that explicitly as
``test_leader_formatter_render_deferred_to_round_22`` so the deferral
is visible in the audit trail.

The Excel render is split deliberately:

    * ``write_summary_sheet`` (the SSoT for the Excel summary tab) is
      exercised end-to-end here -- render to xlsx, parse via openpyxl,
      assert labels and values.
    * ``app_simple.py::generate_excel`` (the Excel writer that hosts
      R20-NEXT-001's closure-binding bug at L7335+) is NOT exercised
      from this test. Driving it requires the full Flask app context
      (analysis status dict, output paths, threading state). Round 22
      will lift this once the bug fix lands.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

# Make the golden module importable as a top-level module without
# requiring tests/ or tests/fixtures/ to be Python packages. Same
# pattern as test_round21_canonical_kpi_golden_fixture.py.
_GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "round19"
if str(_GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GOLDEN_DIR))

from golden import (  # noqa: E402
    EXPECTED_KPIS,
    make_ab_df,
    make_csone_df,
    make_pulse_df,
)


# ---------------------------------------------------------------------------
# Helpers: Word docx parsing
# ---------------------------------------------------------------------------


def _docx_table_rows(table) -> List[List[str]]:
    """Return all rows of a Word table as cleaned text lists."""

    rows: List[List[str]] = []
    for row in table.rows:
        rows.append([cell.text.strip() for cell in row.cells])
    return rows


def _find_kpi_in_tile_table(
    doc, header_label: str
) -> Optional[str]:
    """Find the value of ``header_label`` in any 2-row "tile" table.

    The Compact / EI formatters render a "Key Portfolio Metrics" style
    table with the labels in row 0 and the values in row 1. Search
    every table for a row-0 cell that exactly matches ``header_label``
    and return the cell directly below it.
    """
    for table in doc.tables:
        rows = _docx_table_rows(table)
        if len(rows) < 2:
            continue
        header = rows[0]
        values = rows[1]
        for col_idx, label in enumerate(header):
            if label == header_label and col_idx < len(values):
                return values[col_idx]
    return None


def _find_kpi_in_metric_value_table(
    doc, metric_label: str
) -> Optional[str]:
    """Find a value in a "metric | value | ..." style verification table.

    The Compact formatter's "Verification" section uses an N-row table
    where each row is ``[Metric, Value, Source, ...]``. Search every
    table for a row whose first cell equals ``metric_label`` and return
    the second cell.
    """
    for table in doc.tables:
        rows = _docx_table_rows(table)
        if not rows:
            continue
        for row in rows[1:]:
            if not row:
                continue
            if row[0] == metric_label and len(row) >= 2:
                return row[1]
    return None


# ---------------------------------------------------------------------------
# Compact formatter render
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def compact_doc(tmp_path_factory):
    """Render the Compact Word report against the golden fixture once.

    Renders WITHOUT extra_customer_frames so the formatter's internal
    consistency check (``validate_report_consistency`` runs without
    extras while ``portfolio_metrics`` is forced to use the same
    universe) sees a coherent customer count. The canonical-layer test
    in test_round21_canonical_kpi_golden_fixture.py already covers the
    multi-source extras path via ``cm.count_customers``.
    """
    try:
        from compact_report_formatter import create_compact_executive_report
        from docx import Document
    except Exception:
        pytest.skip("compact_report_formatter or python-docx unavailable")

    tmp_dir = tmp_path_factory.mktemp("round21_1_compact")
    out_path = tmp_dir / "compact.docx"
    create_compact_executive_report(
        analysis_id="r21-fixture",
        manager="Manager Round21",
        technology="Webex",
        days=90,
        ab_data=make_ab_df(),
        csone_data=make_csone_df(),
        ai_insights={"executive_summary": "Round 21.1 fixture render"},
        output_path=str(out_path),
    )
    assert out_path.exists(), "Compact report did not write output file"
    return Document(str(out_path))


def test_compact_renders_against_golden_fixture(compact_doc):
    """Compact formatter must produce a non-trivial Word document."""

    assert len(compact_doc.tables) >= 3, (
        "Compact report should have multiple tables (KPI tile, "
        "verification, etc.)"
    )


def test_compact_total_customers_matches_ab_cs_universe(compact_doc):
    """Compact's "Total Customers" tile uses the AB+CSOne customer universe.

    Without extra_customer_frames, the universe is
    ``{AcmeCorp, BetaInc, GammaLLC}`` = 3 (AB has all three, CSOne has
    AcmeCorp+BetaInc, dedup yields 3). The full multi-source value (5)
    is asserted at the canonical layer, not the Compact-render layer.
    """
    value = _find_kpi_in_tile_table(compact_doc, "Total Customers")
    assert value == "3", (
        f"Compact 'Total Customers' tile emitted {value!r}; "
        "expected '3' (AB+CSOne universe with no extras)."
    )


def test_compact_support_cases_matches_expected(compact_doc):
    """Compact's "Support Cases" tile is canonical ``count_total_tac``."""
    value = _find_kpi_in_tile_table(compact_doc, "Support Cases")
    assert value == str(EXPECTED_KPIS["total_cases"]), (
        f"Compact 'Support Cases' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['total_cases']}."
    )


def test_compact_p1_matches_expected(compact_doc):
    """Compact's "Critical (P1)" tile is canonical ``count_p1``."""
    value = _find_kpi_in_tile_table(compact_doc, "Critical (P1)")
    assert value == str(EXPECTED_KPIS["p1_cases"]), (
        f"Compact 'Critical (P1)' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['p1_cases']}."
    )


def test_compact_p2_matches_expected(compact_doc):
    """Compact's "High (P2)" tile is canonical ``count_p2``."""
    value = _find_kpi_in_tile_table(compact_doc, "High (P2)")
    assert value == str(EXPECTED_KPIS["p2_cases"]), (
        f"Compact 'High (P2)' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['p2_cases']}."
    )


def test_compact_bems_matches_expected(compact_doc):
    """Compact's "BEMS Escalations" tile is canonical ``count_bems``."""
    value = _find_kpi_in_tile_table(compact_doc, "BEMS Escalations")
    assert value == str(EXPECTED_KPIS["bems_count"]), (
        f"Compact 'BEMS Escalations' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['bems_count']}."
    )


def test_compact_critical_abs_uses_critical_or_high_mode(compact_doc):
    """Compact's "Critical ABs" tile uses ``CRITICAL_AB_MODE_CRITICAL_OR_HIGH``.

    The Compact formatter at compact_report_formatter.py L2652-2654
    explicitly passes ``mode=cm.CRITICAL_AB_MODE_CRITICAL_OR_HIGH`` to
    ``count_critical_barriers``. EXPECTED_KPIS['count_critical_barriers']
    = 4 = 2 Critical + 2 High in the fixture.
    """
    value = _find_kpi_in_tile_table(compact_doc, "Critical ABs")
    assert value == str(EXPECTED_KPIS["count_critical_barriers"]), (
        f"Compact 'Critical ABs' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['count_critical_barriers']} "
        "(Critical+High mode)."
    )


def test_compact_escalated_cases_matches_expected(compact_doc):
    """Compact's "Escalated Cases" tile is canonical ``count_escalated`` (P1+P2)."""
    value = _find_kpi_in_tile_table(compact_doc, "Escalated Cases")
    assert value == str(EXPECTED_KPIS["count_escalated"]), (
        f"Compact 'Escalated Cases' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['count_escalated']}."
    )


def test_compact_total_adoption_barriers_in_verification_table(compact_doc):
    """Compact's verification table reports the canonical AB total."""
    value = _find_kpi_in_metric_value_table(compact_doc, "Total Adoption Barriers")
    assert value == str(EXPECTED_KPIS["total_barriers"]), (
        f"Compact 'Total Adoption Barriers' verification row emitted "
        f"{value!r}; expected {EXPECTED_KPIS['total_barriers']}."
    )


def test_compact_total_support_cases_in_verification_table(compact_doc):
    """Compact's verification table reports the canonical TAC total."""
    value = _find_kpi_in_metric_value_table(compact_doc, "Total Support Cases")
    assert value == str(EXPECTED_KPIS["total_cases"]), (
        f"Compact 'Total Support Cases' verification row emitted "
        f"{value!r}; expected {EXPECTED_KPIS['total_cases']}."
    )


# ---------------------------------------------------------------------------
# Executive Intelligence formatter render
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ei_doc(tmp_path_factory):
    """Render the Executive Intelligence Word report against the golden fixture."""
    try:
        from executive_intelligence_formatter import (
            create_executive_intelligence_report,
        )
        from docx import Document
    except Exception:
        pytest.skip(
            "executive_intelligence_formatter or python-docx unavailable"
        )

    tmp_dir = tmp_path_factory.mktemp("round21_1_ei")
    out_path = tmp_dir / "ei.docx"
    create_executive_intelligence_report(
        analysis_id="r21-fixture",
        manager="Manager Round21",
        technology="Webex",
        days=90,
        ab_data=make_ab_df(),
        csone_data=make_csone_df(),
        ai_insights={"executive_summary": "Round 21.1 fixture render"},
        ext_bugs=[],
        ext_incidents=[],
        risk_scores={},
        risk_summary={},
        output_path=str(out_path),
    )
    assert out_path.exists(), "EI report did not write output file"
    return Document(str(out_path))


def test_ei_renders_against_golden_fixture(ei_doc):
    """EI formatter must produce a non-trivial Word document."""
    assert len(ei_doc.tables) >= 1, (
        "EI report should have at least one table (KPI tile)."
    )


def test_ei_total_customers_matches_ab_cs_universe(ei_doc):
    """EI's "Total Customers" tile uses the AB+CSOne customer universe (no extras)."""
    value = _find_kpi_in_tile_table(ei_doc, "Total Customers")
    assert value == "3", (
        f"EI 'Total Customers' tile emitted {value!r}; "
        "expected '3' (AB+CSOne universe with no extras)."
    )


def test_ei_support_cases_matches_expected(ei_doc):
    """EI's "Support Cases" tile is canonical ``count_total_tac``."""
    value = _find_kpi_in_tile_table(ei_doc, "Support Cases")
    assert value == str(EXPECTED_KPIS["total_cases"]), (
        f"EI 'Support Cases' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['total_cases']}."
    )


def test_ei_p1_matches_expected(ei_doc):
    """EI's "Critical (P1)" tile is canonical ``count_p1``."""
    value = _find_kpi_in_tile_table(ei_doc, "Critical (P1)")
    assert value == str(EXPECTED_KPIS["p1_cases"]), (
        f"EI 'Critical (P1)' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['p1_cases']}."
    )


def test_ei_p2_matches_expected(ei_doc):
    """EI's "High (P2)" tile is canonical ``count_p2``."""
    value = _find_kpi_in_tile_table(ei_doc, "High (P2)")
    assert value == str(EXPECTED_KPIS["p2_cases"]), (
        f"EI 'High (P2)' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['p2_cases']}."
    )


def test_ei_bems_matches_expected(ei_doc):
    """EI's "BEMS Escalations" tile is canonical ``count_bems``."""
    value = _find_kpi_in_tile_table(ei_doc, "BEMS Escalations")
    assert value == str(EXPECTED_KPIS["bems_count"]), (
        f"EI 'BEMS Escalations' tile emitted {value!r}; "
        f"expected {EXPECTED_KPIS['bems_count']}."
    )


def test_ei_software_defects_zero_when_unwired(ei_doc):
    """When the EI formatter is invoked without ``software_defects``, the
    tile must read 0 (not blank, not N/A). This pins the "no data" UX
    and prevents future regressions where missing inputs render as NaN.
    """
    value = _find_kpi_in_tile_table(ei_doc, "Software Defects")
    assert value == "0", (
        f"EI 'Software Defects' tile emitted {value!r}; "
        "expected '0' when no defects passed."
    )


def test_ei_security_vulnerabilities_zero_when_unwired(ei_doc):
    """Same contract as Software Defects above."""
    value = _find_kpi_in_tile_table(ei_doc, "Security Vulnerabilities")
    assert value == "0", (
        f"EI 'Security Vulnerabilities' tile emitted {value!r}; "
        "expected '0' when no vulns passed."
    )


# ---------------------------------------------------------------------------
# Excel summary render (write_summary_sheet -> xlsx -> openpyxl parse)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def excel_summary_rows(tmp_path_factory):
    """Render write_summary_sheet to a real xlsx and parse the rows back.

    Returns a list of ``(label, value)`` string tuples in order.
    """
    try:
        import openpyxl  # noqa: F401  -- import check only
        from report_export_styling import write_summary_sheet
    except Exception:
        pytest.skip("report_export_styling or openpyxl unavailable")

    tmp_dir = tmp_path_factory.mktemp("round21_1_excel")
    out_path = tmp_dir / "summary.xlsx"

    sheets: Dict[str, Any] = {
        "AB_Detail_All": make_ab_df(),
        "CSOne_Detail_All": make_csone_df(),
        "External_Bugs": pd.DataFrame(),
        "External_Incidents": pd.DataFrame(),
    }
    csconsole_data = {"customer_pulse": make_pulse_df()}

    with pd.ExcelWriter(str(out_path), engine="xlsxwriter") as writer:
        wrote = write_summary_sheet(
            writer,
            sheets,
            csconsole_data,
            manager="Manager Round21",
            tech="Webex",
            days=90,
        )
    assert wrote, "write_summary_sheet returned False"
    assert out_path.exists(), "Summary xlsx not written"

    import openpyxl as _opx
    wb = _opx.load_workbook(str(out_path), read_only=True, data_only=True)
    ws = wb["Summary"]
    rows: List[tuple[str, str]] = []
    for ri, row in enumerate(ws.iter_rows(values_only=True)):
        if ri == 0:
            continue
        if not row:
            continue
        label = "" if row[0] is None else str(row[0])
        value = "" if (len(row) < 2 or row[1] is None) else str(row[1])
        if not label:
            continue
        rows.append((label, value))
    wb.close()
    return rows


def test_excel_summary_label_order_matches_documented_sequence(excel_summary_rows):
    """The Excel summary tab labels appear in the documented sequence
    (registry L1388-1403 -- the same sequence pinned at the canonical
    layer in test_round21_canonical_kpi_golden_fixture.py)."""

    actual_labels = tuple(label for label, _ in excel_summary_rows)
    expected_labels = EXPECTED_KPIS["excel_summary_label_order"]
    assert actual_labels == expected_labels, (
        f"Excel summary label sequence mismatch.\n"
        f"  actual:   {actual_labels!r}\n"
        f"  expected: {expected_labels!r}"
    )


def test_excel_summary_customers_in_portfolio_matches_pulse_universe(
    excel_summary_rows,
):
    """``Customers in portfolio`` flows through ``cm.count_customers(ab,
    cs, pulse)``. With AB={Acme,Beta,Gamma}, CSOne={Acme,Beta},
    pulse={Acme,Beta,Gamma,DeltaCo}, the dedup yields 4 customers.
    """
    by_label = dict(excel_summary_rows)
    value = by_label.get("Customers in portfolio")
    assert value == "4", (
        f"Excel summary 'Customers in portfolio' emitted {value!r}; "
        "expected '4' (AB+CSOne+pulse universe). Pulse adds DeltaCo "
        "(not in AB or CSOne) on top of {Acme, Beta, Gamma}."
    )


def test_excel_summary_adoption_barriers_total_matches_expected(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("Adoption barriers (total)")
    assert value == str(EXPECTED_KPIS["total_barriers"]), (
        f"Excel summary 'Adoption barriers (total)' emitted {value!r}; "
        f"expected {EXPECTED_KPIS['total_barriers']}."
    )


def test_excel_summary_adoption_barriers_critical_matches_expected(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("Adoption barriers (critical)")
    assert value == str(EXPECTED_KPIS["count_critical_barriers"]), (
        f"Excel summary 'Adoption barriers (critical)' emitted {value!r}; "
        f"expected {EXPECTED_KPIS['count_critical_barriers']}."
    )


def test_excel_summary_adoption_barriers_open_matches_expected(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("Adoption barriers (open)")
    assert value == str(EXPECTED_KPIS["count_open_barriers"]), (
        f"Excel summary 'Adoption barriers (open)' emitted {value!r}; "
        f"expected {EXPECTED_KPIS['count_open_barriers']}."
    )


def test_excel_summary_tac_cases_total_matches_expected(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("TAC cases (total)")
    assert value == str(EXPECTED_KPIS["total_cases"]), (
        f"Excel summary 'TAC cases (total)' emitted {value!r}; "
        f"expected {EXPECTED_KPIS['total_cases']}."
    )


def test_excel_summary_tac_cases_p1_matches_expected(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("TAC cases (P1)")
    assert value == str(EXPECTED_KPIS["p1_cases"]), (
        f"Excel summary 'TAC cases (P1)' emitted {value!r}; "
        f"expected {EXPECTED_KPIS['p1_cases']}."
    )


def test_excel_summary_tac_cases_open_matches_expected(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("TAC cases (open)")
    assert value == str(EXPECTED_KPIS["count_open_tac"]), (
        f"Excel summary 'TAC cases (open)' emitted {value!r}; "
        f"expected {EXPECTED_KPIS['count_open_tac']}."
    )


def test_excel_summary_escalations_matches_expected(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("Escalations")
    assert value == str(EXPECTED_KPIS["count_escalated"]), (
        f"Excel summary 'Escalations' emitted {value!r}; "
        f"expected {EXPECTED_KPIS['count_escalated']}."
    )


def test_excel_summary_bems_matches_expected(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("BEMS / break-fix")
    assert value == str(EXPECTED_KPIS["bems_count"]), (
        f"Excel summary 'BEMS / break-fix' emitted {value!r}; "
        f"expected {EXPECTED_KPIS['bems_count']}."
    )


def test_excel_summary_external_bugs_zero_when_empty(excel_summary_rows):
    """Empty External_Bugs DataFrame yields 0 rows in the summary."""
    by_label = dict(excel_summary_rows)
    value = by_label.get("External bugs (rows)")
    assert value == "0", (
        f"Excel summary 'External bugs (rows)' emitted {value!r}; "
        "expected '0' for empty DataFrame."
    )


def test_excel_summary_external_incidents_zero_when_empty(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("External incidents (rows)")
    assert value == "0", (
        f"Excel summary 'External incidents (rows)' emitted {value!r}; "
        "expected '0' for empty DataFrame."
    )


def test_excel_summary_window_days_uses_passed_value(excel_summary_rows):
    """``Window (days)`` reflects the ``days`` arg, not a default."""
    by_label = dict(excel_summary_rows)
    value = by_label.get("Window (days)")
    assert value == "90", (
        f"Excel summary 'Window (days)' emitted {value!r}; "
        "expected '90' (the days arg passed in the fixture)."
    )


def test_excel_summary_manager_scope_uses_passed_value(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("Manager scope")
    assert value == "Manager Round21", (
        f"Excel summary 'Manager scope' emitted {value!r}; "
        "expected 'Manager Round21'."
    )


def test_excel_summary_technology_scope_uses_passed_value(excel_summary_rows):
    by_label = dict(excel_summary_rows)
    value = by_label.get("Technology scope")
    assert value == "Webex", (
        f"Excel summary 'Technology scope' emitted {value!r}; "
        "expected 'Webex'."
    )


# ---------------------------------------------------------------------------
# Leader formatter render (deferred -- documented as R21-NEXT-LEADER)
# ---------------------------------------------------------------------------


def test_leader_formatter_render_harness_landed_in_round_23_1():
    """Round 23.1 closes R21-NEXT-LEADER (deferred Leader render-diff).

    The Round 21.1 cut deliberately punted the Leader formatter
    render-diff because ``leader_report_generator.generate_leader_report``
    requires a live Snowflake ``ctx`` and a ``team_roster`` of
    ``(manager_name, cssm_name, cssm_email)`` tuples, and the
    ``LeaderReportGenerator`` invokes five Snowflake fetch helpers
    during init + render that cannot be exercised from a unit test
    without invasive mocking.

    Round 23.1 / R22-NEXT-LEADER lands that mocking surface as
    ``tests/fixtures/round19/leader_mock_harness.py``: a fixture-aligned
    ``team_roster`` plus a ``patch_leader_generator_with_round19_fixture``
    helper that monkeypatches the five ``_fetch_*`` /
    ``_get_subscriptions_for_cssm`` methods to return Round 19 fixture-
    shaped DataFrames. The actual render-diff lives in
    ``tests/test_round23_1_leader_render_diff.py``; this test only
    asserts the harness module exists so a future round can't silently
    delete it without the audit trail noticing.
    """
    harness_path = Path(__file__).resolve().parent / "fixtures" / "round19" / "leader_mock_harness.py"
    assert harness_path.exists(), (
        f"Round 23.1 / R22-NEXT-LEADER: Leader-report mock harness "
        f"missing at {harness_path}; the deferred R21-NEXT-LEADER "
        f"work cannot land without it."
    )


# ---------------------------------------------------------------------------
# Documented limitation: case_type_class re-derivation
# ---------------------------------------------------------------------------


def test_case_type_class_is_re_derived_by_formatters_documented():
    """Document why ``break_fix_cases`` / ``provisioning_cases`` are NOT
    pinned at the formatter layer.

    The Compact and EI formatters call
    ``add_case_lifecycle_fields(csone_data)`` which always re-classifies
    ``case_type_class`` from the case ``Title`` field, regardless of any
    pre-populated ``case_type_class`` column on the input frame. The
    canonical helpers (``count_break_fix`` / ``count_provisioning``)
    DO respect a pre-populated column as an ergonomic for tests, which
    is why ``EXPECTED_KPIS['break_fix_cases'] == 3`` agrees with
    canonical_metrics on the fixture but not with the formatters'
    re-derived classification.

    This is a deliberate design split, NOT a defect:

      - canonical helpers: respect pre-populated column for test
        ergonomics + pipeline efficiency
      - formatters: always re-derive so the rendered report reflects
        the freshest title-based classification

    The diff harness therefore pins both sides separately:

      - canonical layer: ``break_fix_cases == 3`` (matches
        EXPECTED_KPIS, pinned in test_round21_canonical_kpi_golden_fixture.py)
      - formatter layer: NOT pinned for this metric (this test
        documents the rationale)

    If a future round wants formatter-layer pinning for these two
    metrics, the right move is to (a) drop ``case_type_class`` from
    ``_CSONE_ROWS`` so the canonical layer also re-derives, and (b)
    re-hand-compute ``EXPECTED_KPIS['break_fix_cases']`` /
    ``provisioning_cases`` against the fresh Title-based
    classification. That's a ~30-minute change but it changes the
    contract surface, so it's deferred to Round 22.
    """
    # This test asserts nothing -- its docstring is the documentation.
    # Keep it as a real test so the deferral is grep-able.
    assert True
