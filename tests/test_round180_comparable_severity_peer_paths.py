"""Round 180: peer likely-next only from comparable-severity lived paths.

Theme+method peers at Critical and Low must not publish the same
likely-next. Existing Ask AI / Customer 360 / Historical Context cards
inherit via the R177 view. Fixtures only; not a new report or page.
"""

from __future__ import annotations

import csv
import shutil
import sqlite3
from pathlib import Path

import pytest

import corpus_retriever as cr
import knowledge_schema
import report_corpus_context as rcc
from corpus_indexer import index_folder, open_corpus_db
from source_shape_utils import assert_in_source

PEER_METHOD = "Rotated service token and updated documentation for SSO setup."
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "round17"


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _peer_barrier(
    customer: str, *, resolution: str, suffix: str, ab_status: str = "Closed"
) -> dict[str, str]:
    return {
        "customer_name": customer,
        "SUBJECT_C": f"Authentication SSO login errors {suffix}",
        "SEVERITY_C": "Critical",
        "AB_STATUS_C": ab_status,
        "ID": f"AB-{suffix}",
        "technology": "security",
        "theme": "authentication",
        "resolution": resolution,
    }


def _index_corpus(
    tmp_path: Path,
    extra_rows: dict[str, list[dict[str, str]]] | None = None,
    *,
    copy_bundled_barriers: bool = True,
):
    """Index a temp corpus. Round 180 tests stay self-contained.

    Do not import the R175/R177 suites here — they pull app_simple /
    decision_report_delivery and break collection on thin VMs.
    ``copy_bundled_barriers=False`` keeps the all-unknown-severity case
    free of Synthetic Alpha's Critical authentication row.
    """
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    for name in (
        "synthetic_cases.csv",
        "synthetic_barriers.csv",
        "synthetic_pulse.csv",
    ):
        if name == "synthetic_barriers.csv" and not copy_bundled_barriers:
            continue
        shutil.copy2(FIXTURES / name, corpus_root / name)
    extras = extra_rows or {}
    if extras.get("barriers"):
        _write_csv(corpus_root / "peer_barriers.csv", extras["barriers"])
    connection = open_corpus_db(tmp_path / "corpus.db")
    index_folder(connection, corpus_root)
    cr.configure_connection(connection)
    return connection


VIEW_KEYS = frozenset(
    {
        "status",
        "insufficient_reason",
        "insufficient_copy",
        "next_step",
        "likely_next",
        "likely_next_label",
        "method",
        "evidence_line",
        "peer_accounts",
        "method_peers",
        "closed_n",
        "open_n",
        "basis",
        "honesty_label",
        "ready_for_live_cisco",
        "source_id",
        "theme",
        "technology",
    }
)


def _evidence(**overrides: object) -> cr.PeerGuidanceEvidence:
    payload: dict[str, object] = {
        "theme": "authentication",
        "technology": "security",
        "peer_customer_count": 3,
        "resolved_peer_count": 2,
        "dominant_method_text": PEER_METHOD,
        "dominant_method_peers": 2,
        "closed_peer_count": 2,
        "open_peer_count": 1,
        "close_time_median_days": 12.0,
        "pulse_recovered_count": 0,
        "pulse_worsened_count": 0,
        "likely_next": "closure",
        "next_step": "apply that observed method to the current open work next",
        "evidence_sufficient": True,
        "method_closed_peer_count": 2,
        "method_open_peer_count": 0,
        "method_pulse_recovered_count": 0,
        "method_pulse_worsened_count": 0,
        "method_barrier_closed_peer_count": 2,
        "method_barrier_open_peer_count": 0,
        "likely_next_basis": "barrier",
    }
    payload.update(overrides)
    return cr.PeerGuidanceEvidence(**payload)  # type: ignore[arg-type]

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_corpus_connection():
    cr.configure_connection(None)
    yield
    cr.configure_connection(None)


def _unknown_severity_barrier(customer: str, *, suffix: str) -> dict[str, str]:
    row = _peer_barrier(customer, resolution=PEER_METHOD, suffix=suffix)
    row.pop("SEVERITY_C", None)
    return row


def _sev_barrier(
    customer: str, *, suffix: str, severity: str, ab_status: str = "Closed"
) -> dict[str, str]:
    row = _peer_barrier(
        customer, resolution=PEER_METHOD, suffix=suffix, ab_status=ab_status
    )
    row["SEVERITY_C"] = severity
    return row


@pytest.mark.parametrize(
    ("raw", "band"),
    [
        ("Critical", "critical"),
        ("P1", "critical"),
        ("sev1", "critical"),
        ("1", "critical"),
        ("High", "high"),
        ("P2", "high"),
        ("2", "high"),
        ("Medium", "medium"),
        ("moderate", "medium"),
        ("P3", "medium"),
        ("Low", "low"),
        ("P4", "low"),
        ("4", "low"),
        ("informational", "low"),
        ("", ""),
        ("unknown", ""),
        ("10", ""),
        ("P10", ""),
    ],
)
def test_peer_severity_band_is_exact(raw: str, band: str) -> None:
    assert cr._peer_severity_band(raw) == band
    assert cr._peer_severity_band("Critical") != cr._peer_severity_band("High")


def test_schema_version_bumped_for_barrier_severity() -> None:
    assert knowledge_schema.SCHEMA_VERSION >= 4
    assert '"severity"' in knowledge_schema._DDL_BARRIERS


def test_indexer_persists_severity_c(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _sev_barrier("Peer A", suffix="A", severity="Critical"),
                _sev_barrier("Peer B", suffix="B", severity="Low"),
                _unknown_severity_barrier("Peer C", suffix="C"),
            ],
        },
    )
    try:
        rows = connection.execute(
            'SELECT cust."name" AS "name", b."severity" AS "severity" '
            'FROM "barriers" b JOIN "customers" cust ON cust."id" = b."customer_id" '
            'WHERE cust."name" IN (?, ?, ?) '
            'ORDER BY cust."name" ASC;',
            ("Peer A", "Peer B", "Peer C"),
        ).fetchall()
        by_name = {str(row[0]): row[1] for row in rows}
        assert by_name["Peer A"] == "Critical"
        assert by_name["Peer B"] == "Low"
        assert by_name["Peer C"] in (None, "")
    finally:
        connection.close()


def test_unanimous_critical_still_publishes_closure(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _sev_barrier("Peer A", suffix="A", severity="Critical"),
                _sev_barrier("Peer B", suffix="B", severity="Critical"),
            ],
        },
        copy_bundled_barriers=False,
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Omega",
            target_severity="Critical",
        )
        assert evidence.likely_next == "closure"
        assert evidence.evidence_sufficient is True
        assert evidence.incomparable_severity is False
        assert evidence.comparable_severity_band == "critical"
        assert evidence.comparable_method_peer_count == 2
        view = rcc.build_peer_guidance_view(evidence)
        assert view["status"] == "actionable"
        assert view["ready_for_live_cisco"] is False
    finally:
        connection.close()


def test_unknown_severity_keeps_pre_r180_behavior(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _unknown_severity_barrier("Peer A", suffix="A"),
                _unknown_severity_barrier("Peer B", suffix="B"),
            ],
        },
        copy_bundled_barriers=False,
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Zeta",
            target_severity="",
        )
        assert evidence.likely_next == "closure"
        assert evidence.incomparable_severity is False
        assert evidence.comparable_severity_band == ""
        assert evidence.comparable_method_peer_count == 2
    finally:
        connection.close()


def test_mixed_critical_and_low_withholds_likely_next(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _sev_barrier("Peer A", suffix="A", severity="Critical"),
                _sev_barrier("Peer B", suffix="B", severity="Low"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.dominant_method_text == PEER_METHOD
        assert evidence.dominant_method_peers == 2
        assert evidence.likely_next == "insufficient"
        assert evidence.next_step == ""
        assert evidence.incomparable_severity is True
        assert evidence.severity_comparable is False
        view = rcc.build_peer_guidance_view(evidence)
        assert set(view) == VIEW_KEYS
        assert view["status"] == "method_only"
        assert view["insufficient_reason"] == "incomparable_severity"
        assert "comparable-severity" in str(view["insufficient_copy"])
        assert view["likely_next"] == ""
        assert view["next_step"] == ""
        assert view["ready_for_live_cisco"] is False
        lines = rcc.format_peer_guidance_scan_lines(evidence)
        assert any("comparable-severity" in line for line in lines)
        assert not any(line.startswith("Next step:") for line in lines)
    finally:
        connection.close()


def test_critical_is_not_high(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _sev_barrier("Peer A", suffix="A", severity="Critical"),
                _sev_barrier("Peer B", suffix="B", severity="High"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.incomparable_severity is True
        assert evidence.likely_next == "insufficient"
    finally:
        connection.close()


def test_p1_and_critical_are_the_same_band(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _sev_barrier("Peer A", suffix="A", severity="P1"),
                _sev_barrier("Peer B", suffix="B", severity="Critical"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.incomparable_severity is False
        assert evidence.comparable_severity_band == "critical"
        assert evidence.likely_next == "closure"
    finally:
        connection.close()


def test_target_mismatch_withholds_likely_next(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _sev_barrier("Peer A", suffix="A", severity="Low"),
                _sev_barrier("Peer B", suffix="B", severity="Low"),
            ],
        },
    )
    try:
        # Synthetic Alpha's authentication barrier is Critical in the
        # bundled fixture — auto-lookup must fail closed against Low peers.
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.dominant_method_peers == 2
        assert evidence.incomparable_severity is True
        assert evidence.likely_next == "insufficient"
        view = rcc.public_peer_guidance_view(rcc.build_peer_guidance_view(evidence))
        assert view["status"] == "method_only"
        assert view["insufficient_reason"] == "incomparable_severity"
        assert view["ready_for_live_cisco"] is False
    finally:
        connection.close()


def test_unknown_peers_excluded_when_one_band_is_known(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _sev_barrier("Peer A", suffix="A", severity="Critical"),
                _unknown_severity_barrier("Peer B", suffix="B"),
            ],
        },
        copy_bundled_barriers=False,
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Omega",
            target_severity="Critical",
        )
        assert evidence.incomparable_severity is False
        assert evidence.comparable_severity_band == "critical"
        assert evidence.comparable_method_peer_count == 1
        assert evidence.likely_next == "insufficient"
        assert evidence.dominant_method_text == PEER_METHOD
        view = rcc.build_peer_guidance_view(evidence)
        assert view["status"] == "method_only"
        assert view["insufficient_reason"] == "mixed_evidence"
    finally:
        connection.close()


def test_receipt_fingerprint_includes_severity_band(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _sev_barrier("Peer A", suffix="A", severity="Critical"),
                _sev_barrier("Peer B", suffix="B", severity="Critical"),
            ],
        },
    )
    try:
        matched = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        mixed = cr.PeerGuidanceEvidence(
            theme=matched.theme,
            technology=matched.technology,
            peer_customer_count=matched.peer_customer_count,
            resolved_peer_count=matched.resolved_peer_count,
            dominant_method_text=matched.dominant_method_text,
            dominant_method_peers=matched.dominant_method_peers,
            closed_peer_count=matched.closed_peer_count,
            open_peer_count=matched.open_peer_count,
            close_time_median_days=matched.close_time_median_days,
            pulse_recovered_count=matched.pulse_recovered_count,
            pulse_worsened_count=matched.pulse_worsened_count,
            likely_next="insufficient",
            next_step="",
            evidence_sufficient=True,
            exclusion_digest=matched.exclusion_digest,
            incomparable_severity=True,
            comparable_severity_band="",
            comparable_method_peer_count=0,
        )
        sid_ok = rcc.peer_guidance_source_id(matched)
        sid_mixed = rcc.peer_guidance_source_id(mixed)
        assert sid_ok.startswith("CORPUS:PG-")
        assert sid_mixed.startswith("CORPUS:PG-")
        assert sid_ok != sid_mixed
    finally:
        connection.close()


def test_constructed_evidence_defaults_remain_comparable() -> None:
    view = rcc.build_peer_guidance_view(_evidence())
    assert view["status"] == "actionable"
    assert view["ready_for_live_cisco"] is False


def _v3_barriers_connection(path: Path) -> sqlite3.Connection:
    """Pre-Round-180 corpus shape: schema_meta=3, barriers without severity."""
    connection = sqlite3.connect(str(path))
    connection.execute(
        'CREATE TABLE "schema_meta" ('
        ' "id" INTEGER PRIMARY KEY CHECK ("id" = 1),'
        ' "version" INTEGER NOT NULL,'
        ' "migrated_at" TEXT NOT NULL)'
    )
    connection.execute(
        'INSERT INTO "schema_meta" ("id", "version", "migrated_at") '
        "VALUES (1, 3, '2026-01-01T00:00:00Z')"
    )
    connection.execute(
        'CREATE TABLE "barriers" ('
        ' "id" INTEGER PRIMARY KEY AUTOINCREMENT,'
        ' "customer_id" INTEGER NOT NULL,'
        ' "technology" TEXT,'
        ' "theme" TEXT NOT NULL,'
        ' "status" TEXT,'
        ' "is_open" INTEGER,'
        ' "first_seen" TEXT,'
        ' "last_seen" TEXT,'
        ' "occurrences" INTEGER NOT NULL DEFAULT 1,'
        ' "source_file_id" INTEGER)'
    )
    connection.commit()
    return connection


def test_apply_schema_does_not_stamp_v3_forward() -> None:
    connection = _v3_barriers_connection(Path(":memory:"))
    try:
        assert knowledge_schema.get_persisted_schema_version(connection) == 3
        returned = knowledge_schema.apply_schema(connection)
        assert returned == 3
        assert knowledge_schema.get_persisted_schema_version(connection) == 3
        assert knowledge_schema.needs_rebuild(connection) is True
        names = knowledge_schema._table_column_names(connection, "barriers")
        assert "severity" in names
    finally:
        connection.close()


def test_index_folder_rebuilds_v3_and_stamps_schema_v4(tmp_path: Path) -> None:
    db_path = tmp_path / "corpus.db"
    connection = _v3_barriers_connection(db_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    try:
        index_folder(connection, empty_root)
        assert knowledge_schema.get_persisted_schema_version(connection) == 4
        assert knowledge_schema.needs_rebuild(connection) is False
        names = knowledge_schema._table_column_names(connection, "barriers")
        assert "severity" in names
    finally:
        connection.close()


def test_source_shape_round180_markers() -> None:
    schema = ROOT / "knowledge_schema.py"
    indexer = ROOT / "corpus_indexer.py"
    retriever = ROOT / "corpus_retriever.py"
    context = ROOT / "report_corpus_context.py"
    workflow = ROOT / ".github" / "workflows" / "pr-quality.yml"
    this_test = Path(__file__).read_text(encoding="utf-8")
    assert_in_source(schema.read_text(encoding="utf-8"), "Round 180", label="schema")
    assert_in_source(indexer.read_text(encoding="utf-8"), "barrier_severity", label="idx")
    assert_in_source(
        retriever.read_text(encoding="utf-8"), "_peer_severity_band", label="ret"
    )
    assert_in_source(
        context.read_text(encoding="utf-8"), "incomparable_severity", label="ctx"
    )
    assert "pull_request:" in workflow.read_text(encoding="utf-8")
    assert "SCHEMA_VERSION: int = 4" in schema.read_text(encoding="utf-8")
    import_lines = [
        line
        for line in this_test.splitlines()
        if line.lstrip().startswith(("import ", "from "))
    ]
    joined = "\n".join(import_lines)
    assert "test_round175_peer_guidance_knowledge" not in joined
    assert "test_round177_peer_guidance_surfaces" not in joined
