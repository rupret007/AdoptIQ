"""Round 26 - review (NIT-007a): end-to-end intel_uploads -> BM25.

Pins the contract that a CSOne-shaped xlsx dropped into
``Config.CSONE_INTEL_UPLOADS_FOLDER`` lands real rows in the corpus
DB and surfaces in BM25 retrieval.  Before this test the
upload-then-index path had only unit-level coverage of the upload
endpoint and the source-list resolver -- nothing exercised the
xlsx -> indexer -> BM25 chain that operators actually rely on.

Reuses the ``_build_csone_fixture`` recipe from
``test_round17_csone_export_indexed.py`` so the synthetic banner +
header + payload layout is identical to the live CSOne export.

Tests are deterministic and offline.  No real PII.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


pd = pytest.importorskip("pandas")
openpyxl = pytest.importorskip("openpyxl")


def _build_csone_fixture(path: Path) -> None:
    """Mirror of the Round 17.1 fixture builder so the e2e test
    indexes a realistic CSOne-shaped xlsx (banner rows 1-15, header
    on row 16, payload from row 17).  The unique-per-row title text
    drives the BM25 retrieval assertion below."""
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
    # Titles must clear ``corpus_indexer._MIN_CHUNK_TOKENS == 12``
    # (post-stop-word) or no playbook chunk is emitted from the
    # summary blob and BM25 retrieval has nothing to rank.  Match
    # the verbose-title shape the live CSOne export produces.
    payload = [
        (
            "Synthetic Uploads Alpha",
            "Webex Calling",
            "P2",
            "Open",
            "Distinctive uploadable jitter glitch on outbound calls "
            "during peak failover when branch offices fail to register "
            "with the regional cluster after a planned firmware upgrade "
            "across three data centers.",
        ),
        (
            "Synthetic Uploads Beta",
            "Webex Meetings",
            "P3",
            "Closed",
            "Meeting-room camera stuck on splash logo after firmware "
            "push from drop-folder index causing supervisors to lose "
            "video feeds during morning standups in three regional "
            "engineering offices for the second consecutive week.",
        ),
    ]
    for offset, (cust, tech, sev, status, title) in enumerate(payload):
        row_num = 17 + offset
        ws.cell(row=row_num, column=2, value=cust)
        ws.cell(row=row_num, column=6, value=tech)
        ws.cell(row=row_num, column=9, value=sev)
        ws.cell(row=row_num, column=11, value=f"6-{offset:09d}")
        ws.cell(row=row_num, column=12, value=f"99988877{offset:02d}")
        ws.cell(row=row_num, column=13, value=title)
        ws.cell(row=row_num, column=14, value=status)
    wb.save(str(path))


def _common_intel_uploads_only_setup(monkeypatch, tmp_path: Path) -> Path:
    """Disable the other 3 sources (sharepoint, onedrive,
    user_downloads) and point ``CSONE_INTEL_UPLOADS_FOLDER`` at
    ``tmp_path / "intel_uploads"`` so this test is the sole driver
    of the indexer."""
    from config import Config

    intel_dir = tmp_path / "intel_uploads"
    intel_dir.mkdir()

    # Disable sharepoint/onedrive/user_downloads so source resolution
    # collapses to the single intel_uploads entry.
    monkeypatch.setattr(
        Config, "ADOPTIQ_SHAREPOINT_ENABLED", False, raising=False
    )
    monkeypatch.setattr(
        Config, "CSONE_ONEDRIVE_FOLDER", str(tmp_path / "absent-od"),
        raising=False,
    )
    monkeypatch.setattr(
        Config, "CSONE_INCLUDE_USER_DOWNLOADS", False, raising=False
    )
    monkeypatch.setattr(
        Config, "CSONE_USER_DOWNLOADS_DIR", str(tmp_path / "absent-dl"),
        raising=False,
    )
    monkeypatch.setattr(
        Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True, raising=False
    )
    monkeypatch.setattr(
        Config, "CSONE_INTEL_UPLOADS_FOLDER", str(intel_dir), raising=False
    )
    return intel_dir


def test_intel_uploads_xlsx_indexes_into_corpus_db(monkeypatch, tmp_path: Path):
    """A CSOne-shaped xlsx dropped into intel_uploads/ lands rows
    in the corpus DB when ``index_folder`` walks the directory.

    Mirrors the Round 17.1 ``test_csone_export_lands_customers_and_cases_in_db``
    pattern but anchored on ``intel_uploads`` so a regression that
    excludes the new source from the indexer surfaces here.
    """
    from corpus_indexer import index_folder, open_corpus_db

    intel_dir = _common_intel_uploads_only_setup(monkeypatch, tmp_path)
    fixture = (
        intel_dir / "AdoptIQ Enhanced Premium Collab Summary-2026-04-26.xlsx"
    )
    _build_csone_fixture(fixture)

    db_path = tmp_path / "corpus.db"
    conn: sqlite3.Connection = open_corpus_db(db_path)
    try:
        stats = index_folder(conn, intel_dir)
        assert stats.files_seen == 1
        assert stats.files_parsed >= 1, f"errors={stats.errors!r}"
        assert stats.files_failed == 0

        cur = conn.cursor()
        customers = cur.execute('SELECT COUNT(*) FROM "customers"').fetchone()[0]
        cases = cur.execute('SELECT COUNT(*) FROM "cases"').fetchone()[0]
        chunks = cur.execute(
            'SELECT COUNT(*) FROM "playbook_chunks"'
        ).fetchone()[0]

        assert customers == 2, f"expected 2 customers, got {customers}"
        assert cases == 2, f"expected 2 cases, got {cases}"
        assert chunks >= 2
    finally:
        conn.close()


def test_intel_uploads_is_resolved_as_a_bootstrap_source(
    monkeypatch, tmp_path: Path
):
    """When the other sources are disabled / missing,
    ``_resolve_index_sources`` must surface ``intel_uploads`` as the
    only entry pointing at ``Config.CSONE_INTEL_UPLOADS_FOLDER``.
    This is the per-source-breakdown analog of
    ``aggregate.files_parsed >= 1`` -- proves the bootstrap walker
    will reach the upload directory in production."""
    import corpus_bootstrap as cb

    intel_dir = _common_intel_uploads_only_setup(monkeypatch, tmp_path)

    sources = cb._resolve_index_sources()
    intel_entries = [s for s in sources if s["label"] == "intel_uploads"]
    assert len(intel_entries) == 1, sources
    assert str(intel_entries[0]["dir"]) == str(intel_dir)


def test_intel_uploads_xlsx_surfaces_via_bm25_search(
    monkeypatch, tmp_path: Path
):
    """End-to-end: indexed intel_uploads xlsx must be retrievable via
    ``corpus_retriever.search_playbook``.  We seed a deterministic,
    distinctive token ("uploadable jitter glitch") in the synthetic
    case title so BM25 retrieval has an unambiguous match."""
    from corpus_indexer import index_folder, open_corpus_db
    import corpus_retriever

    intel_dir = _common_intel_uploads_only_setup(monkeypatch, tmp_path)
    fixture = (
        intel_dir / "AdoptIQ Enhanced Premium Collab Summary-2026-04-26.xlsx"
    )
    _build_csone_fixture(fixture)

    db_path = tmp_path / "corpus.db"
    conn: sqlite3.Connection = open_corpus_db(db_path)
    try:
        stats = index_folder(conn, intel_dir)
        assert stats.files_parsed >= 1, f"errors={stats.errors!r}"

        # Wire the retriever's singleton at the test connection so
        # search_playbook reads from the freshly indexed DB.
        original_conn = corpus_retriever._CONN  # pylint: disable=protected-access
        corpus_retriever.configure_connection(conn)
        try:
            results = corpus_retriever.search_playbook(
                "uploadable jitter glitch",
                top_k=5,
            )
        finally:
            corpus_retriever.configure_connection(original_conn)

        assert results, "BM25 search returned no hits for indexed upload"
        # The Alpha row's title contains "uploadable jitter glitch" and
        # should rank first by BM25 score.
        top_text = (results[0].text or "").lower()
        assert (
            "uploadable jitter glitch" in top_text
            or "jitter glitch" in top_text
        ), f"unexpected top hit: {results[0].text!r}"
    finally:
        conn.close()
