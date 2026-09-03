"""Round 176: peer likely-next joins adoption-barrier status.

Round 175 indexed CSOne ``resolution`` text but dropped ``AB_STATUS_C``.
Closure was inferred from theme-matched TAC cases, so a still-open
barrier with a closed TAC ticket published "closed after {method}".
This round stores barrier status (schema v3) and fail-closes mixed
barrier-vs-case evidence. Fixtures only; not a new report or page.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import corpus_retriever as cr
import knowledge_schema
import report_corpus_context
from corpus_indexer import _barrier_open_flag, index_folder
from test_round175_peer_guidance_knowledge import (
    PEER_METHOD,
    _index_corpus,
    _peer_barrier,
    _peer_case,
    _peer_pulse,
)


@pytest.fixture(autouse=True)
def _clear_corpus_connection():
    cr.configure_connection(None)
    yield
    cr.configure_connection(None)


def test_schema_version_bumped_for_barrier_status() -> None:
    assert knowledge_schema.SCHEMA_VERSION >= 3
    ddl = knowledge_schema._DDL_BARRIERS
    assert '"status"' in ddl
    assert '"is_open"' in ddl


def test_barrier_open_flag_uses_canonical_normalizer() -> None:
    assert _barrier_open_flag("Open") == 1
    assert _barrier_open_flag("In Progress") == 1
    assert _barrier_open_flag("Closed") == 0
    assert _barrier_open_flag("Resolved") == 0
    assert _barrier_open_flag("") is None
    assert _barrier_open_flag(None) is None
    assert _barrier_open_flag("not-a-status") is None


def test_indexer_persists_ab_status_c(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status="Open"
                ),
                _peer_barrier(
                    "Peer C", resolution=PEER_METHOD, suffix="C", ab_status=""
                ),
            ],
        },
    )
    try:
        rows = connection.execute(
            'SELECT cust."name" AS "name", b."status" AS "status", b."is_open" AS "is_open" '
            'FROM "barriers" b JOIN "customers" cust ON cust."id" = b."customer_id" '
            'WHERE cust."name" IN (?, ?, ?) '
            'ORDER BY cust."name" ASC;',
            ("Peer A", "Peer B", "Peer C"),
        ).fetchall()
        by_name = {str(row[0]): (row[1], row[2]) for row in rows}
        assert by_name["Peer A"][0] == "Closed"
        assert int(by_name["Peer A"][1]) == 0
        assert by_name["Peer B"][0] == "Open"
        assert int(by_name["Peer B"][1]) == 1
        assert not str(by_name["Peer C"][0] or "").strip()
        assert by_name["Peer C"][1] is None
    finally:
        cr.configure_connection(None)
        connection.close()


def test_closed_cases_plus_open_barriers_fail_closed(tmp_path: Path) -> None:
    """Theme-matched TAC closure is not barrier closure. Round 176."""
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status="Open"
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status="Open"
                ),
            ],
            "cases": [
                _peer_case("Peer A", suffix="A", status="Closed"),
                _peer_case("Peer B", suffix="B", status="Closed"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.evidence_sufficient is True
        assert evidence.method_closed_peer_count == 2
        assert evidence.method_barrier_open_peer_count == 2
        assert evidence.method_barrier_closed_peer_count == 0
        assert evidence.likely_next == "insufficient"
        assert evidence.likely_next_basis == ""
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert clause == ""
        fallback = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=False, prefer_peer_resolution=True
        )
        assert "peer-observed resolution was" in fallback
        assert "likely-next" not in fallback
        assert "closed after" not in fallback
        assert "will " not in fallback.casefold()
    finally:
        cr.configure_connection(None)
        connection.close()


def test_closed_barriers_without_cases_publish_closure(tmp_path: Path) -> None:
    """Use the barrier lifecycle fixture data Round 175 dropped. Round 176."""
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.method_closed_peer_count == 0
        assert evidence.method_barrier_closed_peer_count == 2
        assert evidence.likely_next == "closure"
        assert evidence.likely_next_basis == "barrier"
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "2 similar accounts closed after" in clause
        assert "likely-next is closure after that method (not a certainty)" in clause
        assert "(median" not in clause
        assert "will " not in clause.casefold()
        facts_text = report_corpus_context.format_peer_guidance_ask_ai_line(evidence)
        assert "CORPUS:PG-" in facts_text
        assert "insufficient_peer_evidence=true" not in facts_text
    finally:
        cr.configure_connection(None)
        connection.close()


def test_open_barriers_without_cases_remain_open(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status="Open"
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status="Open"
                ),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.likely_next == "remains_open"
        assert evidence.likely_next_basis == "barrier"
        assert evidence.method_barrier_open_peer_count == 2
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "remain open after" in clause
        assert "closed after" not in clause
    finally:
        cr.configure_connection(None)
        connection.close()


def test_blank_barrier_status_falls_back_to_cases(tmp_path: Path) -> None:
    """Unknown AB_STATUS_C keeps the Round 175 case path. Round 176."""
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status=""
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status=""
                ),
            ],
            "cases": [
                _peer_case("Peer A", suffix="A", status="Closed"),
                _peer_case("Peer B", suffix="B", status="Closed"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.method_barrier_closed_peer_count == 0
        assert evidence.method_barrier_open_peer_count == 0
        assert evidence.method_closed_peer_count == 2
        assert evidence.likely_next == "closure"
        assert evidence.likely_next_basis == "case"
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "closed after" in clause
        assert "(median 9d)" in clause
    finally:
        cr.configure_connection(None)
        connection.close()


def test_mixed_open_closed_barrier_snapshots_contribute_no_outcome(
    tmp_path: Path,
) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A1", ab_status="Open"
                ),
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A2", ab_status="Closed"
                ),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
                _peer_barrier("Peer C", resolution=PEER_METHOD, suffix="C"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        # Peer A mixed → no barrier outcome; B+C closed is a majority of 2.
        assert evidence.method_barrier_closed_peer_count == 2
        assert evidence.method_barrier_open_peer_count == 0
        assert evidence.likely_next == "closure"
        assert evidence.likely_next_basis == "barrier"
    finally:
        cr.configure_connection(None)
        connection.close()


def test_closed_barriers_plus_worse_pulse_fail_closed(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "pulse": (
                _peer_pulse("Peer A", first="green", last="red", suffix="A")
                + _peer_pulse("Peer B", first="green", last="red", suffix="B")
            ),
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.method_barrier_closed_peer_count == 2
        assert evidence.method_pulse_worsened_count == 2
        assert evidence.likely_next == "insufficient"
        published = report_corpus_context._r175_published_peer_clause(
            evidence, include_likely_next=True
        )
        assert "peer-observed resolution was" in published
        assert "likely-next" not in published
        assert "closed after" not in published
    finally:
        cr.configure_connection(None)
        connection.close()


def test_closed_barriers_win_a_case_tie(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
                _peer_barrier("Peer C", resolution=PEER_METHOD, suffix="C"),
                _peer_barrier("Peer D", resolution=PEER_METHOD, suffix="D"),
            ],
            "cases": [
                _peer_case("Peer A", suffix="A", status="Closed"),
                _peer_case("Peer B", suffix="B", status="Closed"),
                _peer_case("Peer C", suffix="C", status="Open"),
                _peer_case("Peer D", suffix="D", status="Open"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.method_closed_peer_count == 2
        assert evidence.method_open_peer_count == 2
        assert evidence.method_barrier_closed_peer_count == 4
        assert evidence.likely_next == "closure"
        assert evidence.likely_next_basis == "barrier"
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "4 similar accounts closed after" in clause
    finally:
        cr.configure_connection(None)
        connection.close()


def test_decide_matrix_barrier_overrides_and_mixes() -> None:
    def decide(**overrides: int) -> tuple[str, str]:
        payload = {
            "dominant_n": 4,
            "min_n": 2,
            "method_closed": 0,
            "method_open": 0,
            "method_pulse_recovered": 0,
            "method_pulse_worsened": 0,
            "method_barrier_closed": 0,
            "method_barrier_open": 0,
        }
        payload.update(overrides)
        return cr._peer_likely_next_and_basis(**payload)

    assert decide(method_closed=2) == ("closure", "case")
    assert decide(method_barrier_closed=2) == ("closure", "barrier")
    assert decide(method_barrier_open=2) == ("remains_open", "barrier")
    assert decide(method_closed=2, method_barrier_open=2) == ("insufficient", "")
    assert decide(method_open=2, method_barrier_closed=2) == ("insufficient", "")
    assert decide(method_barrier_closed=2, method_pulse_worsened=2) == (
        "insufficient",
        "",
    )
    assert decide(method_barrier_closed=2, method_barrier_open=2) == (
        "insufficient",
        "",
    )
    assert decide(method_closed=3, method_open=2) == ("closure", "case")
    assert decide(method_pulse_recovered=2) == ("pulse_recovery", "pulse")
    # Known barrier split is mixed even when TAC cases have a majority.
    assert decide(method_closed=3, method_barrier_closed=2, method_barrier_open=2) == (
        "insufficient",
        "",
    )


def _v2_barriers_connection(path: Path) -> sqlite3.Connection:
    """Pre-Round-176 corpus shape: schema_meta=2, barriers without status."""
    connection = sqlite3.connect(str(path))
    connection.execute(
        'CREATE TABLE "schema_meta" ('
        ' "id" INTEGER PRIMARY KEY CHECK ("id" = 1),'
        ' "version" INTEGER NOT NULL,'
        ' "migrated_at" TEXT NOT NULL)'
    )
    connection.execute(
        'INSERT INTO "schema_meta" ("id", "version", "migrated_at") '
        "VALUES (1, 2, '2026-01-01T00:00:00Z')"
    )
    connection.execute(
        'CREATE TABLE "barriers" ('
        ' "id" INTEGER PRIMARY KEY AUTOINCREMENT,'
        ' "customer_id" INTEGER NOT NULL,'
        ' "technology" TEXT,'
        ' "theme" TEXT NOT NULL,'
        ' "first_seen" TEXT,'
        ' "last_seen" TEXT,'
        ' "occurrences" INTEGER NOT NULL DEFAULT 1,'
        ' "source_file_id" INTEGER)'
    )
    connection.commit()
    return connection


def test_apply_schema_does_not_stamp_v2_forward() -> None:
    """open_corpus_db must not skip rebuild by stamping v3 first. Round 176."""
    connection = _v2_barriers_connection(Path(":memory:"))
    try:
        assert knowledge_schema.get_persisted_schema_version(connection) == 2
        returned = knowledge_schema.apply_schema(connection)
        assert returned == 2
        assert knowledge_schema.get_persisted_schema_version(connection) == 2
        assert knowledge_schema.needs_rebuild(connection) is True
        names = knowledge_schema._table_column_names(connection, "barriers")
        assert "status" in names
        assert "is_open" in names
    finally:
        connection.close()


def test_open_corpus_db_on_v2_still_needs_rebuild(tmp_path: Path) -> None:
    """Production boot path: apply_schema must not hide the v2 rebuild. Round 176."""
    db_path = tmp_path / "corpus.db"
    seeded = _v2_barriers_connection(db_path)
    seeded.close()
    from corpus_indexer import open_corpus_db

    connection = open_corpus_db(db_path)
    try:
        assert knowledge_schema.get_persisted_schema_version(connection) == 2
        assert knowledge_schema.needs_rebuild(connection) is True
    finally:
        connection.close()


def test_index_folder_rebuilds_v2_and_stamps_schema_v3(tmp_path: Path) -> None:
    db_path = tmp_path / "corpus.db"
    connection = _v2_barriers_connection(db_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    try:
        index_folder(connection, empty_root)
        assert knowledge_schema.get_persisted_schema_version(connection) == 3
        assert knowledge_schema.needs_rebuild(connection) is False
        names = knowledge_schema._table_column_names(connection, "barriers")
        assert "status" in names
        assert "is_open" in names
    finally:
        connection.close()


def test_source_shape_round176_markers() -> None:
    schema = Path(__file__).resolve().parents[1] / "knowledge_schema.py"
    indexer = Path(__file__).resolve().parents[1] / "corpus_indexer.py"
    retriever = Path(__file__).resolve().parents[1] / "corpus_retriever.py"
    context = Path(__file__).resolve().parents[1] / "report_corpus_context.py"
    assert "Round 176" in schema.read_text(encoding="utf-8")
    assert "_barrier_open_flag" in indexer.read_text(encoding="utf-8")
    assert "_peer_barrier_outcome" in retriever.read_text(encoding="utf-8")
    assert "likely_next_basis" in retriever.read_text(encoding="utf-8")
    assert 'basis == "barrier"' in context.read_text(encoding="utf-8")
    assert "SCHEMA_VERSION: int = 3" in schema.read_text(encoding="utf-8")
