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
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import ask_ai_corpus
import corpus_retriever as cr
import decision_report_delivery as delivery
import report_corpus_context
from ask_ai_grounded import _normalize_claim_id, _validate_claim_citations
from corpus_indexer import enumerate_corpus_files, index_folder, open_corpus_db


AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "round17"
OWN_RESOLUTION = (
    "Rotated service token and updated documentation for SSO setup."
)
PEER_METHOD = "Rotated service token and updated documentation for SSO setup."
CONFIG_METHOD = (
    "Restored tenant-level policy overrides from last-known-good snapshot."
)
PII_METHOD = (
    "Rotated service token for Peer A (jane@cisco.com, TAC9001, notes.csv)"
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


def _peer_barrier(
    customer: str, *, resolution: str, suffix: str, ab_status: str = "Closed"
) -> dict[str, str]:
    return {
        "customer_name": customer,
        "SUBJECT_C": f"Authentication SSO login errors {suffix}",
        "SEVERITY_C": "Critical",
        "AB_STATUS_C": ab_status,  # Round 176: blank = unknown, not guessed closed
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
    case_number: str | None = None,
) -> dict[str, str]:
    closed_at = closed if status == "Closed" else ""
    return {
        "customer_name": customer,
        "Case Number": case_number or f"PEER-{suffix}",
        "Title": f"Authentication failure during {suffix} onboarding",
        "Severity": "P2",
        "Case Status": status,
        "Date/Time Opened": opened,
        "Date/Time Closed": closed_at,
        "Resolution Summary": "Closed after token rotation.",
        "technology": "security",
    }


def _index_corpus(
    tmp_path: Path,
    extra_rows: dict[str, list[dict[str, str]]] | None = None,
    *,
    include_round17: bool = True,
):
    corpus_root = tmp_path / "corpus"
    if include_round17:
        _copy_round17(corpus_root)
    else:
        corpus_root.mkdir(parents=True, exist_ok=True)
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
                },
                {
                    # Round 179: fresh account that has not lived the peer path.
                    "customer_name": "Synthetic Omega",
                    "SUBJECT_C": "Authentication SSO login errors Omega",
                    # Positive prediction fixture must match the Critical peers.
                    "SEVERITY_C": "Critical",
                    "AB_STATUS_C": "Open",
                    "ID": "AB-OMEGA",
                    "technology": "security",
                    "theme": "authentication",
                },
            ],
            "cases": [
                _peer_case("Peer A", suffix="A"),
                _peer_case("Peer B", suffix="B"),
                _peer_case("Peer C", suffix="C"),
                _peer_case(
                    "Synthetic Omega",
                    suffix="OMEGA",
                    case_number="OMEGA-1",
                    status="Open",
                ),
                # Round 179: operating-health corpus claims need one closed
                # window. A second identity stays Closed; the Open case keeps
                # Omega out of theme-wide closed_peer_count (open wins).
                _peer_case(
                    "Synthetic Omega",
                    suffix="OMEGA-CLOSED",
                    case_number="OMEGA-CLOSED",
                    status="Closed",
                ),
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


def test_operating_health_does_not_forecast_already_lived_path(
    configured_peer_corpus,
) -> None:
    facts = _build_report()
    insight = facts["decision_insights"]["support_operating_health"]
    text = insight["paragraph_text"]
    evidence = cr.get_peer_guidance_evidence(
        "authentication",
        "security",
        exclude_customer="Synthetic Alpha",
    )
    assert evidence.target_path == "already_closed"
    standalone = report_corpus_context.format_peer_guidance_clause(
        evidence, include_likely_next=True
    )
    published = report_corpus_context._r175_published_peer_clause(
        evidence, include_likely_next=True
    )
    assert standalone == ""
    assert "likely-next is closure" not in text
    assert "Test the peer-observed method" not in text
    assert "Test the peer-observed method" not in standalone
    assert "peer-observed resolution was" in published
    assert "likely-next" not in published
    assert "will " not in text.lower()
    assert "certainly" not in text.lower()
    assert "try the same method on the current open work" not in text


def test_operating_health_publishes_peer_likely_next(configured_peer_corpus) -> None:
    facts = _build_report(customer="Synthetic Omega")
    insight = facts["decision_insights"]["support_operating_health"]
    text = insight["paragraph_text"]
    assert "Observed-in-peers:" in text
    assert "likely-next is closure after that method (not a certainty)" in text
    assert "will " not in text.lower()
    assert "certainly" not in text.lower()
    standalone = report_corpus_context.format_peer_guidance_clause(
        cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Omega",
        ),
        include_likely_next=True,
    )
    assert "Next step: Test the peer-observed method" in standalone
    assert "try the same method on the current open work" not in text
    assert "try the same method on the current open work" not in standalone
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
        question="How is customer Synthetic Omega doing?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    assert "CORPUS_PEER_GUIDANCE:" in out.block
    assert "Observed-in-peers:" in out.block
    assert "likely-next is closure after that method (not a certainty)" in out.block
    assert out.stats.get("insufficient_peer_evidence") is False
    assert out.stats.get("likely_next") == "closure"
    assert out.stats.get("decision") == "peer_path_trial"
    assert out.stats.get("do_not_invent_a_future") is True
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
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status="Open"
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status="Open"
                ),
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
        include_round17=False,  # Round 179: isolate peer-math from Alpha's lived path
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
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status=""
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status=""
                ),
            ],
            "pulse": (
                _peer_pulse("Peer A", first="green", last="red", suffix="A")
                + _peer_pulse("Peer B", first="green", last="red", suffix="B")
            ),
        },
        include_round17=False,  # Round 179: isolate peer-math from Alpha's lived path
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
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status=""
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status=""
                ),
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
        include_round17=False,  # Round 179: isolate peer-math from Alpha's lived path
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
        assert "Keep the peer-observed method in place and verify pulse" in clause
        assert "keep the current method and watch pulse" not in clause
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
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status=""
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status=""
                ),
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
        include_round17=False,  # Round 179: isolate peer-math from Alpha's lived path
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
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status=""
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status=""
                ),
                _peer_barrier(
                    "Peer C", resolution=PEER_METHOD, suffix="C", ab_status=""
                ),
                _peer_barrier(
                    "Peer D", resolution=PEER_METHOD, suffix="D", ab_status=""
                ),
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
    # Round 179: Alpha already lived this path, so the account-scoped
    # formatter withholds likely-next. This pin is the peer-observed
    # median, not this-account advice.
    peer_math = replace(
        evidence,
        target_path="not_tried",
        next_step=cr._peer_next_step(
            evidence.likely_next,
            evidence.dominant_method_text,
            target_path="not_tried",
        ),
    )
    clause = report_corpus_context.format_peer_guidance_clause(
        peer_math, include_likely_next=True
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
        include_round17=False,  # Round 179: isolate peer-math from Alpha's lived path
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
        next_step=(
            "Test the peer-observed method on the current barrier, then verify "
            "closure before marking it resolved."
        ),
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
    assert retrievals[0]["next_step_published"] is True
    assert retrievals[0]["close_time_median_days"] == 9.0
    assert report_corpus_context._r175_median_close_fragment(None) == ""
    assert report_corpus_context._r175_median_close_fragment("n/a") == ""
    assert report_corpus_context._r175_median_close_fragment(-1) == ""
    assert report_corpus_context._r175_median_close_fragment(9.0) == " (median 9d)"
    assert report_corpus_context._r175_median_close_fragment(4.3) == " (median 4.3d)"

    clause_core = report_corpus_context.format_peer_guidance_clause(
        evidence,
        include_likely_next=True,
        include_median_close=False,
        include_next_step=False,
    )
    prefix_core = 520 - len(f"; {clause_core}.")
    core_base = ("y" * prefix_core) + "."
    combined_core, retrievals_core = report_corpus_context._r175_maybe_append_peer_clause(
        core_base,
        [],
        customer="Synthetic Alpha",
        theme="authentication",
        technology="security",
        include_likely_next=True,
        prefer_peer_resolution=False,
    )
    assert "likely-next is closure after that method (not a certainty)" in combined_core
    assert "Next step:" not in combined_core
    assert retrievals_core[0]["next_step_published"] is False
    assert len(combined_core) <= 520


def test_pulse_csv_snapshot_date_orders_trajectory_not_insertion(tmp_path: Path) -> None:
    """Later-written earlier snapshot must not invert recovered vs worsened."""
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
    assert "Peer guidance: Not enough evidence" in text


def test_historical_context_renders_ranked_peer_clause(
    configured_peer_corpus,
) -> None:
    ctx = report_corpus_context.build_historical_context(
        ["Synthetic Omega"], enabled=True
    )
    text = report_corpus_context.render_to_text(ctx)
    assert "Observed-in-peers:" in text
    assert "likely-next is closure after that method (not a certainty)" in text
    assert "Next step:" in text
    assert text.index("Next step:") < text.index("Observed-in-peers:")
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
        _predictive_team("Synthetic Omega"),
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
    """Round 177: thin evidence is honest and visible, not hidden.

    The R175 ``data-r175-peer-guidance`` marker stays off so sufficient-path
    tests still mean "actionable/method_only". The hyphenated token
    ``likely-next`` must not appear on the thin path.
    """
    from config import Config

    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", True, raising=False)
    resp = client.get("/customer/Synthetic%20Alpha")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "data-r177-peer-guidance" in body
    assert "data-r177-peer-insufficient" in body
    assert "data-r175-peer-guidance" not in body
    assert "likely-next" not in body
    assert "Not enough evidence" in body
    assert "Not live Cisco validation." in body
    # CSS may mention .r177-next-step; the actionable Next-step box must
    # not render on the thin path.
    assert 'class="r177-next-step"' not in body


def test_customer_360_renders_aggregate_peer_line(
    client, monkeypatch, configured_peer_corpus
) -> None:
    from config import Config

    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", True, raising=False)
    resp = client.get("/customer/Synthetic%20Omega")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "data-r175-peer-guidance" in body
    assert "Observed-in-peers" in body
    assert "Likely next from peer paths: closure after this method" in body
    assert "r177-next-step" in body
    card_start = body.index("data-r175-peer-guidance")
    # Round 177: do not slice a fixed 3500 chars — that swallows the
    # Cases timeline (TAC numbers) below the card. Stop at the next section.
    timeline = body.find("Cases timeline", card_start)
    card = body[card_start:timeline] if timeline != -1 else body[card_start : card_start + 1800]
    assert "Next step" in card
    assert card.index("Next step") < card.index("Likely next from peer paths")
    assert "will " not in card.casefold()
    assert "Not live Cisco validation." in card
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
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status=""
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status=""
                ),
                _peer_barrier(
                    "Peer C", resolution=PEER_METHOD, suffix="C", ab_status=""
                ),
                _peer_barrier(
                    "Peer D", resolution=PEER_METHOD, suffix="D", ab_status=""
                ),
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
        include_round17=False,  # Round 179: isolate peer-math from Alpha's lived path
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
        include_round17=False,  # Round 179: isolate peer-math from Alpha's lived path
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
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status=""
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status=""
                ),
                _peer_barrier(
                    "Peer C", resolution=PEER_METHOD, suffix="C", ab_status=""
                ),
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


# ---------------------------------------------------------------------------
# Round 178 — question-scoped theme hint outranks the globally-strongest
# unrelated barrier, so a question about one barrier never answers with a
# confident peer story about a different one. A hint that matches nothing
# this customer tracks is a no-op (fail-closed, never invents a match).
# ---------------------------------------------------------------------------


def _configured_delta_ranked_corpus(tmp_path: Path):
    """Same shape as configured_ranked_peer_corpus, standalone (no fixture)."""
    return _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier(
                    "Peer A", resolution=PEER_METHOD, suffix="A", ab_status=""
                ),
                _peer_barrier(
                    "Peer B", resolution=PEER_METHOD, suffix="B", ab_status=""
                ),
                _peer_barrier(
                    "Peer C", resolution=PEER_METHOD, suffix="C", ab_status=""
                ),
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


def test_question_theme_hint_switches_ranking_to_the_asked_about_barrier(
    tmp_path: Path,
) -> None:
    """Baseline: with no hint, the globally-strongest theme (configuration,
    closure, sufficient) wins over the customer's own thin authentication
    barrier -- same contract as
    test_ranked_guidance_prefers_clean_theme_over_tied_first_barrier."""
    connection = _configured_delta_ranked_corpus(tmp_path)
    try:
        history = cr.get_customer_history("Synthetic Delta")
        no_hint = report_corpus_context.select_ranked_peer_guidance(
            customer="Synthetic Delta",
            barriers=history.barriers,
            fallback_technology="security",
        )
        assert no_hint is not None
        assert no_hint.theme == "configuration"

        # A hint naming a theme this customer does not track at all changes
        # nothing -- ranking never invents a match out of thin air.
        no_match_hint = report_corpus_context.select_ranked_peer_guidance(
            customer="Synthetic Delta",
            barriers=history.barriers,
            fallback_technology="security",
            question_theme="billing",
        )
        assert no_match_hint is not None
        assert no_match_hint.theme == "configuration"

        # A hint matching a barrier this customer DOES track -- even one
        # whose own peer evidence is thin/mixed -- outranks the stronger
        # unrelated theme so the answer stays on-topic.
        matched_hint = report_corpus_context.select_ranked_peer_guidance(
            customer="Synthetic Delta",
            barriers=history.barriers,
            fallback_technology="security",
            question_theme="authentication",
        )
        assert matched_hint is not None
        assert matched_hint.theme == "authentication"
        assert matched_hint.likely_next == "insufficient"
    finally:
        cr.configure_connection(None)
        connection.close()


def test_ask_ai_on_topic_question_reports_insufficient_instead_of_unrelated_closure(
    tmp_path: Path,
) -> None:
    """Full Ask AI path: a question naming the customer's SSO/login barrier
    must not answer with the confident-but-unrelated policy-configuration
    closure story just because that story is the customer's globally
    strongest peer evidence."""
    connection = _configured_delta_ranked_corpus(tmp_path)
    try:
        off_topic = ask_ai_corpus.build_corpus_block(
            question="How is customer Synthetic Delta doing?",
            technology="security",
            enabled=True,
            top_k=3,
        )
        assert off_topic.stats.get("peer_theme") == "configuration"
        assert off_topic.stats.get("likely_next") == "closure"

        on_topic = ask_ai_corpus.build_corpus_block(
            question=(
                "How is customer Synthetic Delta doing with their sso "
                "login authentication errors?"
            ),
            technology="security",
            enabled=True,
            top_k=3,
        )
        # The on-topic barrier's own peer evidence is a closed/open tie
        # (mixed, per test_closed_open_tie_among_method_peers_fails_closed)
        # so Ask AI withholds the recommendation and receipt (Round 179).
        # The structured card retains the method observation. It must NOT
        # borrow the unrelated configuration
        # barrier's confident "closure" story just because that story is
        # stronger.
        assert on_topic.stats.get("peer_theme") == "authentication"
        assert on_topic.stats.get("likely_next") == "insufficient"
        assert "insufficient_peer_evidence=true" in on_topic.block
        assert not any(sid.startswith("CORPUS:PG-") for sid in on_topic.allowed_ids)
        assert on_topic.stats["peer_guidance"]["status"] == "method_only"
        assert on_topic.stats["peer_guidance"]["method"]
        assert "closed after" not in on_topic.block
        assert "likely-next is closure" not in on_topic.block
        for leaked in _forbidden_leakage():
            assert leaked not in on_topic.block
    finally:
        cr.configure_connection(None)
        connection.close()


# ---------------------------------------------------------------------------
# Round 175.4 — case-identity, tech-scope, aliases, Ask AI SourceID, PII
# ---------------------------------------------------------------------------

def _case_rec(*, number: str, is_open: bool, opened: str, closed: str) -> dict:
    return {
        "case_number": number,
        "is_open": is_open,
        "opened_at": opened,
        "closed_at": closed,
        "summary": "Authentication failure during onboarding",
    }


@pytest.mark.parametrize("order", ["open_first", "closed_first"])
def test_mixed_snapshot_same_case_identity_has_no_outcome(order: str) -> None:
    opened = "2026-01-01T00:00:00Z"
    closed = "2026-01-10T00:00:00Z"
    open_rec = _case_rec(number="CONF-1", is_open=True, opened=opened, closed="")
    closed_rec = _case_rec(
        number="CONF-1", is_open=False, opened=opened, closed=closed
    )
    recs = (
        [open_rec, closed_rec]
        if order == "open_first"
        else [closed_rec, open_rec]
    )
    state, duration, _pivot = cr._peer_case_outcome(recs)
    assert state == "none"
    assert duration is None


@pytest.mark.parametrize("order", ["open_first", "closed_first"])
def test_conflicting_case_snapshots_emit_no_likely_next(tmp_path: Path, order: str) -> None:
    open_a = _peer_case(
        "Conflict A", suffix="CA", status="Open", case_number="CONF-1"
    )
    closed_a = _peer_case(
        "Conflict A", suffix="CA", status="Closed", case_number="CONF-1"
    )
    open_b = _peer_case(
        "Conflict B", suffix="CB", status="Open", case_number="CONF-2"
    )
    closed_b = _peer_case(
        "Conflict B", suffix="CB", status="Closed", case_number="CONF-2"
    )
    a_cases = [open_a, closed_a] if order == "open_first" else [closed_a, open_a]
    b_cases = [open_b, closed_b] if order == "open_first" else [closed_b, open_b]
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier(
                    "Conflict A",
                    resolution=PEER_METHOD,
                    suffix="CA",
                    ab_status="",
                ),
                _peer_barrier(
                    "Conflict B",
                    resolution=PEER_METHOD,
                    suffix="CB",
                    ab_status="",
                ),
            ],
            "cases": a_cases + b_cases,
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
        assert evidence.likely_next == "insufficient"
        assert evidence.method_open_peer_count == 0
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        published = report_corpus_context._r175_published_peer_clause(
            evidence, include_likely_next=True
        )
        assert "likely-next" not in clause
        assert "likely-next" not in published
        assert "remain open" not in published.casefold()
        assert "Observed-in-peers" not in clause
    finally:
        cr.configure_connection(None)
        connection.close()


def _index_multitech(tmp_path: Path, mt_order: tuple[str, ...]):
    corpus_root = tmp_path / "corpus"
    _copy_round17(corpus_root)
    _write_csv(
        corpus_root / "mt_a_security.csv",
        [
            {
                "customer_name": "Synthetic MultiTech",
                "Case Number": "MT-SEC",
                "Title": "Inventory sync delay after import",
                "Severity": "P3",
                "Case Status": "Closed",
                "Date/Time Opened": "2026-01-01T00:00:00Z",
                "Date/Time Closed": "2026-01-02T00:00:00Z",
                "Resolution Summary": "Scaled workers.",
                "technology": "security",
            }
        ],
    )
    _write_csv(
        corpus_root / "mt_b_collab.csv",
        [
            {
                "customer_name": "Synthetic MultiTech",
                "Case Number": "MT-COL",
                "Title": "Meeting join delay on mobile",
                "Severity": "P3",
                "Case Status": "Closed",
                "Date/Time Opened": "2026-01-03T00:00:00Z",
                "Date/Time Closed": "2026-01-04T00:00:00Z",
                "Resolution Summary": "Client cache refresh.",
                "technology": "collaboration",
            }
        ],
    )
    _write_csv(
        corpus_root / "mt_c_blank_barrier.csv",
        [
            {
                "customer_name": "Synthetic MultiTech",
                "SUBJECT_C": "Authentication SSO login errors blank tech",
                "SEVERITY_C": "Critical",
                "AB_STATUS_C": "Open",
                "ID": "AB-MT-BLANK",
                "theme": "authentication",
                "resolution": PEER_METHOD,
            }
        ],
    )
    _write_csv(
        corpus_root / "peer_barriers.csv",
        [
            _peer_barrier("Peer A", resolution=PEER_METHOD, suffix="A"),
            _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
        ],
    )
    _write_csv(
        corpus_root / "peer_cases.csv",
        [
            _peer_case("Peer A", suffix="A"),
            _peer_case("Peer B", suffix="B"),
        ],
    )
    files = enumerate_corpus_files(corpus_root)
    by_name = {cf.filename: cf for cf in files}
    rest = [
        cf
        for cf in sorted(files, key=lambda item: item.filename)
        if cf.filename not in mt_order
    ]
    ordered = rest + [by_name[name] for name in mt_order]
    connection = open_corpus_db(tmp_path / "corpus.db")
    index_folder(connection, corpus_root, files=ordered)
    cr.configure_connection(connection)
    return connection


@pytest.mark.parametrize(
    "mt_order",
    [
        ("mt_a_security.csv", "mt_b_collab.csv", "mt_c_blank_barrier.csv"),
        ("mt_b_collab.csv", "mt_a_security.csv", "mt_c_blank_barrier.csv"),
    ],
)
def test_blank_barrier_tech_does_not_use_history_technology(
    tmp_path: Path, mt_order: tuple[str, ...]
) -> None:
    connection = _index_multitech(tmp_path, mt_order)
    try:
        history = cr.get_customer_history("Synthetic MultiTech")
        blank = [b for b in history.barriers if not str(b.technology or "").strip()]
        assert blank, "blank-tech authentication barrier must be indexed"
        ranked = report_corpus_context.load_ranked_peer_guidance(
            "Synthetic MultiTech"
        )
        clause = report_corpus_context.format_ranked_peer_guidance_clause(
            "Synthetic MultiTech"
        )
        assert clause == ""
        if ranked is not None:
            published = report_corpus_context._r175_published_peer_clause(
                ranked, include_likely_next=True
            )
            assert published == ""
            assert ranked.likely_next == "insufficient" or not ranked.evidence_sufficient
    finally:
        cr.configure_connection(None)
        connection.close()


def test_blank_barrier_tech_absence_is_identical_across_file_order(tmp_path: Path) -> None:
    orders = [
        ("mt_a_security.csv", "mt_b_collab.csv", "mt_c_blank_barrier.csv"),
        ("mt_b_collab.csv", "mt_a_security.csv", "mt_c_blank_barrier.csv"),
    ]
    techs = []
    clauses = []
    for mt_order in orders:
        nested = tmp_path / "".join(mt_order)[:24]
        nested.mkdir()
        connection = _index_multitech(nested, mt_order)
        try:
            history = cr.get_customer_history("Synthetic MultiTech")
            techs.append(str(history.technology or ""))
            clauses.append(
                report_corpus_context.format_ranked_peer_guidance_clause(
                    "Synthetic MultiTech"
                )
            )
        finally:
            cr.configure_connection(None)
            connection.close()
    assert techs[0] != techs[1]
    assert clauses[0] == clauses[1] == ""


@pytest.mark.parametrize("swap", [False, True])
def test_alias_group_contributes_zero_peer_count(tmp_path: Path, swap: bool) -> None:
    alias_a = _peer_barrier(
        "NYU LANGONE HEALTH", resolution=PEER_METHOD, suffix="NYU1"
    )
    alias_b = _peer_barrier("NYULH", resolution=PEER_METHOD, suffix="NYU2")
    barriers = [alias_b, alias_a] if swap else [alias_a, alias_b]
    cases = [
        _peer_case("NYU LANGONE HEALTH", suffix="NYU1"),
        _peer_case("NYULH", suffix="NYU2"),
    ]
    if swap:
        cases = list(reversed(cases))
    connection = _index_corpus(
        tmp_path,
        {"barriers": barriers, "cases": cases},
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="NYU MEDICAL CENTER",
        )
        assert evidence.peer_customer_count == 1
        assert evidence.evidence_sufficient is False
        assert evidence.likely_next == "insufficient"
        assert evidence.exclusion_count >= 2
        assert len(evidence.exclusion_digest) == 64
        assert report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        ) == ""
        payload = " ".join(str(v) for v in asdict(evidence).values())
        for leaked in ("NYU LANGONE", "NYULH", "MEDICAL CENTER"):
            assert leaked not in payload
    finally:
        cr.configure_connection(None)
        connection.close()


def test_legal_suffix_sibling_is_not_a_peer(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A Inc", resolution=PEER_METHOD, suffix="INC"),
                _peer_barrier("Peer B", resolution=PEER_METHOD, suffix="B"),
            ],
            "cases": [
                _peer_case("Peer A Inc", suffix="INC"),
                _peer_case("Peer B", suffix="B"),
            ],
        },
    )
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Peer A",
        )
        # Round17 Synthetic Alpha + Peer B. Peer A Inc is the same identity.
        assert evidence.peer_customer_count == 2
        payload = " ".join(str(v) for v in asdict(evidence).values())
        assert "Peer A Inc" not in payload
        assert "jane@" not in payload
    finally:
        cr.configure_connection(None)
        connection.close()


def test_unproved_alias_registry_fails_closed(monkeypatch, configured_peer_corpus) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("alias registry unavailable")

    monkeypatch.setattr(
        "data_normalization.load_customer_alias_registry",
        _boom,
    )
    evidence = cr.get_peer_guidance_evidence(
        "authentication",
        "security",
        exclude_customer="Synthetic Alpha",
    )
    assert evidence.peer_customer_count == 0
    assert evidence.evidence_sufficient is False
    assert evidence.likely_next == "insufficient"
    assert report_corpus_context.format_peer_guidance_clause(
        evidence, include_likely_next=True
    ) == ""


def test_ask_ai_peer_source_id_whitelisted_when_sufficient(configured_peer_corpus) -> None:
    out = ask_ai_corpus.build_corpus_block(
        question="How is customer Synthetic Omega doing?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    peer_ids = [sid for sid in out.allowed_ids if str(sid).startswith("CORPUS:PG-")]
    assert len(peer_ids) == 1
    assert f"[SourceID: {peer_ids[0]}]" in out.block
    assert "Observed-in-peers:" in out.block
    allowed = {_normalize_claim_id(x) for x in out.allowed_ids}
    valid, _unknowns, rejected = _validate_claim_citations(
        [{"statement": "likely-next is closure after that method.", "citations": peer_ids}],
        allowed,
    )
    assert rejected == 0
    assert valid
    _valid2, _unknowns2, rejected2 = _validate_claim_citations(
        [
            {
                "statement": "tampered citation must fail closed.",
                "citations": ["CORPUS:PG-DEADBEEFDEADBEEF"],
            }
        ],
        allowed,
    )
    assert rejected2 == 1


def test_ask_ai_thin_path_adds_no_peer_source_id(configured_round17_corpus) -> None:
    out = ask_ai_corpus.build_corpus_block(
        question="How is customer Synthetic Alpha doing?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    assert "insufficient_peer_evidence=true" in out.block
    assert not any(str(sid).startswith("CORPUS:PG-") for sid in out.allowed_ids)
    assert "[SourceID: CORPUS:PG-" not in out.block


def test_pii_method_fails_closed_on_every_surface(tmp_path: Path) -> None:
    connection = _index_corpus(
        tmp_path,
        {
            "barriers": [
                _peer_barrier("Peer A", resolution=PII_METHOD, suffix="A"),
                _peer_barrier("Peer B", resolution=PII_METHOD, suffix="B"),
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
        assert evidence.dominant_method_text == ""
        assert evidence.evidence_sufficient is False
        assert evidence.likely_next == "insufficient"
        clause = report_corpus_context.format_peer_guidance_clause(
            evidence, include_likely_next=True
        )
        ask = report_corpus_context.format_peer_guidance_ask_ai_line(evidence)
        hist = report_corpus_context.build_historical_context(
            ["Synthetic Alpha"], enabled=True
        )
        ranked_clause = report_corpus_context.format_ranked_peer_guidance_clause(
            "Synthetic Alpha"
        )
        out = ask_ai_corpus.build_corpus_block(
            question="How is customer Synthetic Alpha doing?",
            technology="security",
            enabled=True,
            top_k=3,
        )
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
        outlook = facts["decision_insights"]["predictive_outlook"]["paragraph_text"]
        health = facts["decision_insights"]["support_operating_health"]["paragraph_text"]
        published = "\n".join(
            [
                clause,
                ask,
                ranked_clause,
                hist.entries[0].peer_guidance_clause if hist.entries else "",
                out.block,
                outlook,
                health,
            ]
        )
        for leaked in (
            "Peer A",
            "jane@cisco.com",
            "TAC9001",
            "notes.csv",
        ):
            assert leaked not in published
        assert "likely-next" not in clause
        assert "insufficient_peer_evidence=true" in ask
        assert not any(str(sid).startswith("CORPUS:PG-") for sid in out.allowed_ids)
    finally:
        cr.configure_connection(None)
        connection.close()


def test_title_case_method_is_not_treated_as_pii() -> None:
    """Salesforce Title-Case methods must still publish. Person names must not."""
    assert cr._peer_text_leaks_pii(PEER_METHOD) is False
    assert cr._peer_text_leaks_pii(
        "Rotated Service Token And Updated Documentation"
    ) is False
    assert cr._peer_text_leaks_pii(PII_METHOD) is True
    assert cr._peer_text_leaks_pii("Jane Smith reset the token") is True
    assert cr._peer_text_leaks_pii("Assigned to Jane Smith") is True
    step = cr._peer_next_step("closure", PEER_METHOD)
    assert "peer-observed method" in step
    assert "try the same method" not in step
    assert cr._peer_next_step("closure", PII_METHOD) == ""


def test_path_like_method_text_is_treated_as_unsafe() -> None:
    # Round 184.2: hard fence for private offline lane — no filesystem path
    # strings (including CSOne roots) may reach published peer-guidance text.
    assert (
        cr._peer_text_leaks_pii(
            "/Users/alex/Library/CloudStorage/OneDrive-Cisco/"
            "Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports"
        )
        is True
    )
    assert (
        cr._peer_text_leaks_pii(
            r"C:\Users\alex\AppData\Roaming\AdoptIQ\uploads\manual-review.template.json"
        )
        is True
    )
    assert (
        cr._peer_text_leaks_pii(
            "~/Library/Application Support/AdoptIQ/knowledge/corpus.db.enc"
        )
        is True
    )
    assert cr._peer_text_leaks_pii("Open/Closed workflow remains active.") is False


def test_unc_path_method_text_is_treated_as_unsafe() -> None:
    # Round 185: UNC paths (Windows network shares \\server\share) must be blocked.
    # These can leak internal network infrastructure and server names.
    # Round 185.1: bare UNC share roots and hidden shares (c$) are unsafe too.
    assert cr._peer_text_leaks_pii(r"\\server\share") is True
    assert cr._peer_text_leaks_pii(r"\\server\share\folder") is True
    assert cr._peer_text_leaks_pii(r"\\nas01\customer-data\reports") is True
    assert cr._peer_text_leaks_pii(r"\\fileserver\c$\customer-data") is True
    assert cr._peer_text_leaks_pii(r"\\fileserver.corp.cisco.com\shared\documents") is True
    assert cr._peer_text_leaks_pii(r"Uploaded to \\backup-srv\archive\2025\cases") is True
    assert cr._peer_text_leaks_pii(r"File stored at \\192.168.1.100\data\export.csv") is True
    # Forward slashes in UNC paths (rare but valid on some systems)
    assert cr._peer_text_leaks_pii(r"\\server/share/folder/file.txt") is True
    # Edge cases: not UNC paths, should not match
    assert cr._peer_text_leaks_pii(r"Used backslash \\ in config") is False
    assert cr._peer_text_leaks_pii(r"Escaped sequence: \\n means newline") is False


def test_round175_4_source_shape_no_history_technology_fallback() -> None:
    ask_ai = Path(__file__).resolve().parents[1] / "ask_ai_corpus.py"
    context = Path(__file__).resolve().parents[1] / "report_corpus_context.py"
    app = Path(__file__).resolve().parents[1] / "app_simple.py"
    retriever = Path(__file__).resolve().parents[1] / "corpus_retriever.py"
    ask_body = ask_ai.read_text(encoding="utf-8")
    ctx_body = context.read_text(encoding="utf-8")
    app_body = app.read_text(encoding="utf-8")
    ret_body = retriever.read_text(encoding="utf-8")
    assert "history.technology or technology" not in ask_body
    assert 'fallback_technology or getattr(history, "technology"' not in ctx_body
    assert "Round 175.4" in ask_body
    assert "Round 175.4" in ctx_body
    assert "Round 175.4" in app_body
    assert "Round 175.4" in ret_body
    assert "_peer_identity_exclusion" in ret_body
    assert "peer_guidance_source_id" in ctx_body
    assert "_r175_peer_text_leaks_pii" in ctx_body
    assert 'return f"CORPUS:PG-{digest}"' in ctx_body
    assert 'return f"CORPUS:PEER-{digest}"' not in ctx_body
    assert "_PEER_METHOD_LEADING_VERBS" in ret_body
    assert "try the same method on the current open work" not in ret_body
    assert "include_next_step" in ctx_body
