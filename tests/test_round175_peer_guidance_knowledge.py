"""Round 175: deepen existing guidance with peer resolutions and likely-next.

This is knowledge-layer work on the surfaces that already exist
(support_themes, support_operating_health, Ask AI corpus block).  It is
not a new report, insight, or UI.  Published text stays aggregate-only
and fail-closed when peer evidence is thin.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import ask_ai_corpus
import corpus_retriever as cr
import decision_report_delivery as delivery
import report_corpus_context
from corpus_indexer import index_folder, open_corpus_db


AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "round17"
OWN_RESOLUTION = (
    "Rotated service token and updated documentation for SSO setup."
)
PEER_METHOD = "Rotated service token and updated documentation for SSO setup."
CONFIG_METHOD = (
    "Restored tenant-level policy overrides from last-known-good snapshot."
)
NO_CORPUS_THEMES_FP = (
    "12d6607df3ed91b85863a40a4c54ee6218e8bc1a1cc876ee6b82b0fea66ebe1c"
)
NO_CORPUS_HEALTH_FP = (
    "4d747829ac5008739c9741ab61506655bca45bf8c202a8e1b615214ab336dca3"
)


@pytest.fixture(autouse=True)
def _clear_corpus_connection():
    cr.configure_connection(None)
    yield
    cr.configure_connection(None)


def _copy_round17(corpus_root: Path) -> None:
    corpus_root.mkdir(parents=True, exist_ok=True)
    for name in (
        "synthetic_cases.csv",
        "synthetic_barriers.csv",
        "synthetic_pulse.csv",
    ):
        shutil.copy2(FIXTURES / name, corpus_root / name)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _peer_barrier(customer: str, *, resolution: str, suffix: str) -> dict[str, str]:
    return {
        "customer_name": customer,
        "SUBJECT_C": f"Authentication SSO login errors {suffix}",
        "SEVERITY_C": "Critical",
        "AB_STATUS_C": "Closed",
        "ID": f"AB-{suffix}",
        "technology": "security",
        "theme": "authentication",
        "resolution": resolution,
    }


def _peer_case(
    customer: str,
    *,
    suffix: str,
    status: str = "Closed",
    opened: str = "2026-01-01T00:00:00Z",
    closed: str = "2026-01-10T00:00:00Z",
) -> dict[str, str]:
    closed_at = closed if status == "Closed" else ""
    return {
        "customer_name": customer,
        "Case Number": f"PEER-{suffix}",
        "Title": f"Authentication failure during {suffix} onboarding",
        "Severity": "P2",
        "Case Status": status,
        "Date/Time Opened": opened,
        "Date/Time Closed": closed_at,
        "Resolution Summary": "Closed after token rotation.",
        "technology": "security",
    }


def _index_corpus(tmp_path: Path, extra_rows: dict[str, list[dict[str, str]]] | None = None):
    corpus_root = tmp_path / "corpus"
    _copy_round17(corpus_root)
    extras = extra_rows or {}
    if extras.get("barriers"):
        _write_csv(corpus_root / "peer_barriers.csv", extras["barriers"])
    if extras.get("cases"):
        _write_csv(corpus_root / "peer_cases.csv", extras["cases"])
    if extras.get("pulse"):
        _write_csv(corpus_root / "peer_pulse.csv", extras["pulse"])
    if extras.get("unresolved_barriers"):
        _write_csv(corpus_root / "peer_c_barriers.csv", extras["unresolved_barriers"])
    connection = open_corpus_db(tmp_path / "corpus.db")
    index_folder(connection, corpus_root)
    cr.configure_connection(connection)
    return connection


@pytest.fixture
def configured_round17_corpus(tmp_path: Path):
    connection = _index_corpus(tmp_path)
    try:
        yield connection
    finally:
        cr.configure_connection(None)
        connection.close()


@pytest.fixture
def configured_peer_corpus(tmp_path: Path):
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "unresolved_barriers": [
                {
                    "customer_name": "Peer C",
                    "SUBJECT_C": "Authentication SSO login errors C",
                    "SEVERITY_C": "High",
                    "AB_STATUS_C": "Open",
                    "ID": "AB-C",
                    "technology": "security",
                    "theme": "authentication",
                }
            ],
            "cases": [
                _peer_case("Peer A", suffix="A"),
                _peer_case("Peer B", suffix="B"),
                _peer_case("Peer C", suffix="C"),
            ],
        },
    )
    try:
        yield connection
    finally:
        cr.configure_connection(None)
        connection.close()


def _team_data(*, customer: str = "Synthetic Alpha", technology: str = "security"):
    return {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame(
                [{"SUBSCRIPTION_ID": "S1", "BU_NAME": customer, "STATUS_C": "Active"}]
            ),
            "action_plans": pd.DataFrame(
                [
                    {
                        "ID": "AP1",
                        "BU_NAME": customer,
                        "SUBJECT_C": "Review authentication",
                        "STATUS_C": "Open",
                    }
                ]
            ),
            "adoption_barriers": pd.DataFrame(
                [
                    {
                        "ID": "AB1",
                        "BU_NAME": customer,
                        "SEVERITY_C": "High",
                        "AB_STATUS_C": "Open",
                        "OPEN_DATE_C": "2026-07-01",
                    }
                ]
            ),
            "customer_pulse": pd.DataFrame(
                [
                    {
                        "ID": "CP1",
                        "BU_NAME": customer,
                        "SCORE__C": 8,
                        "PULSE_DATE_C": "2026-07-20",
                    }
                ]
            ),
            "tac_cases": pd.DataFrame(
                [
                    {
                        "SR Number": "T1",
                        "BU_NAME": customer,
                        "Severity": "P2",
                        "Case Status": "Closed",
                        "Date/Time Opened": "2026-07-20",
                        "Date/Time Closed": "2026-07-25",
                        "Tech.": technology,
                    },
                    {
                        "SR Number": "T2",
                        "BU_NAME": customer,
                        "Severity": "P3",
                        "Case Status": "Open",
                        "Date/Time Opened": "2026-07-29",
                        "Tech.": technology,
                    },
                ]
            ),
            "success_priorities": pd.DataFrame(),
        }
    }


def _build_report(*, customer: str = "Synthetic Alpha", technology: str = "security"):
    return delivery.build_report_facts(
        _team_data(customer=customer, technology=technology),
        report_type="Leader",
        scope_type="team",
        scope_value="Alex Rivera's Team",
        manager_name="Alex Rivera",
        days=90,
        as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(),
        data_as_of_state="available",
        data_mode="offline_fixture",
        live_validation_performed=False,
    )


def _receipt_rows(sheets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    info = sheets["Report_Info"]
    return info.loc[
        info["Item"].fillna("").astype(str).str.startswith("Corpus_Retriever_Receipt:")
    ]


def _forbidden_leakage() -> tuple[str, ...]:
    return (
        "Peer A",
        "Peer B",
        "Peer C",
        "Peer D",
        "Peer E",
        "Peer F",
        "PEER-A",
        "PEER-B",
        "PEER-C",
        "PEER-D",
        "CFG-E",
        "CFG-F",
        "TAC9001",
        "synthetic_barriers.csv",
        "peer_barriers.csv",
        "jestory",
        "@cisco",
    )


def test_round17_thin_peers_do_not_invent_likely_next(configured_round17_corpus) -> None:
    facts = _build_report()
    themes = facts["decision_insights"]["support_themes"]
    health = facts["decision_insights"]["support_operating_health"]
    combined = themes["paragraph_text"] + "\n" + health["paragraph_text"]
    assert "likely-next" not in combined
    assert "Observed-in-peers" not in combined
    assert "peer-observed" not in combined
    assert OWN_RESOLUTION.rstrip(".") in themes["paragraph_text"]
    assert OWN_RESOLUTION.rstrip(".") in health["paragraph_text"]
    theme_methods = [
        item["method"] for item in themes["corpus_claims"][0]["receipt_payload"]["retrievals"]
    ]
    health_methods = [
        item["method"] for item in health["corpus_claims"][0]["receipt_payload"]["retrievals"]
    ]
    assert theme_methods == [
        "corpus_retriever.get_customer_history",
        "corpus_retriever.get_recurring_themes",
        "corpus_retriever.get_resolutions_for",
    ]
    assert health_methods == theme_methods


def test_get_resolutions_for_is_non_empty_after_indexer_alias(configured_round17_corpus) -> None:
    out = cr.get_resolutions_for("authentication", "security", limit=5)
    assert out
    assert any("token" in item.method_text.lower() for item in out)


def test_operating_health_publishes_peer_likely_next(configured_peer_corpus) -> None:
    facts = _build_report()
    insight = facts["decision_insights"]["support_operating_health"]
    text = insight["paragraph_text"]
    assert "Observed-in-peers:" in text
    assert "likely-next is closure after that method (not a certainty)" in text
    assert "Next step: try the same method on the current open work" in text
    assert "will " not in text.lower()
    assert "certainly" not in text.lower()
    methods = [
        item["method"] for item in insight["corpus_claims"][0]["receipt_payload"]["retrievals"]
    ]
    assert methods == [
        "corpus_retriever.get_customer_history",
        "corpus_retriever.get_recurring_themes",
        "corpus_retriever.get_resolutions_for",
        "corpus_retriever.get_peer_guidance_evidence",
    ]
    serialized = json.dumps(
        insight["corpus_claims"][0]["receipt_payload"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    for leaked in _forbidden_leakage():
        assert leaked not in serialized
        assert leaked not in text


def test_themes_cite_peer_observed_resolution_when_own_history_is_empty(
    configured_peer_corpus,
) -> None:
    facts = _build_report(customer="Peer C")
    insight = facts["decision_insights"]["support_themes"]
    text = insight["paragraph_text"]
    assert "peer-observed resolution was" in text
    assert "Rotated service token" in text
    assert "a previously observed resolution was" not in text
    methods = [
        item["method"] for item in insight["corpus_claims"][0]["receipt_payload"]["retrievals"]
    ]
    assert methods[-1] == "corpus_retriever.get_peer_guidance_evidence"
    for leaked in (
        "Peer A",
        "Peer B",
        "PEER-A",
        "PEER-B",
        "PEER-C",
        "TAC9001",
        "synthetic_barriers.csv",
        "peer_barriers.csv",
    ):
        assert leaked not in text


def test_one_peer_and_method_tie_omit_likely_next(tmp_path: Path) -> None:
    one_peer = _index_corpus(
        tmp_path / "one",
        {
            "barriers": [_peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A")],
            "cases": [_peer_case("Peer A", suffix="A")],
        },
    )
    try:
        facts = _build_report()
        text = facts["decision_insights"]["support_operating_health"]["paragraph_text"]
        assert "likely-next" not in text
        assert "Observed-in-peers" not in text
    finally:
        cr.configure_connection(None)
        one_peer.close()

    tied = _index_corpus(
        tmp_path / "tie",
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier(
                    "Peer B",
                    resolution="Restored tenant-level policy overrides from last-known-good snapshot.",
                    suffix="B",
                ),
            ],
            "cases": [
                _peer_case("Peer A", suffix="A"),
                _peer_case("Peer B", suffix="B"),
            ],
        },
    )
    try:
        facts = _build_report()
        text = facts["decision_insights"]["support_operating_health"]["paragraph_text"]
        assert "likely-next" not in text
        assert "Observed-in-peers" not in text
    finally:
        cr.configure_connection(None)
        tied.close()


def test_ask_ai_emits_insufficient_line_on_thin_round17(configured_round17_corpus) -> None:
    out = ask_ai_corpus.build_corpus_block(
        question="How is customer Synthetic Alpha doing?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    assert "CORPUS_PEER_GUIDANCE:" in out.block
    assert "insufficient_peer_evidence=true" in out.block
    assert "no likely-next is asserted" in out.block
    assert "Observed-in-peers" not in out.block
    assert out.stats.get("insufficient_peer_evidence") is True
    assert out.stats.get("likely_next") == "insufficient"


def test_ask_ai_emits_peer_likely_next_when_evidence_is_sufficient(
    configured_peer_corpus,
) -> None:
    out = ask_ai_corpus.build_corpus_block(
        question="How is customer Synthetic Alpha doing?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    assert "CORPUS_PEER_GUIDANCE:" in out.block
    assert "Observed-in-peers:" in out.block
    assert "likely-next is closure after that method (not a certainty)" in out.block
    assert out.stats.get("insufficient_peer_evidence") is False
    assert out.stats.get("likely_next") == "closure"
    for leaked in _forbidden_leakage():
        assert leaked not in out.block


def test_peer_guidance_evidence_is_aggregate_only(configured_peer_corpus) -> None:
    evidence = cr.get_peer_guidance_evidence(
        "authentication",
        "security",
        exclude_customer="Synthetic Alpha",
    )
    assert evidence.evidence_sufficient is True
    assert evidence.dominant_method_peers >= 2
    assert evidence.likely_next == "closure"
    payload = asdict(evidence)
    blob = json.dumps(payload, sort_keys=True)
    assert "name" not in payload
    assert "email" not in payload
    assert "case_number" not in payload
    for leaked in _forbidden_leakage():
        assert leaked not in blob


def test_no_corpus_operating_health_oracle_remains_byte_stable() -> None:
    cr.configure_connection(None)
    facts = _build_report()
    assert "corpus_claims" not in facts["decision_insights"]["support_themes"]
    assert "corpus_claims" not in facts["decision_insights"]["support_operating_health"]
    assert delivery.fact_contract_fingerprint(facts) == NO_CORPUS_HEALTH_FP
    assert NO_CORPUS_THEMES_FP  # R172 owns the open-TAC themes oracle


def test_format_helpers_fail_closed_when_thin() -> None:
    empty = cr.PeerGuidanceEvidence(
        theme="authentication",
        technology="security",
        peer_customer_count=1,
        resolved_peer_count=1,
        dominant_method_text=PEER_METHOD,
        dominant_method_peers=1,
        closed_peer_count=1,
        open_peer_count=0,
        close_time_median_days=3.0,
        pulse_recovered_count=0,
        pulse_worsened_count=0,
        likely_next="insufficient",
        next_step="",
        evidence_sufficient=False,
    )
    assert report_corpus_context.format_peer_guidance_clause(
        empty, include_likely_next=True
    ) == ""
    assert "insufficient_peer_evidence=true" in (
        report_corpus_context.format_peer_guidance_ask_ai_line(empty)
    )
    assert report_corpus_context.format_peer_guidance_clause(
        None, include_likely_next=True
    ) == ""


def test_receipt_hash_includes_peer_clause(configured_peer_corpus) -> None:
    facts = _build_report()
    claim = facts["decision_insights"]["support_operating_health"]["corpus_claims"][0]
    serialized = json.dumps(
        claim["receipt_payload"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == claim["receipt_sha256"]
    assert "get_peer_guidance_evidence" in serialized
    sheets = delivery.build_source_data_sheets(facts)
    assert not _receipt_rows(sheets).empty


def _peer_pulse(customer: str, *, first: str, last: str, suffix: str) -> list[dict[str, str]]:
    return [
        {
            "customer_name": customer,
            "snapshot_date": "2026-01-15",
            "CUSTOMER_PULSE__C": first,
            "COMMENTS__C": f"Peer pulse {suffix} start",
        },
        {
            "customer_name": customer,
            "snapshot_date": "2026-03-15",
            "CUSTOMER_PULSE__C": last,
            "COMMENTS__C": f"Peer pulse {suffix} end",
        },
    ]


def test_method_closed_count_is_coupled_not_theme_wide(configured_peer_corpus) -> None:
    evidence = cr.get_peer_guidance_evidence(
        "authentication",
        "security",
        exclude_customer="Synthetic Alpha",
    )
    assert evidence.method_closed_peer_count == 2
    assert evidence.closed_peer_count == 3
    assert evidence.method_closed_peer_count < evidence.closed_peer_count
    assert evidence.likely_next == "closure"


def test_uncoupled_closures_do_not_claim_closed_after_method(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "unresolved_barriers": [
                {
                    "customer_name": "Peer C",
                    "SUBJECT_C": "Authentication SSO login errors C",
                    "SEVERITY_C": "High",
                    "AB_STATUS_C": "Closed",
                    "ID": "AB-C",
                    "technology": "security",
                    "theme": "authentication",
                },
                {
                    "customer_name": "Peer D",
                    "SUBJECT_C": "Authentication SSO login errors D",
                    "SEVERITY_C": "High",
                    "AB_STATUS_C": "Closed",
                    "ID": "AB-D",
                    "technology": "security",
                    "theme": "authentication",
                },
            ],
            "cases": [
                _peer_case("Peer A", suffix="A", status="Open"),
                _peer_case("Peer B", suffix="B", status="Open"),
                _peer_case("Peer C", suffix="C"),
                _peer_case("Peer D", suffix="D"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.dominant_method_peers == 2
        assert evidence.method_open_peer_count == 2
        assert evidence.method_closed_peer_count == 0
        assert evidence.closed_peer_count >= 2
        assert evidence.likely_next == "remains_open"
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "remain open after" in clause
        assert "closed after" not in clause
        facts = _build_report()
        text = facts["decision_insights"]["support_operating_health"]["paragraph_text"]
        assert "closed after" not in text
        if "Observed-in-peers:" in text:
            assert "remain open after" in text
            assert "likely-next is remaining open" in text
        for leaked in _forbidden_leakage():
            assert leaked not in text
    finally:
        cr.configure_connection(None)
        connection.close()


def test_closures_without_dominant_method_fail_closed(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier(
                    "Peer B",
                    resolution="Restored tenant-level policy overrides from last-known-good snapshot.",
                    suffix="B",
                ),
            ],
            "cases": [
                _peer_case("Peer A", suffix="A"),
                _peer_case("Peer B", suffix="B"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.dominant_method_peers == 0
        assert evidence.closed_peer_count >= 2
        assert evidence.likely_next == "insufficient"
        assert evidence.evidence_sufficient is False
        facts = _build_report()
        text = facts["decision_insights"]["support_operating_health"]["paragraph_text"]
        assert "likely-next" not in text
        assert "Observed-in-peers" not in text
        assert "closed after" not in text
    finally:
        cr.configure_connection(None)
        connection.close()


def test_pulse_worsening_is_method_scoped(tmp_path: Path) -> None:
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
        assert evidence.likely_next == "pulse_worsening"
        assert evidence.method_pulse_worsened_count == 2
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "worse pulse" in clause
        assert "not a certainty" in clause
        assert "will " not in clause.casefold()
    finally:
        cr.configure_connection(None)
        connection.close()


def test_pulse_recovery_is_method_scoped(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "unresolved_barriers": [
                {
                    "customer_name": "Peer C",
                    "SUBJECT_C": "Authentication SSO login errors C",
                    "SEVERITY_C": "High",
                    "AB_STATUS_C": "Open",
                    "ID": "AB-C",
                    "technology": "security",
                    "theme": "authentication",
                }
            ],
            "pulse": (
                _peer_pulse("Peer A", first="red", last="green", suffix="A")
                + _peer_pulse("Peer B", first="red", last="green", suffix="B")
                + _peer_pulse("Peer C", first="red", last="green", suffix="C")
            ),
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.likely_next == "pulse_recovery"
        assert evidence.method_pulse_recovered_count == 2
        assert evidence.pulse_recovered_count == 3
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "recovered pulse" in clause
        assert "likely-next is pulse recovery (not a certainty)" in clause
        assert "keep the current method and watch pulse" in clause
        assert "will " not in clause.casefold()
        facts = _build_report()
        text = facts["decision_insights"]["support_operating_health"]["paragraph_text"]
        if "Observed-in-peers:" in text:
            assert "recovered pulse" in text
            assert "closed after" not in text
    finally:
        cr.configure_connection(None)
        connection.close()


def test_open_cases_block_pulse_recovery(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "cases": [
                _peer_case("Peer A", suffix="A", status="Open"),
                _peer_case("Peer B", suffix="B", status="Open"),
            ],
            "pulse": (
                _peer_pulse("Peer A", first="red", last="green", suffix="A")
                + _peer_pulse("Peer B", first="red", last="green", suffix="B")
            ),
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.method_pulse_recovered_count == 2
        assert evidence.method_open_peer_count == 2
        assert evidence.likely_next == "remains_open"
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "remain open after" in clause
        assert "pulse recovery" not in clause
    finally:
        cr.configure_connection(None)
        connection.close()


def test_pulse_recovery_and_worsening_tie_fail_closed(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
                _peer_barrier("Peer C", resolution=PEER_METHOD, suffix="C"),
                _peer_barrier("Peer D", resolution=PEER_METHOD, suffix="D"),
            ],
            "pulse": (
                _peer_pulse("Peer A", first="red", last="green", suffix="A")
                + _peer_pulse("Peer B", first="red", last="green", suffix="B")
                + _peer_pulse("Peer C", first="green", last="red", suffix="C")
                + _peer_pulse("Peer D", first="green", last="red", suffix="D")
            ),
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.dominant_method_peers == 4
        assert evidence.method_pulse_recovered_count == 2
        assert evidence.method_pulse_worsened_count == 2
        assert evidence.likely_next == "insufficient"
        assert evidence.evidence_sufficient is True
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert clause == ""
        fallback = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=False, prefer_peer_resolution=True
        )
        assert "peer-observed resolution was" in fallback
        assert "likely-next" not in fallback
    finally:
        cr.configure_connection(None)
        connection.close()


def test_closure_clause_includes_observed_median_close_days(
    configured_peer_corpus,
) -> None:
    evidence = cr.get_peer_guidance_evidence(
        "authentication",
        "security",
        exclude_customer="Synthetic Alpha",
    )
    assert evidence.likely_next == "closure"
    assert evidence.close_time_median_days == 9.0
    clause = report_corpus_context.format_peer_guidance_clause(
        evidence, include_likely_next=True
    )
    assert "(median 9d)" in clause
    assert "closed after" in clause
    assert "will " not in clause.casefold()


def test_peer_close_median_uses_per_peer_not_last_case(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "cases": [
                _peer_case(
                    "Peer A",
                    suffix="A1",
                    opened="2026-01-01T00:00:00Z",
                    closed="2026-01-06T00:00:00Z",
                ),
                _peer_case(
                    "Peer A",
                    suffix="A2",
                    opened="2026-01-01T00:00:00Z",
                    closed="2026-01-16T00:00:00Z",
                ),
                _peer_case(
                    "Peer B",
                    suffix="B",
                    opened="2026-01-01T00:00:00Z",
                    closed="2026-01-10T00:00:00Z",
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
        # Peer A median(5d, 15d)=10.0; Peer B=9.0; overall median=9.5.
        # Last-wins would have been 15d for Peer A and 12.0 overall.
        assert evidence.close_time_median_days == 9.5
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "(median 9.5d)" in clause
    finally:
        cr.configure_connection(None)
        connection.close()


def test_insight_sentence_cap_drops_median_before_dropping_clause(monkeypatch) -> None:
    evidence = cr.PeerGuidanceEvidence(
        theme="authentication",
        technology="security",
        peer_customer_count=2,
        resolved_peer_count=2,
        dominant_method_text=PEER_METHOD,
        dominant_method_peers=2,
        closed_peer_count=2,
        open_peer_count=0,
        close_time_median_days=9.0,
        pulse_recovered_count=0,
        pulse_worsened_count=0,
        likely_next="closure",
        next_step="try the same method on the current open work",
        evidence_sufficient=True,
        method_closed_peer_count=2,
        method_open_peer_count=0,
    )
    clause_full = report_corpus_context.format_peer_guidance_clause(
        evidence, include_likely_next=True, include_median_close=True
    )
    clause_short = report_corpus_context.format_peer_guidance_clause(
        evidence, include_likely_next=True, include_median_close=False
    )
    assert "(median 9d)" in clause_full
    assert "(median 9d)" not in clause_short
    # Fit the short clause exactly at the 520-char insight cap.
    prefix_len = 520 - len(f"; {clause_short}.")
    long_base = ("x" * prefix_len) + "."
    monkeypatch.setattr(
        report_corpus_context,
        "_r175_load_peer_evidence",
        lambda *_args, **_kwargs: evidence,
    )
    combined, retrievals = report_corpus_context._r175_maybe_append_peer_clause(
        long_base,
        [],
        customer="Synthetic Alpha",
        theme="authentication",
        technology="security",
        include_likely_next=True,
        prefer_peer_resolution=False,
    )
    assert "Observed-in-peers:" in combined
    assert "likely-next is closure after that method (not a certainty)" in combined
    assert "(median 9d)" not in combined
    assert len(combined) <= 520
    assert retrievals[0]["median_close_published"] is False
    assert retrievals[0]["close_time_median_days"] == 9.0
    assert report_corpus_context._r175_median_close_fragment(None) == ""
    assert report_corpus_context._r175_median_close_fragment("n/a") == ""
    assert report_corpus_context._r175_median_close_fragment(-1) == ""
    assert report_corpus_context._r175_median_close_fragment(9.0) == " (median 9d)"
    assert report_corpus_context._r175_median_close_fragment(4.3) == " (median 4.3d)"


def test_pulse_csv_snapshot_date_orders_trajectory_not_insertion(tmp_path: Path) -> None:
    """Later-written earlier snapshot must not invert recovered vs worsened."""
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "pulse": [
                {
                    "customer_name": "Peer A",
                    "snapshot_date": "2026-03-15",
                    "CUSTOMER_PULSE__C": "green",
                    "COMMENTS__C": "Peer pulse A later recovered",
                },
                {
                    "customer_name": "Peer A",
                    "snapshot_date": "2026-01-15",
                    "CUSTOMER_PULSE__C": "red",
                    "COMMENTS__C": "Peer pulse A earlier down",
                },
                {
                    "customer_name": "Peer B",
                    "snapshot_date": "2026-03-15",
                    "CUSTOMER_PULSE__C": "green",
                    "COMMENTS__C": "Peer pulse B later recovered",
                },
                {
                    "customer_name": "Peer B",
                    "snapshot_date": "2026-01-15",
                    "CUSTOMER_PULSE__C": "red",
                    "COMMENTS__C": "Peer pulse B earlier down",
                },
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.likely_next == "pulse_recovery"
        assert evidence.method_pulse_recovered_count == 2
        assert evidence.method_pulse_worsened_count == 0
    finally:
        cr.configure_connection(None)
        connection.close()


def _delta_config_rows() -> list[dict[str, str]]:
    rows = []
    for index in range(5):
        rows.append(
            {
                "customer_name": "Synthetic Delta",
                "SUBJECT_C": f"Policy configuration drift {index}",
                "SEVERITY_C": "High",
                "AB_STATUS_C": "Open",
                "ID": f"AB-DELTA-CFG-{index}",
                "technology": "collaboration",
                "theme": "configuration",
            }
        )
    rows.append(
        {
            "customer_name": "Synthetic Delta",
            "SUBJECT_C": "Authentication SSO login errors Delta",
            "SEVERITY_C": "Critical",
            "AB_STATUS_C": "Open",
            "ID": "AB-DELTA-AUTH",
            "technology": "security",
            "theme": "authentication",
        }
    )
    return rows


@pytest.fixture
def configured_ranked_peer_corpus(tmp_path: Path):
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ]
            + _delta_config_rows(),
            "cases": [
                _peer_case("Peer A", suffix="A"),
                _peer_case("Peer B", suffix="B"),
            ],
        },
    )
    try:
        yield connection
    finally:
        cr.configure_connection(None)
        connection.close()


def test_ask_ai_ranks_sufficient_theme_not_first_barrier(
    configured_ranked_peer_corpus,
) -> None:
    history = cr.get_customer_history("Synthetic Delta")
    assert history.barriers[0].theme == "configuration"
    out = ask_ai_corpus.build_corpus_block(
        question="How is customer Synthetic Delta doing?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    assert out.stats.get("peer_theme") == "authentication"
    assert out.stats.get("likely_next") == "closure"
    assert out.stats.get("insufficient_peer_evidence") is False
    assert "Observed-in-peers:" in out.block
    assert "likely-next is closure after that method (not a certainty)" in out.block
    for leaked in _forbidden_leakage():
        assert leaked not in out.block


def test_ask_ai_source_no_longer_hardcodes_first_barrier() -> None:
    source = Path(__file__).resolve().parents[1] / "ask_ai_corpus.py"
    body = source.read_text(encoding="utf-8")
    assert "select_ranked_peer_guidance" in body
    assert "first_barrier = history.barriers[0]" not in body


def test_historical_context_omits_peer_clause_on_thin_round17(
    configured_round17_corpus,
) -> None:
    ctx = report_corpus_context.build_historical_context(
        ["Synthetic Alpha"], enabled=True
    )
    text = report_corpus_context.render_to_text(ctx)
    assert ctx.entries[0].peer_guidance_clause == ""
    assert "likely-next" not in text
    assert "Observed-in-peers" not in text


def test_historical_context_renders_ranked_peer_clause(
    configured_peer_corpus,
) -> None:
    ctx = report_corpus_context.build_historical_context(
        ["Synthetic Alpha"], enabled=True
    )
    text = report_corpus_context.render_to_text(ctx)
    assert "Observed-in-peers:" in text
    assert "likely-next is closure after that method (not a certainty)" in text
    assert "will " not in text.casefold()
    for leaked in (
        "Peer A",
        "Peer B",
        "Peer C",
        "PEER-A",
        "PEER-B",
        "PEER-C",
        "peer_barriers.csv",
        "jestory",
        "@cisco",
    ):
        assert leaked not in text


def _predictive_team(customer: str) -> dict:
    return {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame(
                [{"SUBSCRIPTION_ID": "S1", "BU_NAME": customer, "STATUS_C": "Active"}]
            ),
            "action_plans": pd.DataFrame(
                [{"ID": "AP1", "BU_NAME": customer, "SUBJECT_C": "t", "STATUS_C": "Open"}]
            ),
            "adoption_barriers": pd.DataFrame(
                [
                    {
                        "ID": "AB1",
                        "BU_NAME": customer,
                        "SEVERITY_C": "High",
                        "AB_STATUS_C": "Open",
                        "OPEN_DATE_C": "2026-06-01",
                        "sub_technology": "Webex Calling",
                    }
                ]
            ),
            "customer_pulse": pd.DataFrame(
                [
                    {
                        "ID": "CP-001",
                        "BU_NAME": customer,
                        "SCORE__C": 7.0,
                        "PULSE_DATE_C": "2026-04-15",
                    },
                    {
                        "ID": "CP-002",
                        "BU_NAME": customer,
                        "SCORE__C": 3.0,
                        "PULSE_DATE_C": "2026-07-20",
                    },
                ]
            ),
            "tac_cases": pd.DataFrame(
                [
                    {
                        "SR Number": "1",
                        "BU_NAME": customer,
                        "Customer": customer,
                        "Severity": "P1",
                        "Case Status": "Open",
                        "Date/Time Opened": "2026-07-25",
                    },
                    {
                        "SR Number": "2",
                        "BU_NAME": customer,
                        "Customer": customer,
                        "Severity": "P2",
                        "Case Status": "Closed",
                        "Date/Time Opened": "2026-06-10",
                        "Date/Time Closed": "2026-06-25",
                    },
                    {
                        "SR Number": "3",
                        "BU_NAME": customer,
                        "Customer": customer,
                        "Severity": "P3",
                        "Case Status": "Closed",
                        "Date/Time Opened": "2026-05-10",
                        "Date/Time Closed": "2026-05-25",
                    },
                ]
            ),
            "success_priorities": pd.DataFrame(),
        }
    }


def test_predictive_outlook_unchanged_on_thin_round17_corpus(
    configured_round17_corpus,
) -> None:
    facts = delivery.build_report_facts(
        _predictive_team("Acme"),
        report_type="Leader",
        scope_type="team",
        scope_value="Alex Rivera's Team",
        manager_name="Alex Rivera",
        days=90,
        as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(),
        data_as_of_state="available",
        data_mode="offline_fixture",
        live_validation_performed=False,
    )
    insight = facts["decision_insights"]["predictive_outlook"]
    assert "Critical Watch (76 pts)" in insight["paragraph_text"]
    assert "likely-next" not in insight["paragraph_text"]
    assert "Observed-in-peers" not in insight["paragraph_text"]
    assert "corpus_claims" not in insight


def test_predictive_outlook_appends_fail_closed_peer_clause(
    configured_peer_corpus,
) -> None:
    facts = delivery.build_report_facts(
        _predictive_team("Synthetic Alpha"),
        report_type="Leader",
        scope_type="team",
        scope_value="Alex Rivera's Team",
        manager_name="Alex Rivera",
        days=90,
        as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(),
        data_as_of_state="available",
        data_mode="offline_fixture",
        live_validation_performed=False,
    )
    insight = facts["decision_insights"]["predictive_outlook"]
    text = insight["paragraph_text"]
    assert "Critical Watch (76 pts)" in text
    assert "Observed-in-peers:" in text
    assert "likely-next is closure after that method (not a certainty)" in text
    assert "corpus_claims" not in insight
    sheets = delivery.build_source_data_sheets(facts)
    receipts = _receipt_rows(sheets)
    blob = receipts.to_csv(index=False) if not receipts.empty else ""
    assert "predictive" not in blob.casefold()
    assert "get_peer_guidance_evidence" not in str(insight)
    for leaked in _forbidden_leakage():
        assert leaked not in text


def test_customer_360_hides_peer_card_when_thin(
    client, monkeypatch, configured_round17_corpus
) -> None:
    from config import Config

    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", True, raising=False)
    resp = client.get("/customer/Synthetic%20Alpha")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "data-r175-peer-guidance" not in body
    assert "likely-next" not in body


def test_customer_360_renders_aggregate_peer_line(
    client, monkeypatch, configured_peer_corpus
) -> None:
    from config import Config

    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", True, raising=False)
    resp = client.get("/customer/Synthetic%20Alpha")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "data-r175-peer-guidance" in body
    assert "Observed-in-peers" in body
    assert "likely-next is closure after that method (not a certainty)" in body
    card_start = body.index("data-r175-peer-guidance")
    card = body[card_start : card_start + 2500]
    assert "will " not in card.casefold()
    for leaked in _forbidden_leakage():
        assert leaked not in card


def test_decide_peer_likely_next_fail_closed_matrix() -> None:
    """Round 175.3: ties and mixed close+worse-pulse are not a likely-next."""

    def decide(**overrides: int) -> str:
        payload = {
            "dominant_n": 4,
            "min_n": 2,
            "method_closed": 0,
            "method_open": 0,
            "method_pulse_recovered": 0,
            "method_pulse_worsened": 0,
        }
        payload.update(overrides)
        return cr._decide_peer_likely_next(**payload)

    assert decide(method_closed=2, method_open=2) == "insufficient"
    assert decide(method_closed=3, method_open=2) == "closure"
    assert decide(method_closed=2, method_open=3) == "remains_open"
    assert decide(method_closed=2, method_pulse_worsened=2) == "insufficient"
    assert decide(method_closed=2, method_pulse_recovered=2) == "closure"
    assert decide(method_open=2, method_pulse_recovered=2) == "remains_open"
    assert decide(method_pulse_recovered=2, method_pulse_worsened=2) == "insufficient"
    assert decide(method_pulse_recovered=2) == "pulse_recovery"
    assert decide(dominant_n=1, method_closed=1) == "insufficient"


def test_peer_durations_agree_omits_wide_spread() -> None:
    assert cr._peer_durations_agree((9.0, 9.0)) is True
    assert cr._peer_durations_agree((1.0, 15.0)) is True
    assert cr._peer_durations_agree((1.0, 15.1)) is False
    assert cr._peer_durations_agree((9.0,)) is False
    assert cr._peer_durations_agree(()) is False


def test_peer_pulse_direction_drops_stale_last_snapshot() -> None:
    close = datetime(2026, 1, 10, tzinfo=timezone.utc)
    stale = (
        (datetime(2026, 1, 1, tzinfo=timezone.utc), -1.0),
        (datetime(2026, 1, 5, tzinfo=timezone.utc), 1.0),
    )
    fresh = (
        (datetime(2026, 1, 1, tzinfo=timezone.utc), -1.0),
        (datetime(2026, 1, 15, tzinfo=timezone.utc), 1.0),
    )
    assert cr._peer_pulse_direction(stale, not_before=close) == ""
    assert cr._peer_pulse_direction(fresh, not_before=close) == "recovered"
    assert cr._peer_pulse_direction(stale, not_before=None) == "recovered"


def _peer_config_barrier(customer: str, *, suffix: str) -> dict[str, str]:
    return {
        "customer_name": customer,
        "SUBJECT_C": f"Policy configuration drift {suffix}",
        "SEVERITY_C": "High",
        "AB_STATUS_C": "Closed",
        "ID": f"AB-CFG-{suffix}",
        "technology": "collaboration",
        "theme": "configuration",
        "resolution": CONFIG_METHOD,
    }


def _peer_config_case(
    customer: str,
    *,
    suffix: str,
    status: str = "Closed",
    opened: str = "2026-01-01T00:00:00Z",
    closed: str = "2026-01-10T00:00:00Z",
) -> dict[str, str]:
    closed_at = closed if status == "Closed" else ""
    return {
        "customer_name": customer,
        "Case Number": f"CFG-{suffix}",
        "Title": f"Policy configuration drift during {suffix} rollout",
        "Severity": "P2",
        "Case Status": status,
        "Date/Time Opened": opened,
        "Date/Time Closed": closed_at,
        "Resolution Summary": "Closed after policy restore.",
        "technology": "collaboration",
    }


def test_closed_open_tie_among_method_peers_fails_closed(tmp_path: Path) -> None:
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
        assert evidence.likely_next == "insufficient"
        assert evidence.evidence_sufficient is True
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert clause == ""
        fallback = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=False, prefer_peer_resolution=True
        )
        assert "peer-observed resolution was" in fallback
        assert "likely-next" not in fallback
        assert "will " not in fallback.casefold()
    finally:
        cr.configure_connection(None)
        connection.close()


def test_closure_plus_worse_pulse_is_mixed_and_fails_closed(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "cases": [
                _peer_case("Peer A", suffix="A"),
                _peer_case("Peer B", suffix="B"),
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
        assert evidence.method_closed_peer_count == 2
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


def test_stale_pulse_before_close_is_not_recovery(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "cases": [
                _peer_case("Peer A", suffix="A"),
                _peer_case("Peer B", suffix="B"),
            ],
            "pulse": [
                {
                    "customer_name": "Peer A",
                    "snapshot_date": "2026-01-01",
                    "CUSTOMER_PULSE__C": "red",
                    "COMMENTS__C": "Peer pulse A before close down",
                },
                {
                    "customer_name": "Peer A",
                    "snapshot_date": "2026-01-05",
                    "CUSTOMER_PULSE__C": "green",
                    "COMMENTS__C": "Peer pulse A before close up",
                },
                {
                    "customer_name": "Peer B",
                    "snapshot_date": "2026-01-01",
                    "CUSTOMER_PULSE__C": "red",
                    "COMMENTS__C": "Peer pulse B before close down",
                },
                {
                    "customer_name": "Peer B",
                    "snapshot_date": "2026-01-05",
                    "CUSTOMER_PULSE__C": "green",
                    "COMMENTS__C": "Peer pulse B before close up",
                },
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.likely_next == "closure"
        assert evidence.method_pulse_recovered_count == 0
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "closed after" in clause
        assert "pulse recovery" not in clause
    finally:
        cr.configure_connection(None)
        connection.close()


def test_wide_close_spread_omits_observed_median(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "cases": [
                _peer_case(
                    "Peer A",
                    suffix="A",
                    opened="2026-01-01T00:00:00Z",
                    closed="2026-01-03T00:00:00Z",
                ),
                _peer_case(
                    "Peer B",
                    suffix="B",
                    opened="2026-01-01T00:00:00Z",
                    closed="2026-04-01T00:00:00Z",
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
        assert evidence.likely_next == "closure"
        assert evidence.close_time_median_days is None
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        assert "closed after" in clause
        assert "median" not in clause
        assert "will " not in clause.casefold()
    finally:
        cr.configure_connection(None)
        connection.close()


def test_ranked_guidance_prefers_clean_theme_over_tied_first_barrier(
    tmp_path: Path,
) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
                _peer_barrier("Peer C", resolution=PEER_METHOD, suffix="C"),
                _peer_config_barrier("Peer E", suffix="E"),
                _peer_config_barrier("Peer F", suffix="F"),
            ]
            + _delta_config_rows(),
            "cases": [
                _peer_case("Peer A", suffix="A", status="Closed"),
                _peer_case("Peer B", suffix="B", status="Open"),
                _peer_case("Peer C", suffix="C", status="Open"),
                _peer_config_case("Peer E", suffix="E"),
                _peer_config_case("Peer F", suffix="F"),
            ],
        },
    )
    try:
        history = cr.get_customer_history("Synthetic Delta")
        assert history.barriers[0].theme == "configuration"
        auth = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Delta",
        )
        config = cr.get_peer_guidance_evidence(
            "configuration",
            "collaboration",
            exclude_customer="Synthetic Delta",
        )
        assert auth.method_closed_peer_count == 2
        assert auth.method_open_peer_count == 2
        assert auth.likely_next == "insufficient"
        assert config.likely_next == "closure"
        ranked = report_corpus_context.select_ranked_peer_guidance(
            customer="Synthetic Delta",
            barriers=history.barriers,
            fallback_technology="security",
        )
        assert ranked is not None
        assert ranked.theme == "configuration"
        assert ranked.likely_next == "closure"
        out = ask_ai_corpus.build_corpus_block(
            question="How is customer Synthetic Delta doing?",
            technology="security",
            enabled=True,
            top_k=3,
        )
        assert out.stats.get("peer_theme") == "configuration"
        assert out.stats.get("likely_next") == "closure"
        assert "likely-next is closure after that method (not a certainty)" in out.block
        assert "will " not in out.block.casefold()
        for leaked in _forbidden_leakage():
            assert leaked not in out.block
    finally:
        cr.configure_connection(None)
        connection.close()
