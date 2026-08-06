"""Round 43 / Phase 2 regression test.

Pin the compact path's Excel writer to skip the legacy
``worksheet.autofilter(...)`` call when ``apply_excel_polish`` already
added an Excel Table (which carries its own implicit autofilter).

Pre-fix the compact path at ``app_simple.py:9072..9156`` always ran the
legacy ``worksheet.autofilter(1, 0, len(df_clean), len(df_clean.columns)-1)``
right after ``apply_excel_polish``.  When polish succeeded and added a
Table covering ``A2:F<last_row+1>``, the legacy autofilter declared
``A2:F<n>`` (off by one because ``len(df_clean)`` does not include the
header row), and xlsxwriter raised:

    Worksheet autofilter range 'A2:F53' overlaps previous Table autofilter
    range 'A2:F54'

This killed the build-19 demo compact run
(``Compact_Brian_Frazier_All_Contact_Center_90d_1777428644``).

Round 43 / Phase 2 captures the polish return value and skips the legacy
``autofilter`` when ``table_added`` is True.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"


def _read() -> str:
    return APP_SIMPLE.read_text(encoding="utf-8")


def test_compact_captures_polish_result() -> None:
    """The compact Excel writer MUST capture ``apply_excel_polish``'s return
    value (a dict with ``table_added``) and store the ``table_added`` flag.
    """
    src = _read()
    # Anchor on the Round 43 / Phase 2 polish-result variable.
    assert_in_source(src, "_r43_polish_result", label='src')
    assert_in_source(src, "_r43_polish_added_table", label='src')


def test_compact_legacy_autofilter_is_guarded_by_polish_flag() -> None:
    """The legacy ``worksheet.autofilter(0, 0, len(df_clean), ...)`` call
    that sits right after ``apply_excel_polish`` MUST be wrapped in
    ``if not _r43_polish_added_table:`` so it is skipped when the Table
    already provides an autofilter.  Without this guard, xlsxwriter raises
    the ``Worksheet autofilter range overlaps previous Table autofilter
    range`` error that killed the build-19 demo compact run.

    Round 65 / R-1 update: the autofilter row anchor moved from row 1
    (was under a merged title in row 0) to row 0 (header row 0; no
    title row above).  The ``if not _r43_polish_added_table:`` guard
    is unchanged -- only the absolute row index shifted.

    Note: ``app_simple.py`` has multiple ``worksheet.autofilter`` sites
    -- only the one IMMEDIATELY following ``apply_excel_polish`` needs
    the guard.  This test specifically asserts the polish-adjacent
    site is guarded.
    """
    src = _read()
    pattern = r"worksheet\.autofilter\(0,\s*0,\s*len\(df_clean\),\s*len\(df_clean\.columns\)\s*-\s*1\)"
    matches = list(re.finditer(pattern, src))
    assert matches, (
        "expected at least one legacy autofilter call to still exist; "
        "Round 43 / Phase 2 only adds a guard, it does NOT remove the call. "
        "Round 65 / R-1 changed the anchor from row 1 to row 0 (header at row 0)."
    )
    # At least one of the matches MUST be guarded by the polish flag.
    guarded = [
        m for m in matches
        if "if not _r43_polish_added_table" in src[max(0, m.start() - 600):m.start()]
    ]
    assert guarded, (
        "the polish-adjacent autofilter call must be guarded by "
        "``if not _r43_polish_added_table:`` per Round 43 / Phase 2; "
        "without this guard the build-19 demo Worksheet autofilter overlap "
        "crash returns.  Sites checked: "
        f"{[(m.start(), src[max(0, m.start()-100):m.start()]) for m in matches]}"
    )


def test_polish_return_dict_carries_table_added_key() -> None:
    """Functional check: ``apply_excel_polish`` returns a dict whose
    ``table_added`` key is True when an Excel Table was successfully added.
    Round 43 / Phase 2 relies on this key being present.
    """
    import pandas as pd
    import xlsxwriter
    import tempfile
    import os
    from report_export_styling import apply_excel_polish

    df = pd.DataFrame({"A": [1, 2, 3], "B": ["x", "y", "z"]})
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        wb = xlsxwriter.Workbook(tmp_path)
        ws = wb.add_worksheet("Test_Sheet")
        # Mimic compact's ``startrow=1`` write -- header at row 1, data at rows 2..4.
        ws.write(1, 0, "A")
        ws.write(1, 1, "B")
        for i, row in enumerate(df.itertuples(index=False), start=2):
            ws.write(i, 0, row.A)
            ws.write(i, 1, row.B)
        result = apply_excel_polish(wb, ws, df, "Test_Sheet", set(), startrow=1)
        wb.close()
        assert isinstance(result, dict), "apply_excel_polish must return a dict"
        assert "table_added" in result, (
            "apply_excel_polish return dict must carry a 'table_added' key "
            "(Round 43 / Phase 2 branches on it)."
        )
        assert result["table_added"] is True, (
            "on a healthy 2-col / 3-row DataFrame, apply_excel_polish should "
            "successfully add a Table; if this fails, the contract Round 43 / "
            "Phase 2 relies on has changed."
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
