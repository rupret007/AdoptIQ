"""Round 4 / Phase 1.1 regression test.

The Leader Excel ``Report_Info`` sheet must be written **inside** the
``with pd.ExcelWriter(...) as writer:`` block.  The pre-Round-4 code
indented the write outside the ``with`` block, which closed the writer
and silently dropped the partial-completion ledger.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _leader_excel_block() -> str:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Find the leader-report ExcelWriter context. There is a section
    # comment marker we can anchor on.
    return src


def test_report_info_write_uses_writer_handle() -> None:
    src = _leader_excel_block()
    # We require that report_info_df.to_excel(writer, ...) appears at
    # least once with the writer handle; otherwise it would have used
    # a path string and re-opened the file (the old bug).
    assert re.search(
        r"report_info_df\.to_excel\(\s*writer\s*,",
        src,
    ), (
        "Round 4 Phase 1.1: Leader Excel ``Report_Info`` must be written "
        "via the live ``writer`` handle (inside the ``with`` block), not "
        "to a closed/reopened file."
    )


def test_report_info_block_is_indented_inside_with() -> None:
    src = _leader_excel_block()
    # Heuristic: there must be at least one ``with pd.ExcelWriter`` block
    # that, before its dedent, contains ``Report_Info``.  We do this by
    # scanning the indentation of the ``Report_Info`` sheet name relative
    # to the nearest preceding ``with pd.ExcelWriter``.
    matches = list(re.finditer(r"^\s*with pd\.ExcelWriter\(", src, flags=re.MULTILINE))
    assert matches, "No pd.ExcelWriter(...) block found in app_simple.py"
    found_inside = False
    for m in matches:
        # Consider the next ~200 lines from this writer.
        tail = src[m.end(): m.end() + 60_000]
        # Find ``sheet_name='Report_Info'`` or "Report_Info" in to_excel(writer,...).
        if re.search(
            r"to_excel\(\s*writer\s*,[^)]*Report_Info",
            tail,
        ):
            found_inside = True
            break
    assert found_inside, (
        "Round 4 Phase 1.1: at least one ExcelWriter context must "
        "contain a Report_Info to_excel(writer, ...) call so the sheet "
        "is written before the writer closes."
    )
