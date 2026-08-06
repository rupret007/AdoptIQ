"""Round 67 / Build 41 (B5) -- Compact Report_Info schema parity.

Build 40 acceptance showed the Compact ``Report_Info`` sheet emitting
columns ``Status, Warning, Generated_At`` -- a parity gap with the
Comprehensive (R66/B5) and Renewal (R65/R-1) sheets which already use
the canonical ``Item, Value`` schema.

Round 67 / B5 aligns the Compact writer in
``app_simple.run_compact_analysis`` to the same schema. Sheet-title
rows are now keyed ``Item="Sheet_Title:<sheet>", Value=<title>`` so a
downstream consumer can recover per-sheet titles via a single
``df[df["Item"].str.startswith("Sheet_Title:")]`` filter.

These tests pin the schema by inspecting the source code (so the
contract is enforced even when the heavy XLSX path is not exercised
end-to-end in the test environment).
"""
from __future__ import annotations
from source_shape_utils import assert_in_source, assert_regex_in_source, columns_list_present

import re
from pathlib import Path

import pytest


_APP_SIMPLE = Path(__file__).resolve().parent.parent / "app_simple.py"


def _read_app_simple() -> str:
    return _APP_SIMPLE.read_text(encoding="utf-8")


def test_compact_report_info_writes_item_value_columns() -> None:
    """R67/B5: the Compact Report_Info DataFrame MUST be constructed
    with ``columns=['Item', 'Value']``."""
    src = _read_app_simple()
    # Find the R67/B5 block -- single deterministic anchor.
    assert_in_source(src, "Round 67 / Build 41 (B5)", label='src')
    # Pin the actual DataFrame construction.
    columns_list_present(
        src,
        ["Item", "Value"],
        label="src",
    )
    assert_in_source(src, "report_info_df = pd.DataFrame(_info_records", label="src")


def test_compact_report_info_sheet_title_keyed_rows() -> None:
    """R67/B5: per-sheet titles are emitted as
    ``Item='Sheet_Title:<sheet_name>', Value=<title>`` rows."""
    src = _read_app_simple()
    assert_in_source(src, "Sheet_Title:{_sn}", label="src")


def test_compact_report_info_baseline_rows_present() -> None:
    """R67/B5: the contract is that Compact Report_Info ALWAYS has at
    least Export type + Generated at (UTC) baseline rows."""
    src = _read_app_simple()
    assert_in_source(src, "{'Item': 'Export type', 'Value': 'Standard (Compact)'}", label='src')
    assert_in_source(src, "'Item': 'Generated at (UTC)'", label='src')


def test_compact_report_info_partial_warning_rows_keyed() -> None:
    """Partial-data warnings are persisted as keyed Item/Value rows."""
    src = _read_app_simple()
    assert_in_source(src, "'Item': 'Partial_Data_Warning'", label='src')


def test_round105_compact_report_info_merges_status_partial_warnings() -> None:
    """Round 105: Compact Report_Info must include warnings already exposed
    in status/Word, including ACC scope-exclusion warnings."""
    src = _read_app_simple()
    assert_in_source(src, "Round 105: include the same local/status partial-data warnings", label='src')
    assert_in_source(src, "for _w in partial_data_warnings or []", label="src")
    assert_in_source(src, "_excel_partial_warnings.append(_msg)", label='src')


def test_compact_report_info_truncation_rows_keyed() -> None:
    """Excel truncation events are persisted as keyed Item/Value rows."""
    src = _read_app_simple()
    assert_in_source(src, "'Item': 'Excel_Truncation'", label='src')


def test_compact_report_info_schema_logger_emitted() -> None:
    """R67/B5: the Compact Report_Info writer logs row count + schema
    label so the operator can correlate produced shape with inputs."""
    src = _read_app_simple()
    assert_in_source(src, "Round 67 / B5: Compact Report_Info written", label='src')


def test_compact_report_info_old_schema_columns_removed() -> None:
    """R67/B5: the legacy ``Status, Warning, Generated_At`` schema
    MUST NOT appear in the Compact writer (parity check)."""
    src = _read_app_simple()
    # The literal old column triple should no longer appear in the
    # Compact writer block. We bound the search to the function range.
    pattern = re.compile(
        r"columns=\[\s*'Status'\s*,\s*'Warning'\s*,\s*'Generated_At'\s*\]"
    )
    assert pattern.search(src) is None, (
        "R67/B5: legacy ['Status', 'Warning', 'Generated_At'] schema MUST be removed"
    )
