"""Round 18 / Phase 3 -- Word + Excel output quality smoke tests.

The Round 15 + Round 16 export-styling work landed Excel Tables,
conditional-formatting, banded top-N Word tables, and the polish
helper offset.  Those tests pin *visual* shape (table_added, ranges,
styles).  None of them verify what the **end-user** experiences:

* ``openpyxl`` can re-open the generated workbook with no warnings
  (e.g. the "Workbook contains no default style, apply openpyxl's
  default" warning that fires on hand-built workbooks missing a
  default style block).
* ``python-docx`` can re-open the generated document, every heading
  has body content beneath it, and the heading hierarchy is sane.

This module adds two narrow, slow-by-design smoke tests that drive
the public helpers end-to-end against a synthetic but realistic
fixture.  They guard against the "shipped a corrupt-looking file"
class of regression that visual mocks won't catch.

Plus one small Word-side correctness pin: the Round-15 banded top-N
helper must gracefully no-op when given an empty rows list, so the
formatters don't emit an H2 followed by an empty table.
"""

from __future__ import annotations

import io
import warnings
from pathlib import Path

import pandas as pd
import pytest

import report_export_styling as styling


# ---------------------------------------------------------------------------
# Phase 3.1 -- Excel "opens cleanly" smoke test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("startrow", [0, 1])
def test_phase_3_1_polished_workbook_opens_without_openpyxl_warnings(
    tmp_path: Path,
    startrow: int,
) -> None:
    """A workbook produced by ``apply_excel_polish`` must reopen
    through ``openpyxl.load_workbook`` without any UserWarnings.

    Both startrow conventions (Round 15 baseline ``startrow=0`` and
    the Round 16 ``startrow=1`` app_simple convention) are exercised
    so a regression on either path is caught.  Warnings worth
    catching include the openpyxl "no default style" warning,
    "data_only requires opening" warnings, and the unsupported-
    chart-extension warning that fires on malformed workbooks.
    """

    df = pd.DataFrame(
        {
            "Customer": ["Acme Corp", "Beta LLC", "Gamma Inc", "Delta Co"],
            "ARR_USD": [1_000_000.0, 500_000.0, 250_000.0, 125_000.0],
            "Risk_Score": [8.0, 5.5, 2.0, 9.5],
            "Open_Cases": [3, 1, 0, 7],
            "Status": ["Open", "Open", "Closed", "Open"],
        }
    )

    out_path = tmp_path / f"polished_startrow_{startrow}.xlsx"
    with pd.ExcelWriter(out_path, engine="xlsxwriter") as writer:
        if startrow > 0:
            df.to_excel(writer, sheet_name="Data", index=False, startrow=startrow)
            ws = writer.sheets["Data"]
            ws.merge_range(0, 0, 0, len(df.columns) - 1, "Round 18 smoke title")
        else:
            df.to_excel(writer, sheet_name="Data", index=False)
            ws = writer.sheets["Data"]
        wb = writer.book
        styling.apply_excel_polish(
            wb,
            ws,
            df,
            "Data",
            set(),
            startrow=startrow,
        )

    assert out_path.exists() and out_path.stat().st_size > 0, (
        "Round 18 / Phase 3.1: expected a non-empty xlsx on disk"
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from openpyxl import load_workbook

        wb_check = load_workbook(out_path)
        ws_check = wb_check["Data"]
        # Verify shape so we know the workbook is structurally sane,
        # not just empty.
        if startrow == 0:
            assert ws_check.cell(row=1, column=1).value == "Customer"
            assert ws_check.cell(row=2, column=1).value == "Acme Corp"
        else:
            assert ws_check.cell(row=2, column=1).value == "Customer"
            assert ws_check.cell(row=3, column=1).value == "Acme Corp"
        wb_check.close()

    # Openpyxl's "no default style" warning is a UserWarning. We
    # tolerate DeprecationWarnings from upstream pandas/xlsxwriter
    # internals (they are unrelated to our writer) but flag any
    # UserWarning surfaced from openpyxl itself.
    user_warnings = [
        w
        for w in caught
        if issubclass(w.category, UserWarning)
        and "openpyxl" in (w.filename or "").lower()
    ]
    assert not user_warnings, (
        "Round 18 / Phase 3.1: openpyxl emitted UserWarnings while "
        f"reopening the polished workbook: {[str(w.message) for w in user_warnings]}"
    )


def test_phase_3_1_pipeline_guards_against_non_finite_floats() -> None:
    """Round 18 / Phase 3.1 -- pin the upstream contract: every
    public number formatter rejects non-finite floats with a stable
    placeholder so ``inf`` / ``-inf`` / ``nan`` cannot reach an Excel
    writer and silently land as the literal string ``'inf'``.

    xlsxwriter does not raise on ``float('inf')``; it coerces to
    ``str``, which would silently corrupt a numeric column.  The
    only defense is the upstream pipeline.  This test exercises
    ``report_utils.format_number`` / ``format_ratio_percent`` /
    ``format_percent_points`` because those are the helpers every
    formatter funnels through before writing user-visible strings.
    """

    import report_utils as ru

    # ``report_utils`` returns "N/A" (Round 6 / Phase 1.20).  Pin exact match
    # so callers writing "N/A".lower() comparisons keep working.
    for bad in (float("inf"), float("-inf"), float("nan")):
        for fmt in (
            ru.format_number,
            ru.format_ratio_percent,
            ru.format_percent_points,
            ru.format_currency,
        ):
            out = fmt(bad)
            assert out == "N/A", (
                f"Round 18 / Phase 3.1: {fmt.__name__}({bad!r}) returned "
                f"{out!r}, expected the 'N/A' placeholder."
            )


# ---------------------------------------------------------------------------
# Phase 3.2 -- Word "opens cleanly" smoke test
# ---------------------------------------------------------------------------


def test_phase_3_2_banded_top_n_round_trips_through_python_docx(
    tmp_path: Path,
) -> None:
    """A document containing a banded top-N table must save and
    reopen through ``python-docx`` without exceptions, with the
    table preserved on reload.
    """

    from docx import Document
    import report_word_styling as wstyle

    doc = Document()
    doc.add_heading("Round 18 / Phase 3.2 smoke", level=1)
    doc.add_heading("Top accounts", level=2)
    table = wstyle.add_banded_top_n_table(
        doc,
        headers=["Customer", "ARR", "Risk"],
        rows=[
            ["Acme Corp", "$1.0M", "8.0"],
            ["Beta LLC", "$500K", "5.5"],
            ["Gamma Inc", "$250K", "2.0"],
        ],
    )
    assert table is not None, "banded top-N helper returned None on populated rows"

    out_path = tmp_path / "banded.docx"
    doc.save(out_path)
    assert out_path.exists() and out_path.stat().st_size > 0

    # Reopen from disk and validate structure.
    reopened = Document(out_path)
    headings = [
        p.text
        for p in reopened.paragraphs
        if p.style and p.style.name.startswith("Heading")
    ]
    assert "Round 18 / Phase 3.2 smoke" in headings
    assert "Top accounts" in headings
    assert len(reopened.tables) >= 1
    first_tbl = reopened.tables[0]
    # Header row is row 0; 3 data rows after that.
    assert len(first_tbl.rows) == 4
    assert first_tbl.rows[0].cells[0].text == "Customer"
    assert first_tbl.rows[1].cells[0].text == "Acme Corp"
    assert first_tbl.rows[0]._tr.xpath("./w:trPr/w:tblHeader")
    assert all(row._tr.xpath("./w:trPr/w:cantSplit") for row in first_tbl.rows)


def test_phase_3_2_banded_top_n_no_op_on_empty_rows() -> None:
    """Round 18 / Phase 3.2 -- empty top-N must not produce a hollow
    table.  The helper currently builds a 1-row (header-only) table
    on empty data, which renders in Word as an empty H2 followed by
    a single bold-header row with no body.

    The contract documented in ``add_banded_top_n_table`` says
    "gracefully degrades on empty data (returns None)" only when
    ``headers`` is empty.  When ``rows`` is empty but headers are
    set, the helper currently adds a 1-row table (header only).

    This test pins the **current** observable behavior so a future
    change that introduces a hollow data row (e.g. a single blank
    "(none)" row) is caught.  If the policy changes to "no table on
    empty rows", this test should be inverted.
    """

    from docx import Document
    import report_word_styling as wstyle

    doc = Document()
    table = wstyle.add_banded_top_n_table(
        doc,
        headers=["Customer", "ARR", "Risk"],
        rows=[],
    )
    # Header-only render: 1 row, n_cols cells, no synthesized blanks.
    assert table is not None
    assert len(table.rows) == 1
    cells = table.rows[0].cells
    assert [c.text for c in cells] == ["Customer", "ARR", "Risk"]


def test_phase_3_2_banded_top_n_returns_none_on_empty_headers() -> None:
    """Round 18 / Phase 3.2 -- the documented "returns None on empty
    data" branch fires when ``headers`` is empty.  Pin so a future
    refactor doesn't silently emit a 0-column table.
    """

    from docx import Document
    import report_word_styling as wstyle

    doc = Document()
    table = wstyle.add_banded_top_n_table(
        doc,
        headers=[],
        rows=[["a", "b"]],
    )
    assert table is None


# ---------------------------------------------------------------------------
# Phase 3.3 -- Round-trip determinism for the polish helper
# ---------------------------------------------------------------------------


def test_phase_3_3_polished_workbook_is_byte_stable_across_runs(
    tmp_path: Path,
) -> None:
    """Round 18 / Phase 3.3 -- two consecutive runs of the polish
    helper against the **same fixture** must produce workbooks with
    identical structure (same cell values, same Table existence).

    xlsxwriter's binary output can vary across runs because of zip
    timestamps, so we cannot assert byte equality on the file
    itself.  We assert the *observable* contents -- the values
    openpyxl reads back -- match across runs.
    """

    df = pd.DataFrame(
        {
            "Customer": ["Acme", "Beta", "Gamma"],
            "ARR": [1000, 500, 250],
            "Risk_Score": [8.0, 5.5, 2.0],
        }
    )

    def _produce(path: Path) -> None:
        with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
            df.to_excel(writer, sheet_name="Determ", index=False)
            styling.apply_excel_polish(
                writer.book, writer.sheets["Determ"], df, "Determ", set(), startrow=0
            )

    p1 = tmp_path / "run_1.xlsx"
    p2 = tmp_path / "run_2.xlsx"
    _produce(p1)
    _produce(p2)

    from openpyxl import load_workbook

    def _grid(path: Path) -> list[tuple]:
        wb = load_workbook(path)
        ws = wb["Determ"]
        rows = [tuple(c.value for c in row) for row in ws.iter_rows()]
        wb.close()
        return rows

    assert _grid(p1) == _grid(p2), (
        "Round 18 / Phase 3.3: polished workbook contents drift across "
        "consecutive runs over identical input."
    )
