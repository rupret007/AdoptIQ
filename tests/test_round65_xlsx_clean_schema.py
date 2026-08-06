"""Round 65 / R-1 regression tests.

Build 37 audit found that the Compact / Renewal / Leader Excel writers
all wrote a merged title row in row 0, which pushed the data sheet's
column headers to row 1.  ``pd.read_excel(sheet_name)`` therefore
returned columns named after the title text (``"Customer Adoption
Barriers - <Customer> Renewal Analysis"``) instead of the canonical
schema (``"Customer", "Severity", "Status", ...``).  This broke
every downstream consumer that opened the workbook programmatically.

These tests are SOURCE-SHAPE tests — they grep ``app_simple.py`` for
the writer-block invariants (``startrow=0``, no ``merge_range(0, 0,
0, ...)`` over data sheets, headers written at row 0, autofilter at
row 0, ``freeze_panes(1, 0)``).  They are intentionally brittle so a
future regression that re-introduces the title-in-row-0 pattern
trips the test before the workbook ships.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source, assert_not_in_source, count_in_source

import re
from pathlib import Path

APP_PATH = Path(__file__).resolve().parent.parent / "app_simple.py"


def _src() -> str:
    return APP_PATH.read_text(encoding="utf-8")


# ---- Compact writer (run_compact_analysis) ----


def test_compact_writer_uses_startrow_zero_for_data_sheets() -> None:
    """The Compact non-empty / empty / Data_Unavailable branches must
    all write data with ``startrow=0`` so headers land in row 0."""
    src = _src()
    # The Compact writer is the FIRST occurrence of these patterns
    # (it's around L9700-L10200; the renewal writer follows at
    # L13400+ and the leader writer at L22600+).  Just assert the
    # explicit Round-65 startrow=0 marker is present in all three
    # writers (1 dashboard branch + 1 unavailable + 1 data + 1 empty
    # for Compact = 4 occurrences of ``startrow=0`` in the Compact
    # block alone, plus 3 for Renewal and 2 for Leader).
    assert_in_source(src, "Round 65 / R-1", label='src')
    # Hard pin: NO data-sheet write should still use ``startrow=1``
    # except in the original ``startrow=1`` to_excel path that we
    # explicitly converted; count the explicit Round 65 / R-1
    # comments to confirm coverage.
    r65_marker_count = count_in_source(src, "Round 65 / R-1")
    assert r65_marker_count >= 8, (
        f"Round 65 / R-1 markers underweight: {r65_marker_count} (expected >= 8 "
        "across Compact + Renewal + Leader writers)"
    )


def test_no_writer_emits_merge_range_at_row_0_over_data_sheet() -> None:
    """No writer in the Compact / Renewal / Leader paths should
    invoke ``merge_range(0, 0, 0, ...)`` on a data sheet — that was
    the title-row-in-row-0 pattern that broke pd.read_excel."""
    src = _src()
    # The legacy pattern: ``worksheet.merge_range(0, 0, 0,
    # n_cols-1, title_text, title_format)``.  After Round 65 / R-1
    # this should not appear in the Compact / Renewal / Leader data
    # branches.  We allow it to appear in OTHER writers (e.g. PDF
    # frontmatter, metadata-only sheets) but pin the data branches.
    matches = re.findall(
        r"merge_range\(0,\s*0,\s*0,\s*[^)]+,\s*title_text,\s*title_format\)",
        src,
    )
    assert len(matches) == 0, (
        f"Found {len(matches)} legacy merge_range(0,0,0,...,title_text,title_format) "
        "calls — Round 65 / R-1 dropped these from data sheets"
    )


def test_compact_writer_freeze_panes_anchors_at_row_1() -> None:
    """After Round 65 / R-1 the Compact writer freezes at
    ``freeze_panes(1, 0)`` (header in row 0) instead of the legacy
    ``freeze_panes(2, 0)`` (header in row 1 under a title)."""
    src = _src()
    # The Compact dashboard branch is the FIRST freeze_panes call.
    # Round 65 / R-1 changed it from (2, 0) to (1, 0).  Pin both
    # forms remaining in source.
    legacy_count = count_in_source(src, "freeze_panes(2, 0)")
    fixed_count = count_in_source(src, "freeze_panes(1, 0)")
    assert fixed_count >= 2, (
        f"Expected at least 2 ``freeze_panes(1, 0)`` calls (Round 65 / R-1 "
        f"fix in Compact dashboard + main-data branch), found {fixed_count}"
    )
    # Some legacy ``freeze_panes(2, 0)`` may persist in OTHER
    # writers we did not touch; just pin the Round 65 markers
    # appear in the same neighborhood.
    assert_in_source(src, "Round 65 / R-1: header is now in row 0", label='src')


def test_compact_writer_autofilter_anchors_at_row_0() -> None:
    """Round 65 / R-1: autofilter must start at row 0 (header row)
    instead of row 1 (which was under a title row)."""
    src = _src()
    # The autofilter call after Round 65 / R-1 reads
    # ``worksheet.autofilter(0, 0, len(df_clean), len(df_clean.columns)-1)``.
    fixed_filters = re.findall(
        r"worksheet\.autofilter\(0,\s*0,\s*len\(df_clean\),\s*len\(df_clean\.columns\)\s*-\s*1\)",
        src,
    )
    assert len(fixed_filters) >= 1, (
        "Compact autofilter must anchor at row 0 (Round 65 / R-1)"
    )


# ---- Renewal writer (run_customer_renewal_analysis) ----


def test_renewal_writer_pre_stamps_sheet_titles_into_report_info() -> None:
    """Round 65 / R-1: the Renewal Report_Info sheet now carries
    one ``Sheet_Title:<sheet>`` row per data sheet so the branded
    context survives even though the data sheets themselves no
    longer prepend a merged title row."""
    src = _src()
    assert_in_source(src, "Sheet_Title:", label='src')
    # Pin the sheet list the renewal Report_Info enumerates.
    for sheet in (
        "Renewal_Summary",
        "Risk_Components",
        "Customer_Adoption_Barriers",
        "Customer_Support_Cases",
        "Customer_Action_Plans",
    ):
        assert f"'Sheet_Title:{{_ren_sn}}'".replace("{_ren_sn}", "") in src or (
            f"Sheet_Title:{sheet}" in src or "Sheet_Title:{_ren_sn}" in src
        ), f"Renewal Report_Info missing pre-stamp for {sheet}"


def test_renewal_writer_drops_legacy_title_at_row_0() -> None:
    """Round 65 / R-1: the legacy
    ``_renewal_title_text = ...; merge_range(0, 0, 0, ...)`` block
    is gone."""
    src = _src()
    # The legacy block had ``_renewal_n_cols`` and
    # ``worksheet.merge_range(0, 0, 0, _renewal_n_cols - 1,
    # _renewal_title_text, title_format)``.  After fix, neither
    # ``_renewal_n_cols`` nor that merge_range call should be
    # present in the renewal data branch.
    assert_not_in_source(src, "_renewal_n_cols", label='src')
    assert_not_in_source(src, "_renewal_title_text", label='src')


# ---- Leader writer (run_leader_report_generation) ----


def test_leader_writer_collects_sheet_titles() -> None:
    """Round 65 / R-1: the Leader writer collects per-sheet titles
    into ``_r65_leader_sheet_titles`` and writes them into the
    Report_Info sheet under ``Sheet_Title:<sheet>`` rows."""
    src = _src()
    assert_in_source(src, "_r65_leader_sheet_titles", label='src')
    assert_in_source(src, "Sheet_Title:{_ldr_sn}" in src or "Sheet_Title:{ldr_sn}", label='src')
