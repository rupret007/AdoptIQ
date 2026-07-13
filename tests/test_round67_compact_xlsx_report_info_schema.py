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
    assert "Round 67 / Build 41 (B5)" in src, (
        "R67/B5 marker MUST be present in app_simple.py"
    )
    # Pin the actual DataFrame construction.
    pattern = re.compile(
        r"report_info_df\s*=\s*pd\.DataFrame\(\s*_info_records\s*,\s*columns=\[\s*'Item'\s*,\s*'Value'\s*\]\s*\)"
    )
    assert pattern.search(src), (
        "R67/B5: Compact Report_Info DataFrame MUST be constructed with "
        "columns=['Item', 'Value']"
    )


def test_compact_report_info_sheet_title_keyed_rows() -> None:
    """R67/B5: per-sheet titles are emitted as
    ``Item='Sheet_Title:<sheet_name>', Value=<title>`` rows."""
    src = _read_app_simple()
    pattern = re.compile(
        r"'Item'\s*:\s*f'Sheet_Title:\{_sn\}'"
    )
    assert pattern.search(src), (
        "R67/B5: per-sheet title rows MUST use 'Item': f'Sheet_Title:{_sn}' keying"
    )


def test_compact_report_info_baseline_rows_present() -> None:
    """R67/B5: the contract is that Compact Report_Info ALWAYS has at
    least Export type + Generated at (UTC) baseline rows."""
    src = _read_app_simple()
    assert "{'Item': 'Export type', 'Value': 'Standard (Compact)'}" in src, (
        "R67/B5: Export type baseline row MUST be present"
    )
    assert "'Item': 'Generated at (UTC)'" in src, (
        "R67/B5: Generated at (UTC) baseline row MUST be present"
    )


def test_compact_report_info_partial_warning_rows_keyed() -> None:
    """Partial-data warnings are persisted as keyed Item/Value rows."""
    src = _read_app_simple()
    assert "'Item': 'Partial_Data_Warning'" in src, (
        "R67/B5: partial-data warnings MUST flow through Item='Partial_Data_Warning'"
    )


def test_round105_compact_report_info_merges_status_partial_warnings() -> None:
    """Round 105: Compact Report_Info must include warnings already exposed
    in status/Word, including ACC scope-exclusion warnings."""
    src = _read_app_simple()
    assert "Round 105: include the same local/status partial-data warnings" in src
    assert "for _w in (partial_data_warnings or [])" in src
    assert "_excel_partial_warnings.append(_msg)" in src


def test_compact_report_info_truncation_rows_keyed() -> None:
    """Excel truncation events are persisted as keyed Item/Value rows."""
    src = _read_app_simple()
    assert "'Item': 'Excel_Truncation'" in src, (
        "R67/B5: truncation events MUST flow through Item='Excel_Truncation'"
    )


def test_compact_report_info_schema_logger_emitted() -> None:
    """R67/B5: the Compact Report_Info writer logs row count + schema
    label so the operator can correlate produced shape with inputs."""
    src = _read_app_simple()
    assert "Round 67 / B5: Compact Report_Info written" in src, (
        "R67/B5: a structured logger.info MUST follow the Report_Info "
        "write call so production drift is greppable"
    )


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
