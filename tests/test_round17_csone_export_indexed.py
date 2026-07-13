"""Round 17.1 -- CSOne TAC export end-to-end indexing.

Pins the contract that a CSOne-shaped xlsx (banner rows 1-15,
header on row 16) lands customer + case rows in the corpus DB.
Before Round 17.1 the parser silently dropped every row because
``pd.read_excel(..., header=0)`` saw the banner as the header.

Tests are deterministic and offline.  No real PII.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


pd = pytest.importorskip("pandas")
openpyxl = pytest.importorskip("openpyxl")

from corpus_indexer import index_folder, open_corpus_db


def _build_csone_fixture(path: Path) -> None:
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
    payload = [
        (
            "Synthetic Alpha",
            "Webex Calling",
            "P2",
            "Open",
            "Audio quality issue with intermittent latency exceeding 300ms during conference calls between branch offices and the central data center.",
        ),
        (
            "Synthetic Alpha",
            "Webex Meetings",
            "P3",
            "Closed",
            "Meeting join failure caused by certificate validation timeout when corporate firewall throttled outbound TLS handshakes during peak hours.",
        ),
        (
            "Synthetic Beta",
            "Webex Contact Center",
            "P1",
            "Open",
            "Routing error after upgrade caused inbound calls to be dropped when the supervisor team queue exhausted available agents during morning peaks.",
        ),
        (
            "Synthetic Gamma",
            "UCCE",
            "P3",
            "Closed",
            "Reporting export bug yielded zero-row CSVs for historical reports despite the dashboard showing populated data for the same time window.",
        ),
        (
            "Synthetic Gamma",
            "UCCE",
            "P2",
            "Open",
            "Outbound dialer slowed below configured pacing threshold when the Cisco Finesse desktop pushed concurrent supervisor reports during morning peaks.",
        ),
    ]
    for offset, (cust, tech, sev, status, title) in enumerate(payload):
        row_num = 17 + offset
        ws.cell(row=row_num, column=2, value=cust)
        ws.cell(row=row_num, column=6, value=tech)
        ws.cell(row=row_num, column=9, value=sev)
        ws.cell(row=row_num, column=11, value=f"6-{offset:09d}")
        ws.cell(row=row_num, column=12, value=f"68812345{offset:02d}")
        ws.cell(row=row_num, column=13, value=title)
        ws.cell(row=row_num, column=14, value=status)
    wb.save(str(path))


def test_csone_export_lands_customers_and_cases_in_db(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    fixture = root / "AdoptIQ Enhanced Premium Collab Summary-2026-04-25.xlsx"
    _build_csone_fixture(fixture)

    db_path = tmp_path / "corpus.db"
    conn: sqlite3.Connection = open_corpus_db(db_path)
    try:
        stats = index_folder(conn, root)
        assert stats.files_seen == 1
        assert stats.files_parsed == 1, f"errors={stats.errors!r}"
        assert stats.files_failed == 0

        cur = conn.cursor()
        customers = cur.execute('SELECT COUNT(*) FROM "customers"').fetchone()[0]
        cases = cur.execute('SELECT COUNT(*) FROM "cases"').fetchone()[0]
        chunks = cur.execute(
            'SELECT COUNT(*) FROM "playbook_chunks"'
        ).fetchone()[0]

        # 3 distinct synthetic customers (Alpha / Beta / Gamma).
        assert customers == 3, f"expected 3 customers, got {customers}"
        # 5 case rows.
        assert cases == 5, f"expected 5 cases, got {cases}"
        # At least one playbook chunk per case row that had a title.
        assert chunks >= 5
    finally:
        conn.close()


def test_csone_export_records_open_status(tmp_path: Path):
    """The ``is_open`` flag must be derived from ``Case Status`` so
    downstream filters (Customer 360, AB rollups) see the live cases."""
    root = tmp_path / "corpus"
    root.mkdir()
    fixture = root / "AdoptIQ Enhanced Premium Collab Summary-2026-04-25.xlsx"
    _build_csone_fixture(fixture)

    db_path = tmp_path / "corpus.db"
    conn: sqlite3.Connection = open_corpus_db(db_path)
    try:
        index_folder(conn, root)
        cur = conn.cursor()
        open_cases = cur.execute(
            'SELECT COUNT(*) FROM "cases" WHERE "is_open" = 1'
        ).fetchone()[0]
        # Three "Open" rows in the fixture above.
        assert open_cases == 3, f"expected 3 open cases, got {open_cases}"
    finally:
        conn.close()
