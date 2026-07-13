"""Round 17.1 -- xlsx layout detector contracts.

Pins :func:`corpus_indexer._detect_xlsx_layout` against three tiny
synthetic xlsx fixtures we build in-memory:

* CSOne TAC export: banner rows 1-15 with a "Filtered By" marker and
  the real header on row 16.
* AdoptIQ-rendered Data xlsx: single-cell sheet title on row 1 and
  the real header on row 2.
* Plain header-on-row-1: legacy Round 17 contract.

The detector is the pivot that lets the indexer pick the correct
``pd.read_excel(..., header=...)`` argument so customer / case rows
land in the corpus instead of being silently dropped.

Tests are deterministic and offline.  No real PII is ever shipped
into the fixture cells.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pd = pytest.importorskip("pandas")
openpyxl = pytest.importorskip("openpyxl")

from corpus_indexer import _detect_xlsx_layout


def _build_csone_export(path: Path) -> None:
    """Write a 5-row CSOne TAC export with banner rows 1-15.

    Row 1 holds the macro's title, rows 2-15 hold the "Filtered By"
    block, row 16 is the column header, and rows 17+ are case rows.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "AdoptIQ Enhanced Premium Collab"
    ws.cell(row=1, column=1, value="AdoptIQ Enhanced/Premium Collab Summary")
    ws.cell(row=2, column=1, value="Filtered By:")
    ws.cell(row=3, column=1, value="Date Field: Date/Time Opened")
    ws.cell(row=4, column=1, value="Service Tier equals AdoptIQ Enhanced")
    for r in range(5, 16):
        ws.cell(row=r, column=1, value=f"banner row {r}")
    headers = [
        None,
        "Customer Name: Customer Name",
        "Account",
        "Subscription Reference Id",
        "Product: Product Name",
        "Tech.",
        "Sub Technology",
        "Sub Tech.",
        "Severity",
        "Service Tier",
        "SR Number",
        "Case Number",
        "Title",
        "Case Status",
        "Date/Time Opened",
        "Date/Time Closed",
    ]
    for c, val in enumerate(headers, start=1):
        ws.cell(row=16, column=c, value=val)
    for offset in range(5):
        row_num = 17 + offset
        ws.cell(row=row_num, column=2, value=f"Synthetic Customer {offset}")
        ws.cell(row=row_num, column=11, value=f"6-{offset:09d}")
        ws.cell(row=row_num, column=12, value=f"68812345{offset:02d}")
        ws.cell(row=row_num, column=13, value=f"Synthetic case {offset}")
        ws.cell(row=row_num, column=14, value="Closed" if offset % 2 else "Open")
    wb.save(str(path))


def _build_adoptiq_data(path: Path) -> None:
    """Write a 3-row AdoptIQ-rendered Data xlsx where row 1 holds the
    sheet title and row 2 holds the column header."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Risk_Summary"
    ws.cell(row=1, column=1, value="Risk_Summary")
    headers = ["Customer", "Risk_Score", "Risk_Level", "Adoption_Barriers", "Support_Cases"]
    for c, val in enumerate(headers, start=1):
        ws.cell(row=2, column=c, value=val)
    rows = [
        ("Synthetic Alpha", 7.4, "high", 3, 5),
        ("Synthetic Beta", 4.1, "medium", 1, 2),
        ("Synthetic Gamma", 9.0, "critical", 6, 8),
    ]
    for offset, payload in enumerate(rows):
        row_num = 3 + offset
        for c, val in enumerate(payload, start=1):
            ws.cell(row=row_num, column=c, value=val)
    wb.save(str(path))


def _build_plain(path: Path) -> None:
    """Write a plain header-on-row-1 xlsx (legacy contract)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "All_Support_Cases"
    headers = ["customer_name", "case_number", "severity", "status"]
    for c, val in enumerate(headers, start=1):
        ws.cell(row=1, column=c, value=val)
    rows = [
        ("Synthetic Alpha", "6-100", "P3", "Closed"),
        ("Synthetic Beta", "6-101", "P2", "Open"),
    ]
    for offset, payload in enumerate(rows):
        row_num = 2 + offset
        for c, val in enumerate(payload, start=1):
            ws.cell(row=row_num, column=c, value=val)
    wb.save(str(path))


def test_detect_csone_export_layout(tmp_path: Path):
    fixture = tmp_path / "csone.xlsx"
    _build_csone_export(fixture)
    xl = pd.ExcelFile(fixture, engine="openpyxl")
    layout, header_row = _detect_xlsx_layout(
        xl, xl.sheet_names[0], pd_mod=pd
    )
    assert layout == "csone_export"
    assert header_row == 15  # zero-indexed Excel row 16


def test_detect_adoptiq_data_layout(tmp_path: Path):
    fixture = tmp_path / "data.xlsx"
    _build_adoptiq_data(fixture)
    xl = pd.ExcelFile(fixture, engine="openpyxl")
    layout, header_row = _detect_xlsx_layout(
        xl, xl.sheet_names[0], pd_mod=pd
    )
    assert layout == "adoptiq_data"
    assert header_row == 1  # zero-indexed Excel row 2


def test_detect_plain_layout(tmp_path: Path):
    fixture = tmp_path / "plain.xlsx"
    _build_plain(fixture)
    xl = pd.ExcelFile(fixture, engine="openpyxl")
    layout, header_row = _detect_xlsx_layout(
        xl, xl.sheet_names[0], pd_mod=pd
    )
    assert layout == "plain"
    assert header_row == 0


def test_detect_returns_plain_for_empty_sheet(tmp_path: Path):
    """An empty sheet must fall through to ``plain`` so the parser can
    proceed (and then drop the empty DataFrame harmlessly)."""
    fixture = tmp_path / "empty.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "blank"
    wb.save(str(fixture))
    xl = pd.ExcelFile(fixture, engine="openpyxl")
    layout, header_row = _detect_xlsx_layout(
        xl, xl.sheet_names[0], pd_mod=pd
    )
    assert layout == "plain"
    assert header_row == 0


def test_detect_returns_plain_for_short_sheet(tmp_path: Path):
    """A single-row sheet (header only, no data) is plain.  The
    detector must not mis-classify it as ``adoptiq_data`` because that
    would shift the header row by one and lose the data."""
    fixture = tmp_path / "short.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "single_row"
    headers = ["customer_name", "case_number", "severity"]
    for c, val in enumerate(headers, start=1):
        ws.cell(row=1, column=c, value=val)
    wb.save(str(fixture))
    xl = pd.ExcelFile(fixture, engine="openpyxl")
    layout, header_row = _detect_xlsx_layout(
        xl, xl.sheet_names[0], pd_mod=pd
    )
    assert layout == "plain"
    assert header_row == 0
