"""Round 17.1 -- AdoptIQ Data xlsx multi-sheet router contracts.

The AdoptIQ-rendered Data xlsx that lands in the user's Downloads
folder contains many sheets (``Risk_Summary``, ``All_Support_Cases``,
``Customer_Pulse``, ``Adoption_Barriers``, ``Action_Plans``, ...).
Round 17.1 extends the corpus indexer's sheet-family router so each
of these lands in the right corner of the corpus DB:

* ``All_Support_Cases``  -> cases table
* ``Adoption_Barriers``  -> barriers table
* ``Customer_Pulse``     -> sentiments table
* ``Risk_Summary``       -> playbook chunks (narrative, no case rows)
* ``Action_Plans``       -> barriers + resolutions

Pinning these contracts protects the indexer against accidental
sheet-name renames in the renderer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


pd = pytest.importorskip("pandas")
openpyxl = pytest.importorskip("openpyxl")

from corpus_indexer import index_folder, open_corpus_db


def _write_data_workbook(path: Path) -> None:
    """Write a four-sheet AdoptIQ Data xlsx with row 1 = sheet title,
    row 2 = column header, rows 3+ = data."""
    wb = openpyxl.Workbook()

    # Sheet 1: Risk_Summary -- narrative-only.
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
        for c, val in enumerate(payload, start=1):
            ws.cell(row=3 + offset, column=c, value=val)

    # Sheet 2: All_Support_Cases -- case rows.
    ws = wb.create_sheet("All_Support_Cases")
    ws.cell(row=1, column=1, value="All_Support_Cases")
    headers = [
        "Customer Name: Customer Name",
        "col_2",
        "Subscription Reference Id",
        "Product: Product Name",
        "Tech.",
        "Sub Technology",
        "Severity",
        "Case Number",
        "Title",
        "Case Status",
    ]
    for c, val in enumerate(headers, start=1):
        ws.cell(row=2, column=c, value=val)
    case_rows = [
        (
            "Synthetic Alpha",
            "ACC-1",
            "SUB-1",
            "Webex Calling",
            "Webex Calling",
            "Webex Calling",
            "P2",
            "688123450",
            "Audio quality issue with intermittent latency exceeding three hundred milliseconds during conference calls.",
            "Open",
        ),
        (
            "Synthetic Beta",
            "ACC-2",
            "SUB-2",
            "Webex Contact Center",
            "Webex Contact Center",
            "Webex Contact Center",
            "P1",
            "688123451",
            "Routing error after upgrade caused inbound calls to be dropped when the supervisor queue exhausted available agents.",
            "Open",
        ),
    ]
    for offset, payload in enumerate(case_rows):
        for c, val in enumerate(payload, start=1):
            ws.cell(row=3 + offset, column=c, value=val)

    # Sheet 3: Adoption_Barriers -- barrier rows (Salesforce-style).
    ws = wb.create_sheet("Adoption_Barriers")
    ws.cell(row=1, column=1, value="Adoption_Barriers")
    headers = [
        "ID",
        "ACCOUNT__C",
        "ACCOUNT_MANAGER_C",
        "BUSINESS_UNIT_C",
        "SUBJECT_C",
        "DESCRIPTION_C",
        "ACTION_PLAN_TITLE_C",
        "NEXT_ACTION_C",
        "CLOSURE_COMMENTS_C",
    ]
    for c, val in enumerate(headers, start=1):
        ws.cell(row=2, column=c, value=val)
    barrier_rows = [
        (
            "AB-001",
            "Synthetic Alpha",
            "manager-alpha@example.com",
            "Webex Calling",
            "Audio quality concerns",
            "Customer reports intermittent audio quality issues during cross-region calls; latency spikes correlated with branch office VPN saturation.",
            "Investigate VPN saturation at branch offices",
            "Schedule packet capture with networking partner",
            None,
        ),
        (
            "AB-002",
            "Synthetic Beta",
            "manager-beta@example.com",
            "Webex Contact Center",
            "Routing degradation post-upgrade",
            "Inbound voice traffic dropped after WxCC upgrade; supervisor queue exhausted at peak hours and routing strategy fell back to default flow.",
            "Re-validate routing strategy after upgrade",
            "Confirm fallback flow targets correct queue",
            "Resolved by routing strategy update",
        ),
    ]
    for offset, payload in enumerate(barrier_rows):
        for c, val in enumerate(payload, start=1):
            ws.cell(row=3 + offset, column=c, value=val)

    # Sheet 4: Customer_Pulse -- sentiment rows.
    ws = wb.create_sheet("Customer_Pulse")
    ws.cell(row=1, column=1, value="Customer_Pulse")
    headers = ["NAME", "ACCOUNT__C", "BU_NAME", "CUSTOMER_PULSE__C", "COMMENTS__C", "AS_OF_DATE"]
    for c, val in enumerate(headers, start=1):
        ws.cell(row=2, column=c, value=val)
    pulse_rows = [
        (
            "PULSE-001",
            "Synthetic Alpha",
            "Webex Calling",
            "Yellow",
            "Customer satisfaction has eroded due to recurring audio quality complaints across branch offices.",
            "2026-04-25",
        ),
        (
            "PULSE-002",
            "Synthetic Beta",
            "Webex Contact Center",
            "Red",
            "Executive sponsor escalated this week after the upgrade-related routing failures impacted morning operations.",
            "2026-04-25",
        ),
    ]
    for offset, payload in enumerate(pulse_rows):
        for c, val in enumerate(payload, start=1):
            ws.cell(row=3 + offset, column=c, value=val)

    wb.save(str(path))


def test_adoptiq_data_router_lands_each_sheet_in_right_table(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    fixture = root / "AdoptIQ_Data_2026-04-25_Acme.xlsx"
    _write_data_workbook(fixture)

    db_path = tmp_path / "corpus.db"
    conn: sqlite3.Connection = open_corpus_db(db_path)
    try:
        stats = index_folder(conn, root)
        assert stats.files_seen == 1
        assert stats.files_parsed == 1, f"errors={stats.errors!r}"
        assert stats.files_failed == 0

        cur = conn.cursor()
        # Three customers across the four sheets (Alpha / Beta /
        # Gamma in Risk_Summary; Alpha + Beta repeated in cases /
        # barriers / pulse but de-duplicated by name_norm).
        customers = cur.execute('SELECT COUNT(*) FROM "customers"').fetchone()[0]
        assert customers == 3, f"expected 3 customers, got {customers}"

        # All_Support_Cases -> 2 case rows
        cases = cur.execute('SELECT COUNT(*) FROM "cases"').fetchone()[0]
        assert cases == 2, f"expected 2 cases, got {cases}"

        # Adoption_Barriers -> 2 barrier rows
        barriers = cur.execute(
            'SELECT COUNT(*) FROM "barriers"'
        ).fetchone()[0]
        assert barriers == 2, f"expected 2 barriers, got {barriers}"

        # Adoption_Barriers had ACTION_PLAN_TITLE_C / NEXT_ACTION_C
        # populated -> we expect at least one resolution row.
        resolutions = cur.execute(
            'SELECT COUNT(*) FROM "resolutions"'
        ).fetchone()[0]
        assert resolutions >= 2

        # Customer_Pulse -> 2 sentiment rows
        sentiments = cur.execute(
            'SELECT COUNT(*) FROM "sentiments"'
        ).fetchone()[0]
        assert sentiments == 2, f"expected 2 sentiments, got {sentiments}"

        # Other sheets (cases / barriers / pulse) should produce
        # playbook chunks for their narrative text.  We do not pin
        # Risk_Summary chunks here because tiny ``key: value`` blobs
        # can fall below ``_MIN_CHUNK_TOKENS``; the cases /
        # barriers / pulse summaries (which use full sentences) are
        # the load-bearing chunk source.
        chunks = cur.execute(
            'SELECT COUNT(*) FROM "playbook_chunks"'
        ).fetchone()[0]
        assert chunks >= 4, f"expected at least 4 chunks, got {chunks}"
    finally:
        conn.close()


def test_adoptiq_data_router_skips_unsupported_sheets_silently(tmp_path: Path):
    """Sheets whose name does not match any token (e.g.
    ``Cover_Page``) must be ignored without aborting the rest of the
    workbook."""
    fixture = tmp_path / "AdoptIQ_Data_extra.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cover_Page"
    ws.cell(row=1, column=1, value="Cover_Page")
    ws.cell(row=2, column=1, value="A")
    ws.cell(row=2, column=2, value="B")
    ws.cell(row=2, column=3, value="C")
    ws.cell(row=3, column=1, value="ignore me")
    ws.cell(row=3, column=2, value="ignore me too")
    ws.cell(row=3, column=3, value="and me")

    ws = wb.create_sheet("Risk_Summary")
    ws.cell(row=1, column=1, value="Risk_Summary")
    headers = ["Customer", "Risk_Score", "Risk_Level"]
    for c, val in enumerate(headers, start=1):
        ws.cell(row=2, column=c, value=val)
    ws.cell(row=3, column=1, value="Synthetic Alpha")
    ws.cell(row=3, column=2, value=7.4)
    ws.cell(row=3, column=3, value="high")
    wb.save(str(fixture))

    db_path = tmp_path / "corpus.db"
    conn: sqlite3.Connection = open_corpus_db(db_path)
    try:
        stats = index_folder(conn, tmp_path)
        assert stats.files_parsed == 1, f"errors={stats.errors!r}"
        cur = conn.cursor()
        customers = cur.execute('SELECT COUNT(*) FROM "customers"').fetchone()[0]
        assert customers == 1
    finally:
        conn.close()
