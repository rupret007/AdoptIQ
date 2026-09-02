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


def _peer_case(customer: str, *, suffix: str, status: str = "Closed") -> dict[str, str]:
    closed = "2026-01-10T00:00:00Z" if status == "Closed" else ""
    return {
        "customer_name": customer,
        "Case Number": f"PEER-{suffix}",
        "Title": f"Authentication failure during {suffix} onboarding",
        "Severity": "P2",
        "Case Status": status,
        "Date/Time Opened": "2026-01-01T00:00:00Z",
        "Date/Time Closed": closed,
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
        "PEER-A",
        "PEER-B",
        "PEER-C",
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
