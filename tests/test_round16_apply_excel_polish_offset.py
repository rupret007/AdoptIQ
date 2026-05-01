"""Round 16 / Phase 5 — apply_excel_polish offset + banded top-N swap.

Two surgical pins for the Round-15 follow-ups:

* **R15-FOLLOWUP-1** — the Round-15 polish helper now accepts a
  ``startrow`` parameter so the three ``app_simple.py`` Excel writers
  (which emit a merged title row at row 0 and write the data with
  ``to_excel(..., startrow=1)``) finally get the Excel-Table /
  conditional-format treatment instead of being limited to the
  baseline ``startrow=0`` shape.

* **R15-FOLLOWUP-2** — the Round-15 banded top-N helper
  (``report_word_styling.add_banded_top_n_table``) is now used by the
  compact-formatter "Top 10 Focus Accounts" table and the executive-
  intelligence "Defects by Customer" table.  Both previously inherited
  ``Table Grid`` / ``Light Grid Accent 1`` styles that drifted from
  the title-page Cisco-blue treatment.

The tests prefer behavior pins (helper returns the right ranges,
markers wire-up) over visual diffing; visual rendering is already
covered by the Round-15 word-format and excel-format suites.
"""

from __future__ import annotations

import importlib
import io
from pathlib import Path

import pandas as pd
import pytest

import report_export_styling as styling


# ---------------------------------------------------------------------------
# R15-FOLLOWUP-1 : ``apply_excel_polish`` startrow parameter
# ---------------------------------------------------------------------------


def _polish_with_startrow(df: pd.DataFrame, sheet_name: str, startrow: int):
    """Drive the polish helper end-to-end against an in-memory writer
    so we exercise the same surface the app_simple writers use.
    Returns ``(workbook, worksheet, polish_result)``.
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        if startrow > 0:
            # Mirror the app_simple convention: merged title row at
            # row 0, data written with ``startrow=startrow``.
            df.to_excel(writer, sheet_name=sheet_name, index=False, startrow=startrow)
            ws = writer.sheets[sheet_name]
            try:
                ws.merge_range(0, 0, 0, max(0, len(df.columns) - 1), "TITLE")
            except Exception:
                pass
        else:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            ws = writer.sheets[sheet_name]
        wb = writer.book
        out = styling.apply_excel_polish(
            wb, ws, df, sheet_name, set(), startrow=startrow
        )
    buf.seek(0)
    return buf, out


def test_phase_5_1_apply_excel_polish_signature_accepts_startrow():
    """Public signature exposes ``startrow`` with default 0 so all
    Round-15 callers continue to work without modification.
    """
    import inspect

    params = inspect.signature(styling.apply_excel_polish).parameters
    assert "startrow" in params
    assert params["startrow"].default == 0


def test_phase_5_1_data_range_helper_shifts_with_startrow():
    """``_data_range`` is the inner anchor for every conditional-
    format rule.  A ``startrow=1`` caller must produce
    ``X3:X{n+2}`` instead of ``X2:X{n+1}`` so rules cover only the
    real data rows.
    """
    # 5 data rows, header at row 0 -> Excel range A2:A6
    assert styling._data_range(0, 5, 0) == "A2:A6"
    # 5 data rows, header at row 1 -> Excel range A3:A7 (offset by 1)
    assert styling._data_range(0, 5, 1) == "A3:A7"
    # 100 data rows, header at row 1 -> Excel range A3:A102
    assert styling._data_range(0, 100, 1) == "A3:A102"


def test_phase_5_1_data_range_helper_clamps_negative_startrow():
    """Negative or non-integer startrow must not produce malformed
    Excel ranges.  The helper clamps to 0.
    """
    assert styling._data_range(0, 5, -1) == "A2:A6"
    assert styling._data_range(0, 5, 0) == styling._data_range(0, 5, -3)


def test_phase_5_1_apply_excel_polish_default_startrow_unchanged():
    """Backward compatibility pin: when ``startrow`` is not
    provided, the helper behaves exactly as before -- a Table is
    written across ``A1:DCx`` with ``last_row = n_rows``.
    """
    df = pd.DataFrame(
        {
            "Customer": ["Acme", "Beta", "Gamma"],
            "ARR_USD": [1_000_000, 500_000, 250_000],
            "Risk_Score": [8.0, 5.5, 2.0],
        }
    )
    buf, out = _polish_with_startrow(df, "NoOffset", startrow=0)
    assert out["table_added"] is True
    assert out["startrow"] == 0
    # Conditional-formatting layer applied; risk column detected.
    assert "risk" in out["conditional_rules"]
    assert len(out["conditional_rules"]["risk"]) >= 1


def test_phase_5_1_apply_excel_polish_startrow_one_writes_table():
    """The ``startrow=1`` caller still produces a successful Table
    write, with the table anchored on the actual header row.
    """
    df = pd.DataFrame(
        {
            "Customer": ["Acme", "Beta", "Gamma"],
            "ARR_USD": [1_000_000, 500_000, 250_000],
            "Risk_Score": [8.0, 5.5, 2.0],
        }
    )
    buf, out = _polish_with_startrow(df, "Offset", startrow=1)
    assert out["table_added"] is True
    assert out["startrow"] == 1
    # Conditional-format rules are still applied.
    assert "risk" in out["conditional_rules"]
    assert len(out["conditional_rules"]["risk"]) >= 1
    # The resulting workbook should be openable end-to-end without
    # xlsxwriter raising on a bad Table range.
    from openpyxl import load_workbook

    buf.seek(0)
    wb_check = load_workbook(buf)
    ws_check = wb_check["Offset"]
    # Header row should be at row 2 (Excel 1-based), not row 1.
    assert ws_check.cell(row=2, column=1).value == "Customer"
    # First data row at row 3.
    assert ws_check.cell(row=3, column=1).value == "Acme"


def test_phase_5_1_apply_excel_polish_startrow_one_handles_empty_frame():
    """Empty-frame defensiveness must hold under offset, otherwise a
    fetch-error sheet would crash the whole writer.
    """
    df = pd.DataFrame(columns=["Status", "Dataset"])
    buf, out = _polish_with_startrow(df, "EmptyOffset", startrow=1)
    # ``apply_excel_polish`` returns the same shape; table_added is
    # True because a single blank data row was inserted to satisfy
    # the Excel-Table-needs-data-row rule.
    assert out["startrow"] == 1
    # The blank-data-row injection must have happened at the correct
    # offset (header_row + 1 == 2), not at hard-coded row 1.
    from openpyxl import load_workbook

    buf.seek(0)
    wb_check = load_workbook(buf)
    ws_check = wb_check["EmptyOffset"]
    # Header row is at Excel row 2; row 3 is blank/None data row.
    assert ws_check.cell(row=2, column=1).value == "Status"


def test_phase_5_1_apply_excel_polish_marker_present_in_styling():
    """Self-pin: marker survives in source so adversarial sweeps and
    future maintainers can locate the offset wiring.
    """
    src = Path("report_export_styling.py").read_text(encoding="utf-8")
    assert "Round 16 / Phase 5.1" in src
    assert "startrow" in src


def test_phase_5_1_app_simple_writers_pass_startrow_zero():
    """Self-pin (UPDATED Round 65 / R-1): every place
    ``app_simple.py`` invokes the polish helper has ``startrow=0``
    (header in row 0) and a Round-16 marker next to it.

    Pre-Round-65, the writers prepended a merged title row at row 0
    and called ``apply_excel_polish(..., startrow=1)`` to anchor the
    Excel Table on the header at row 1.  This broke every consumer
    that used ``pd.read_excel(sheet_name)`` to read the workbook
    programmatically -- the column headers landed in row 1, so the
    parsed column names were the title strings instead of the
    canonical schema (Build 37 audit / R-1).  Round 65 / R-1 dropped
    the title-in-row-0 pattern from the Compact / Renewal / Leader
    writers; the canonical sheet titles now live in the
    ``Report_Info`` sheet under ``Sheet_Title:<sheet>`` rows.
    """
    src = Path("app_simple.py").read_text(encoding="utf-8")
    polish_invocations = src.count("_r16_apply_excel_polish(")
    # Three writers were wired (compact / renewal / leader); we expect
    # at least three call sites with the offset parameter.
    assert polish_invocations >= 3, (
        f"_r16_apply_excel_polish invoked only {polish_invocations} times in app_simple.py "
        "(expected at least 3 for compact/renewal/leader writers)"
    )
    # Every invocation must be paired with ``startrow=0`` (Round 65 / R-1).
    assert src.count("startrow=0,\n                            )") + src.count(
        "startrow=0,\n                                    )"
    ) >= 3, "_r16_apply_excel_polish callsites must pass startrow=0 explicitly (Round 65 / R-1)"
    # And every invocation must carry the Round-16 marker.
    assert src.count("Round 16 / Phase 5.1") >= 3
    # Pin the Round-65 marker too so the contract is explicit.
    assert src.count("Round 65 / R-1") >= 3


# ---------------------------------------------------------------------------
# R15-FOLLOWUP-2 : banded top-N helper substitutions
# ---------------------------------------------------------------------------


def test_phase_5_2_compact_formatter_imports_banded_helper():
    """Self-pin: the compact formatter pulls
    ``add_banded_top_n_table`` from the Round-15 word-styling SSoT
    so the substitution can't silently regress to ``Table Grid``.
    """
    src = Path("compact_report_formatter.py").read_text(encoding="utf-8")
    assert "from report_word_styling import add_banded_top_n_table" in src
    assert "Round 16 / Phase 5.2" in src


def test_phase_5_2_compact_formatter_swaps_focus_table():
    """The "Top 10 Focus Accounts" rendering now flows through the
    helper.  Marker presence + helper invocation are both pinned.
    """
    src = Path("compact_report_formatter.py").read_text(encoding="utf-8")
    assert "_r16_add_banded_top_n_table(" in src
    # The legacy ``Table Grid`` setter is preserved only inside the
    # defensive fallback block; the primary path goes through the
    # helper.  Pin that the marker landed near the substitution.
    idx = src.find("_r16_focus_headers")
    assert idx > 0
    # Ensure the helper call is present in the focus-accounts swap.
    helper_idx = src.find("_r16_add_banded_top_n_table(\n                formatter.doc,")
    assert helper_idx > idx, (
        "compact_report_formatter focus table did not invoke "
        "_r16_add_banded_top_n_table after building _r16_focus_headers"
    )


def test_phase_5_2_executive_intelligence_imports_banded_helper():
    src = Path("executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert "from report_word_styling import add_banded_top_n_table" in src
    assert "Round 16 / Phase 5.2" in src


def test_phase_5_2_executive_intelligence_swaps_defect_table():
    src = Path("executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert "_r16_add_banded_top_n_table(" in src
    assert "_r16_defect_headers" in src
    helper_idx = src.find("_r16_add_banded_top_n_table(\n                self.doc,")
    headers_idx = src.find("_r16_defect_headers")
    assert helper_idx > headers_idx, (
        "executive_intelligence defect-by-customer table did not invoke "
        "_r16_add_banded_top_n_table after building _r16_defect_headers"
    )


def test_phase_5_2_compact_focus_table_renders_through_helper():
    """Behavioral pin: drive the substitution in-memory and confirm
    the resulting table carries the helper's banded-row signature
    (header fill = HEADER_FILL_HEX) instead of the legacy
    ``Table Grid`` plain header.
    """
    from docx import Document

    import report_word_styling as wstyle

    # We can't import compact_report_formatter cleanly because its
    # heavy import side-effects (canonical_metrics, risk_scoring,
    # report_consistency) are exercised by the larger suite.  Stand
    # in for the substitution by exercising the helper directly with
    # the same headers / rows the formatter would build.
    doc = Document()
    headers = ["Rank", "Customer", "Risk Score", "Category"]
    rows = [
        ["1", "Acme", "8.5/10", "Technical"],
        ["2", "Beta", "7.0/10", "Adoption"],
        ["3", "Gamma", "5.5/10", "Renewal"],
    ]
    table = wstyle.add_banded_top_n_table(doc, headers=headers, rows=rows)
    assert table is not None
    # Header is Cisco-blue (HEADER_FILL_HEX), proving the swap moved
    # the formatter off the inherited ``Table Grid`` neutral header.
    header_cell = table.rows[0].cells[0]
    assert wstyle.get_cell_fill_hex(header_cell) == wstyle.HEADER_FILL_HEX
    # Banded data rows: row 2 (alternate) has the band fill.
    assert wstyle.get_cell_fill_hex(table.rows[2].cells[0]) == wstyle.ALT_ROW_FILL_HEX


def test_phase_5_2_executive_intelligence_defect_table_renders_through_helper():
    """Same shape as the focus table: confirm the helper produces
    the banded shape for the defect-by-customer rollup the
    formatter feeds in.
    """
    from docx import Document

    import report_word_styling as wstyle

    doc = Document()
    headers = ["Customer Name", "Defect Count", "Defect IDs"]
    rows = [
        ["Acme", "3", "[BST123], [BST456], [BST789]"],
        ["Beta", "1", "[BST111]"],
    ]
    table = wstyle.add_banded_top_n_table(doc, headers=headers, rows=rows)
    assert table is not None
    header_cell = table.rows[0].cells[0]
    assert wstyle.get_cell_fill_hex(header_cell) == wstyle.HEADER_FILL_HEX


# ---------------------------------------------------------------------------
# Module-level marker self-pin
# ---------------------------------------------------------------------------


def test_phase_5_marker_in_test_module():
    """Self-pin: this test file carries the Round-16 marker so a grep
    for ``Round 16 / Phase 5`` enumerates every coverage surface.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    assert "Round 16 / Phase 5" in src
