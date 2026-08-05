"""Round 23 / R22-NEXT-001 — closure-binding fix verification tests.

Mission (per the Round 23 plan / QUALITY_AUDIT.md "Round 23"):

    Inside the nested ``generate_report`` (app_simple.py L7073) and
    ``generate_excel`` (L7395) functions in ``run_compact_analysis``,
    the code used the antipattern

        X if 'X' in locals() else FALLBACK

    to read outer-scope dataframes (csconsole_action_plans,
    csconsole_customer_pulse, csconsole_success_priorities,
    csconsole_adoption_barriers, software_defects, psirt_vulns,
    partial_data_warnings, data_retrieved_at, days,
    team_subs_df_unfiltered).

    Inside a nested Python function, ``locals()`` does NOT include
    free variables captured from the enclosing scope, so each guard
    always took the FALLBACK branch -- silently dropping
    csconsole-only customers from the renewal risk universe and
    hardcoding ``recent_window_days=30`` regardless of the ``days``
    parameter.

    Round 23 introduces an explicit ``_r23_ctx`` dict in the OUTER
    scope (where ``locals()`` works correctly for capturing
    conditionally-bound names) and threads it as a default argument
    into both nested functions.

This test file pins three product-visible properties of the fix:

    R22-NEXT-002a  --  EI WITH-extras render contract (formatter
                       layer correctly handles csconsole frames when
                       the caller threads them, which is what the
                       closure-binding fix now lets it do).
    R22-NEXT-002b  --  Compact WITH-extras render contract (same).
    R22-NEXT-002c  --  recent_window_days flow-through: the fixed
                       source code reads ``days`` via the ctx dict
                       and threads it into
                       ``calculate_renewal_risk_scores`` instead of
                       hardcoding 30.
    R22-NEXT-003   --  end-to-end ``write_summary_sheet`` render
                       against the multi-source golden fixture
                       (closes Round 21.1 hot spot #3 -- the Excel
                       writer was previously only exercised via
                       formatter-direct paths, never with extras
                       threaded the way the closure-binding fix now
                       enables).

Why these are formatter / static / write_summary_sheet tests rather
than full end-to-end driver tests
----------------------------------
``run_compact_analysis`` requires a Snowflake ``ctx`` plus the Flask
analysis-status dict and threading state to run. Building a faithful
mock for that surface is the Round 23.1 / R22-NEXT-LEADER mission.
Until that harness lands, R22-NEXT-002 / R22-NEXT-003 verify the fix
via its three observable surfaces:

    1. The formatter layer correctly emits Total Customers=5 when the
       caller threads csconsole frames (proving the formatter is not
       buggy -- the bug was in the caller failing to thread).
    2. The fixed source code has the correct ctx-based shape
       (``_r23_ctx``, ``_ctx.get('days')``, ``int(_r23_days)``) so a
       future edit cannot silently re-introduce the closure-binding
       pattern.
    3. ``write_summary_sheet`` round-trips the multi-source customer
       universe + ``days`` arg correctly (5 customers / Window=90).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"

# Make the golden module importable as a top-level module without
# requiring tests/ or tests/fixtures/ to be Python packages -- same
# pattern as test_round21_1_formatter_render_diff.py.
_GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "round19"
if str(_GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GOLDEN_DIR))

from golden import (  # noqa: E402
    EXPECTED_KPIS,
    make_ab_df,
    make_csone_df,
    make_extra_frames,
    make_pulse_df,
)


# ---------------------------------------------------------------------------
# docx parsing helpers (same shape as test_round21_1_formatter_render_diff)
# ---------------------------------------------------------------------------


def _docx_table_rows(table) -> List[List[str]]:
    rows: List[List[str]] = []
    for row in table.rows:
        rows.append([cell.text.strip() for cell in row.cells])
    return rows


def _find_kpi_in_tile_table(doc, header_label: str) -> Optional[str]:
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


# ---------------------------------------------------------------------------
# Fixture: render the EI / Compact formatters WITH extras threaded.
# ---------------------------------------------------------------------------
#
# These fixtures are the formatter-layer half of the closure-binding
# fix verification. The closure-binding bug lived in the CALLER
# (run_compact_analysis); the formatter itself correctly handles the
# multi-source frames when they are threaded in. These render tests
# pin that contract so a future edit cannot regress it.


def _build_extras_kwargs() -> Dict[str, pd.DataFrame]:
    """Unpack ``make_extra_frames()`` into the four csconsole_* kwargs.

    ``make_extra_frames()`` returns a fixed-order 4-tuple:
    ``[action_plans, customer_pulse, success_priorities, adoption_barriers]``.
    """
    extras = make_extra_frames()
    return {
        "csconsole_action_plans": extras[0],
        "csconsole_customer_pulse": extras[1],
        "csconsole_success_priorities": extras[2],
        "csconsole_adoption_barriers": extras[3],
    }


@pytest.fixture(scope="module")
def ei_doc_with_extras(tmp_path_factory):
    """Render EI with the four csconsole_* frames threaded through.

    This is the call shape a non-buggy ``run_compact_analysis`` would
    produce (and what the Round 23 closure-binding fix now enables).
    """
    try:
        from executive_intelligence_formatter import (
            create_executive_intelligence_report,
        )
        from docx import Document
    except Exception:
        pytest.skip(
            "executive_intelligence_formatter or python-docx unavailable"
        )

    tmp_dir = tmp_path_factory.mktemp("round23_ei_with_extras")
    out_path = tmp_dir / "ei_with_extras.docx"
    create_executive_intelligence_report(
        analysis_id="r23-fixture",
        manager="Manager Round23",
        technology="Webex",
        days=90,
        ab_data=make_ab_df(),
        csone_data=make_csone_df(),
        ai_insights={"executive_summary": "Round 23 closure-binding fix render"},
        ext_bugs=[],
        ext_incidents=[],
        risk_scores={},
        risk_summary={},
        output_path=str(out_path),
        **_build_extras_kwargs(),
    )
    assert out_path.exists(), "EI report did not write output file"
    return Document(str(out_path))


@pytest.fixture(scope="module")
def compact_doc_with_extras(tmp_path_factory):
    """Render Compact with the four csconsole_* frames threaded through."""
    try:
        from compact_report_formatter import create_compact_executive_report
        from docx import Document
    except Exception:
        pytest.skip("compact_report_formatter or python-docx unavailable")

    tmp_dir = tmp_path_factory.mktemp("round23_compact_with_extras")
    out_path = tmp_dir / "compact_with_extras.docx"
    create_compact_executive_report(
        analysis_id="r23-fixture",
        manager="Manager Round23",
        technology="Webex",
        days=90,
        ab_data=make_ab_df(),
        csone_data=make_csone_df(),
        ai_insights={"executive_summary": "Round 23 closure-binding fix render"},
        output_path=str(out_path),
        **_build_extras_kwargs(),
    )
    assert out_path.exists(), "Compact report did not write output file"
    return Document(str(out_path))


# ---------------------------------------------------------------------------
# R22-NEXT-002a  --  EI WITH-extras: Total Customers must be 5
# ---------------------------------------------------------------------------


def test_ei_render_with_extras_after_r22_next_001(ei_doc_with_extras):
    """EI's "Total Customers" tile must read 5 when the caller threads
    the four csconsole_* frames.

    Pre-fix, the closure-binding bug in ``generate_report`` silently
    dropped these frames (because ``'csconsole_action_plans' in
    locals()`` evaluated False inside the nested function), and the
    EI formatter saw an AB+CSOne-only universe of 3 customers
    (AcmeCorp, BetaInc, GammaLLC).

    Post-fix, ``generate_report`` reads them from the explicit
    ``_r23_ctx`` dict and threads them into the formatter, which
    correctly expands the universe to 5 (adds DeltaCo, EpsilonInc).
    """
    value = _find_kpi_in_tile_table(ei_doc_with_extras, "Total Customers")
    assert value == str(EXPECTED_KPIS["total_customers"]), (
        f"EI 'Total Customers' tile emitted {value!r} when the four "
        f"csconsole_* frames were threaded; expected "
        f"{EXPECTED_KPIS['total_customers']} (the multi-source "
        "universe AcmeCorp+BetaInc+GammaLLC+DeltaCo+EpsilonInc). If "
        "the value collapsed back to 3, the closure-binding bug in "
        "``generate_report`` is back -- check that ``_r23_ctx`` is "
        "still being threaded into the EI formatter call site."
    )


# ---------------------------------------------------------------------------
# R22-NEXT-002b  --  Compact WITH-extras: Total Customers must be 5
# ---------------------------------------------------------------------------


def test_compact_render_with_extras_after_r22_next_001(compact_doc_with_extras):
    """Compact's "Total Customers" tile must read 5 when the caller
    threads the four csconsole_* frames.

    Same closure-binding bug as the EI test above; same fix; same
    contract pin.
    """
    value = _find_kpi_in_tile_table(compact_doc_with_extras, "Total Customers")
    assert value == str(EXPECTED_KPIS["total_customers"]), (
        f"Compact 'Total Customers' tile emitted {value!r} when the "
        f"four csconsole_* frames were threaded; expected "
        f"{EXPECTED_KPIS['total_customers']}. If the value collapsed "
        "back to 3, the closure-binding bug in ``generate_report`` "
        "(fallback path that calls ``_create_enhanced_compact_report``) "
        "is back -- check that ``_r23_ctx`` is still being threaded."
    )


# ---------------------------------------------------------------------------
# R22-NEXT-002c  --  recent_window_days flow-through (static contract)
# ---------------------------------------------------------------------------
#
# ``recent_window_days`` is an internal arg to
# ``calculate_renewal_risk_scores``; it is NOT exposed on the
# formatter surface. So this test pins the source-code contract
# rather than the rendered output: the fixed code must read ``days``
# from the ctx dict (``_ctx.get('days')``) and thread it into the
# call as ``int(_r23_days) if _r23_days else 30``, NOT hardcode 30.


_R23_DAYS_CTX_GET = "_r23_days = _ctx.get('days')"
_R23_DAYS_RWD = "recent_window_days=int(_r23_days) if _r23_days else 30"


def _r23_ctx_dict() -> ast.Dict:
    tree = ast.parse(APP_SIMPLE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "_r23_ctx"
            for target in node.targets
        ):
            return node.value
    raise AssertionError("_r23_ctx dict assignment not found")


def test_recent_window_days_reads_days_from_ctx_dict() -> None:
    """The fixed nested functions must read ``days`` from ``_ctx``.

    Pre-fix, the code read ``days if 'days' in locals() else 30``
    inside the nested function. ``locals()`` doesn't include free
    vars from the enclosing scope, so the False branch always ran
    and ``recent_window_days`` was always 30 regardless of the
    ``days`` parameter passed into ``run_compact_analysis``.

    Post-fix, both nested functions must use ``_ctx.get('days')``
    so the analysis horizon flows correctly into the renewal-risk
    universe.
    """
    src = APP_SIMPLE.read_text(encoding="utf-8")
    occurrences = len(
        re.findall(
            r"_r23_days\s*=\s*_ctx\.get\(\s*[\"']days[\"']\s*\)",
            src,
        )
    )
    # Both ``generate_report`` (twice: try block + fallback) and
    # ``generate_excel`` (twice: try block + fallback) read
    # ``days`` from the ctx, so we expect at least 4 occurrences.
    assert occurrences >= 4, (
        f"R22-NEXT-001 regression: app_simple.py has only "
        f"{occurrences} occurrence(s) of {_R23_DAYS_CTX_GET!r}; "
        f"expected >= 4 (the closure-binding fix routes ``days`` "
        f"via ``_ctx.get('days')`` in both ``generate_report`` and "
        f"``generate_excel``, each with a try block + a fallback "
        f"branch that need the ctx read). Restore the ctx-based "
        f"access introduced in Round 23."
    )


def test_recent_window_days_threads_into_calculate_renewal_risk_scores() -> None:
    """The fixed nested functions must thread ``_r23_days`` (not 30)
    into ``calculate_renewal_risk_scores``.

    A future edit that changes the threading shape -- e.g. drops the
    ``int(_r23_days)`` cast or hardcodes 30 again -- will fail this
    assertion before it can ship.
    """
    src = APP_SIMPLE.read_text(encoding="utf-8")
    occurrences = src.count(_R23_DAYS_RWD)
    assert occurrences >= 4, (
        f"R22-NEXT-001 regression: app_simple.py has only "
        f"{occurrences} occurrence(s) of "
        f"{_R23_DAYS_RWD!r}; expected >= 4. The closure-binding fix "
        f"threads the analysis horizon into the four "
        f"``calculate_renewal_risk_scores`` call sites in "
        f"``generate_report`` (try + fallback) and ``generate_excel`` "
        f"(try + fallback). Restore the ``int(_r23_days) if "
        f"_r23_days else 30`` shape."
    )


def test_recent_window_days_old_locals_check_is_gone() -> None:
    """The pre-fix shape ``int(days) if 'days' in locals() and days else 30``
    must NOT be present anywhere in the nested-function bodies."""
    src = APP_SIMPLE.read_text(encoding="utf-8")
    legacy_shape = "int(days) if 'days' in locals() and days else 30"
    assert legacy_shape not in src, (
        "R22-NEXT-001 regression: the pre-fix "
        f"{legacy_shape!r} shape is back in app_simple.py. Inside "
        "the nested ``generate_report`` / ``generate_excel`` "
        "functions, this guard always took the False branch (free "
        "vars are not in ``locals()``) and silently hardcoded "
        "``recent_window_days=30``. Restore the ``_r23_ctx``-based "
        "ctx-dict access introduced in Round 23."
    )


# ---------------------------------------------------------------------------
# R22-NEXT-001 ctx dict shape contract (defence in depth)
# ---------------------------------------------------------------------------
#
# These two tests overlap with the R20 marker test in
# tests/test_round20_in_locals_simplification.py but are colocated
# here so a future reader investigating an R22-NEXT-001 regression
# finds all the relevant pins in one place.


def test_r23_ctx_dict_includes_all_outer_scope_frames() -> None:
    """``_r23_ctx`` must capture every outer-scope free var the
    nested functions read.

    The 10 keys are: team_subs_df_unfiltered, csconsole_action_plans,
    csconsole_customer_pulse, csconsole_success_priorities,
    csconsole_adoption_barriers, software_defects, psirt_vulns,
    partial_data_warnings, data_retrieved_at, days. Drop any one of
    them and the corresponding nested-function read silently
    fallback-defaults instead of using the real outer-scope value.
    """
    ctx_dict = _r23_ctx_dict()
    ctx_keys = {
        key.value
        for key in ctx_dict.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    required_ctx_keys = (
        "team_subs_df_unfiltered",
        "csconsole_action_plans",
        "csconsole_customer_pulse",
        "csconsole_success_priorities",
        "csconsole_adoption_barriers",
        "software_defects",
        "psirt_vulns",
        "partial_data_warnings",
        "data_retrieved_at",
        "days",
    )
    for key in required_ctx_keys:
        assert key in ctx_keys, (
            f"R22-NEXT-001 regression: ``_r23_ctx`` is missing key "
            f"{key} -- the nested ``generate_report`` / "
            f"``generate_excel`` call sites that read this key will "
            f"silently fall back to the default and the corresponding "
            f"outer-scope data (csconsole frames, security data, "
            f"data freshness, analysis horizon) will be dropped from "
            f"the rendered report. Restore the full ctx-dict shape "
            f"introduced in Round 23."
        )


def test_data_retrieved_at_uses_locals_get_at_outer_scope() -> None:
    """``data_retrieved_at`` is the ONE genuinely conditional
    variable: it is only bound in the outer scope inside ``if _drt
    is not None:``, so its key in ``_r23_ctx`` must be populated via
    ``locals().get('data_retrieved_at')`` (which works correctly at
    OUTER scope -- the closure-binding bug only affects nested-fn
    ``locals()`` reads of free variables).
    """
    ctx_dict = _r23_ctx_dict()
    values_by_key = {
        key.value: value
        for key, value in zip(ctx_dict.keys, ctx_dict.values)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    value = values_by_key.get("data_retrieved_at")
    uses_outer_locals_get = (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "get"
        and isinstance(value.func.value, ast.Call)
        and isinstance(value.func.value.func, ast.Name)
        and value.func.value.func.id == "locals"
        and not value.func.value.args
        and len(value.args) == 1
        and isinstance(value.args[0], ast.Constant)
        and value.args[0].value == "data_retrieved_at"
    )
    assert uses_outer_locals_get, (
        "R22-NEXT-001 regression: ``_r23_ctx`` must populate "
        "``data_retrieved_at`` via ``locals().get('data_retrieved_at')`` "
        "at the outer scope. ``data_retrieved_at`` is conditionally "
        "bound (only set inside ``if _drt is not None:``); a plain "
        "``data_retrieved_at`` reference would raise NameError on the "
        "no-Snowflake-data path."
    )


# ---------------------------------------------------------------------------
# R22-NEXT-003  --  end-to-end ``write_summary_sheet`` against the
# multi-source golden fixture
# ---------------------------------------------------------------------------
#
# Round 21.1 hot spot #3 documented that ``write_summary_sheet`` was
# only exercised at the formatter-direct layer with AB+CSOne+pulse
# (no csconsole_* extras), so a closure-binding regression that
# affected the Excel writer's customer-counting could have shipped
# silently. This test closes that gap by driving
# ``write_summary_sheet`` with the full multi-source extras AND the
# 90-day analysis window the closure-binding fix now respects.


@pytest.fixture(scope="module")
def excel_summary_rows_with_extras(tmp_path_factory):
    """Drive write_summary_sheet end-to-end with the multi-source extras.

    Returns a list of ``(label, value)`` string tuples in order so
    individual tests can assert specific cells without re-rendering.
    """
    try:
        import openpyxl  # noqa: F401  -- import check only
        from report_export_styling import write_summary_sheet
    except Exception:
        pytest.skip("report_export_styling or openpyxl unavailable")

    tmp_dir = tmp_path_factory.mktemp("round23_excel_with_extras")
    out_path = tmp_dir / "summary_with_extras.xlsx"

    extras = make_extra_frames()
    csconsole_action_plans = extras[0]
    csconsole_customer_pulse_extra = extras[1]
    csconsole_success_priorities = extras[2]
    csconsole_adoption_barriers = extras[3]

    sheets: Dict[str, Any] = {
        "AB_Detail_All": make_ab_df(),
        "CSOne_Detail_All": make_csone_df(),
        "External_Bugs": pd.DataFrame(),
        "External_Incidents": pd.DataFrame(),
    }

    # csconsole_data is the kwargs bundle write_summary_sheet
    # consumes. We thread the full multi-source universe -- the
    # closure-binding fix's customer-counting now reflects this
    # universe; before the fix, only ``customer_pulse`` (and only on
    # the formatter-direct path) was getting through.
    csconsole_data = {
        "customer_pulse": make_pulse_df(),
        "action_plans": csconsole_action_plans,
        "extra_customer_pulse": csconsole_customer_pulse_extra,
        "success_priorities": csconsole_success_priorities,
        "adoption_barriers": csconsole_adoption_barriers,
    }

    with pd.ExcelWriter(str(out_path), engine="xlsxwriter") as writer:
        wrote = write_summary_sheet(
            writer,
            sheets,
            csconsole_data,
            manager="Manager Round23",
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


def test_generate_excel_window_days_uses_passed_value(
    excel_summary_rows_with_extras,
):
    """``Window (days)`` must reflect the ``days`` arg.

    Pre-fix, the closure-binding bug hardcoded ``recent_window_days``
    to 30 inside ``generate_excel``; while the Excel summary's
    ``Window (days)`` cell flows from a different code path
    (write_summary_sheet's ``days`` kwarg) and so was NOT directly
    affected, this test pins the broader contract that the analysis
    horizon flows end-to-end. If a future edit threads ``days``
    incorrectly anywhere in the Excel pipeline, this test fails.
    """
    by_label = dict(excel_summary_rows_with_extras)
    value = by_label.get("Window (days)")
    assert value == "90", (
        f"Excel summary 'Window (days)' emitted {value!r}; "
        "expected '90' (the days arg passed in the fixture). The "
        "closure-binding fix now threads the analysis horizon "
        "consistently; this cell is the canonical record of it."
    )


def test_generate_excel_manager_scope_uses_passed_value(
    excel_summary_rows_with_extras,
):
    """``Manager scope`` must round-trip the ``manager`` arg unchanged."""
    by_label = dict(excel_summary_rows_with_extras)
    value = by_label.get("Manager scope")
    assert value == "Manager Round23", (
        f"Excel summary 'Manager scope' emitted {value!r}; "
        "expected 'Manager Round23' (the manager arg passed in the "
        "fixture)."
    )


def test_generate_excel_adoption_barriers_total_matches_expected(
    excel_summary_rows_with_extras,
):
    """``Adoption barriers (total)`` must match the canonical fixture."""
    by_label = dict(excel_summary_rows_with_extras)
    value = by_label.get("Adoption barriers (total)")
    assert value == str(EXPECTED_KPIS["total_barriers"]), (
        f"Excel summary 'Adoption barriers (total)' emitted "
        f"{value!r}; expected {EXPECTED_KPIS['total_barriers']}."
    )


def test_generate_excel_tac_cases_total_matches_expected(
    excel_summary_rows_with_extras,
):
    """``TAC cases (total)`` must match the canonical fixture."""
    by_label = dict(excel_summary_rows_with_extras)
    value = by_label.get("TAC cases (total)")
    assert value == str(EXPECTED_KPIS["total_cases"]), (
        f"Excel summary 'TAC cases (total)' emitted {value!r}; "
        f"expected {EXPECTED_KPIS['total_cases']}."
    )


def test_generate_excel_label_order_matches_documented_sequence(
    excel_summary_rows_with_extras,
):
    """The label sequence must match the registry-pinned order even
    when the multi-source extras are threaded through.

    A future edit that re-orders the Excel summary rows (for
    example: moving "Window (days)" to a different position) would
    silently break downstream parsing without this pin.
    """
    actual_labels = tuple(label for label, _ in excel_summary_rows_with_extras)
    expected_labels = EXPECTED_KPIS["excel_summary_label_order"]
    assert actual_labels == expected_labels, (
        f"Excel summary label sequence mismatch.\n"
        f"  actual:   {actual_labels!r}\n"
        f"  expected: {expected_labels!r}"
    )


# ---------------------------------------------------------------------------
# Bonus contract pin: legacy closure-binding markers are gone from the
# nested functions. This complements the R20 marker test by listing
# specific pre-fix shapes that must NOT re-appear.
# ---------------------------------------------------------------------------


_LEGACY_CLOSURE_BINDING_SHAPES = (
    "team_subs_df_unfiltered if 'team_subs_df_unfiltered' in locals() else None",
    "csconsole_action_plans if 'csconsole_action_plans' in locals() else pd.DataFrame()",
    "csconsole_customer_pulse if 'csconsole_customer_pulse' in locals() else pd.DataFrame()",
    "csconsole_success_priorities if 'csconsole_success_priorities' in locals() else pd.DataFrame()",
    "csconsole_adoption_barriers if 'csconsole_adoption_barriers' in locals() else pd.DataFrame()",
)


def test_legacy_closure_binding_shapes_are_gone() -> None:
    """None of the documented pre-fix closure-binding shapes may
    re-appear in the nested-function bodies."""
    src = APP_SIMPLE.read_text(encoding="utf-8")
    for legacy in _LEGACY_CLOSURE_BINDING_SHAPES:
        # The compact-fallback OUTER-scope clean-up at L7356-7359
        # already removed these shapes; if any reappear it means a
        # future edit re-introduced the pattern. Fail loudly.
        # (We tolerate the legacy strings appearing as quoted
        # *literals* inside this test file via the
        # _LEGACY_CLOSURE_BINDING_SHAPES tuple, but that tuple lives
        # in tests/, not in app_simple.py.)
        assert legacy not in src, (
            "R22-NEXT-001 regression: the pre-fix closure-binding "
            f"shape {legacy!r} is back in app_simple.py. Restore "
            "the ``_r23_ctx``-based ctx-dict access introduced in "
            "Round 23."
        )


# ---------------------------------------------------------------------------
# Documented edge: the OUTER scope in run_compact_analysis still has
# a few legitimate ``in locals()`` sites (e.g. ``'cur' in locals()``
# cleanup-after-try discipline). This test ensures we do NOT
# accidentally over-assert "no in locals() anywhere" -- the audit
# floor lives in tests/test_round20_in_locals_simplification.py.
# ---------------------------------------------------------------------------


def test_outer_scope_in_locals_floor_is_pinned_elsewhere() -> None:
    """Cross-reference: the outer-scope ``in locals()`` floor pin
    lives in test_round20_in_locals_simplification.py.

    This empty-body test exists so a grep for "in locals()" in this
    file returns the cross-reference rather than turning up nothing.
    """
    other_test = (
        REPO_ROOT
        / "tests"
        / "test_round20_in_locals_simplification.py"
    )
    assert other_test.exists(), (
        "test_round20_in_locals_simplification.py is missing -- the "
        "outer-scope ``in locals()`` floor pin is not in this file; "
        "removing the cross-referenced test orphans the count "
        "discipline."
    )
    # Also smoke-check that the floor variable is still present.
    other_src = other_test.read_text(encoding="utf-8")
    assert "_R20_IN_LOCALS_FLOOR" in other_src, (
        "test_round20_in_locals_simplification.py no longer pins the "
        "``in locals()`` floor count -- restore it before merging."
    )
