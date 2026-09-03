"""Round 179: this-account lived paths are not a fabricated future.

Existing Ask AI / Customer 360 / Historical Context surfaces must use
peer-path decisions AND this account's own path. A completed path hard-stops.
An already-open path says do not repeat. Thin evidence stays thin.
Fixtures only. ready_for_live_cisco stays false.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import ask_ai_corpus
import corpus_retriever as cr
import report_corpus_context as rcc
from test_round175_peer_guidance_knowledge import (
    PEER_METHOD,
    _index_corpus,
    _peer_barrier,
    _peer_case,
)
from test_round177_peer_guidance_surfaces import VIEW_KEYS, _assert_view_shape

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_corpus_connection():
    cr.configure_connection(None)
    yield
    cr.configure_connection(None)


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
                    "customer_name": "Synthetic Omega",
                    "SUBJECT_C": "Authentication SSO login errors Omega",
                    "SEVERITY_C": "High",
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


def _lived_path(
    tmp_path: Path,
    *,
    account: str,
    account_status: str,
    peer_statuses: tuple[str, ...] = ("Closed", "Closed"),
    account_resolution: str = PEER_METHOD,
) -> cr.PeerGuidanceEvidence:
    rows = [
        _peer_barrier(
            f"Peer {index}",
            resolution=PEER_METHOD,
            suffix=str(index),
            ab_status=status,
        )
        for index, status in enumerate(peer_statuses, start=1)
    ]
    rows.append(
        _peer_barrier(
            account,
            resolution=account_resolution,
            suffix="SELF",
            ab_status=account_status,
        )
    )
    connection = _index_corpus(
        tmp_path, {"barriers": rows}, include_round17=False
    )
    try:
        return cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer=account,
        )
    finally:
        cr.configure_connection(None)
        connection.close()


def test_already_closed_withholds_trial_and_future(tmp_path: Path) -> None:
    evidence = _lived_path(tmp_path, account="Lived Closed Co", account_status="Closed")
    view = rcc.build_peer_guidance_view(evidence)
    public = rcc.public_peer_guidance_view(view)
    scan = rcc.format_peer_guidance_scan_lines(evidence)
    ask = rcc.format_peer_guidance_ask_ai_line(evidence)
    clause = rcc.format_peer_guidance_clause(evidence, include_likely_next=True)
    published = rcc._r175_published_peer_clause(evidence, include_likely_next=True)

    assert evidence.target_path == "already_closed"
    assert evidence.likely_next == "closure"
    assert evidence.next_step == ""
    assert rcc.peer_path_decision(evidence) == "already_lived"
    assert clause == ""
    assert "likely-next" not in published
    assert "peer-observed resolution was" in published
    _assert_view_shape(view)
    _assert_view_shape(public)
    assert view["status"] == "method_only"
    assert view["insufficient_reason"] == "already_lived"
    assert view["next_step"] == ""
    assert view["likely_next"] == ""
    assert "already completed" in str(view["insufficient_copy"]).casefold()
    assert public["ready_for_live_cisco"] is False
    assert scan[0].startswith("Next step: This account already completed")
    assert all("likely-next is closure" not in line for line in scan)
    assert "decision=already_lived" in ask
    assert "do_not_invent_a_future=true" in ask
    assert "next_step=withheld" in ask
    assert "CORPUS:PG-" not in ask
    assert "Test the peer-observed method" not in ask


def test_already_open_says_do_not_repeat_and_does_not_forecast_closure(
    tmp_path: Path,
) -> None:
    evidence = _lived_path(tmp_path, account="Lived Open Co", account_status="Open")
    view = rcc.build_peer_guidance_view(evidence)
    ask = rcc.format_peer_guidance_ask_ai_line(evidence)
    clause = rcc.format_peer_guidance_clause(evidence, include_likely_next=True)

    assert evidence.target_path == "already_open"
    assert evidence.likely_next == "closure"
    assert rcc.peer_path_decision(evidence) == "do_not_repeat"
    assert "Do not repeat it unchanged" in evidence.next_step
    assert "Test the peer-observed method" not in evidence.next_step
    assert view["status"] == "actionable"
    assert view["likely_next"] == "remains_open"
    assert "likely-next is closure" not in clause
    assert "already used that method and remains open" in clause
    assert "decision=do_not_repeat" in ask
    assert "do_not_invent_a_future=true" in ask
    assert "likely_next=remains_open" in ask
    assert view["ready_for_live_cisco"] is False


def test_not_tried_fresh_account_keeps_round178_trial(tmp_path: Path) -> None:
    evidence = _lived_path(
        tmp_path,
        account="Synthetic Omega",
        account_status="Open",
        account_resolution="Tried a different local workaround.",
    )
    view = rcc.build_peer_guidance_view(evidence)

    assert evidence.target_path == "not_tried"
    assert evidence.likely_next == "closure"
    assert rcc.peer_path_decision(evidence) == "peer_path_trial"
    assert view["status"] == "actionable"
    assert view["next_step"].startswith("Test the peer-observed method")
    assert view["likely_next"] == "closure"


def test_receipt_changes_when_target_path_changes(tmp_path: Path) -> None:
    closed = _lived_path(tmp_path / "closed", account="Path Co", account_status="Closed")
    base = rcc.peer_guidance_source_id(closed)
    assert base.startswith("CORPUS:PG-")
    assert rcc.peer_guidance_source_id(replace(closed, target_path="not_tried")) != base
    assert rcc.peer_guidance_source_id(replace(closed, target_path="already_open")) != base


def test_ranking_prefers_open_work_over_already_closed_theme(
    configured_peer_corpus,
) -> None:
    history = cr.get_customer_history("Synthetic Alpha")
    themes = {str(barrier.theme) for barrier in history.barriers}
    assert "authentication" in themes
    assert "performance" in themes
    auth = cr.get_peer_guidance_evidence(
        "authentication",
        "security",
        exclude_customer="Synthetic Alpha",
    )
    assert auth.target_path == "already_closed"
    ranked = rcc.select_ranked_peer_guidance(
        customer="Synthetic Alpha",
        barriers=history.barriers,
    )
    assert ranked is not None
    assert ranked.theme != "authentication" or ranked.target_path != "already_closed"
    assert ranked.theme == "performance"
    view = rcc.build_peer_guidance_view(ranked)
    assert view["status"] == "insufficient"
    ask = rcc.format_peer_guidance_ask_ai_line(ranked)
    assert "insufficient_peer_evidence=true" in ask
    assert "CORPUS:PG-" not in ask


def test_ask_ai_alpha_already_lived_does_not_invent_a_future(
    configured_peer_corpus,
) -> None:
    out = ask_ai_corpus.build_corpus_block(
        question="How is customer Synthetic Alpha doing?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    assert "CORPUS_PEER_GUIDANCE:" in out.block
    assert "insufficient_peer_evidence=true" in out.block
    assert "do_not_invent_a_future=true" in out.block
    assert "Test the peer-observed method" not in out.block
    assert "likely-next is closure after that method" not in out.block
    assert not any(str(sid).startswith("CORPUS:PG-") for sid in out.allowed_ids)
    assert out.stats.get("do_not_invent_a_future") is True
    assert out.stats.get("insufficient_peer_evidence") is True
    assert out.stats.get("likely_next") == "insufficient"
    view = (out.stats.get("peer_guidance") or {})
    assert view.get("ready_for_live_cisco") is False


def test_customer_360_alpha_shows_already_lived_or_thin_honesty(
    client, monkeypatch, configured_peer_corpus
) -> None:
    from config import Config

    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", True, raising=False)
    resp = client.get("/customer/Synthetic%20Alpha")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Observed-in-peers" in body
    assert "Test the peer-observed method" not in body
    assert "Likely next from peer paths: closure after this method" not in body
    assert "Not live Cisco validation." in body
    assert "data-r177-peer-guidance" in body
    assert (
        "data-r179-peer-already-lived" in body
        or "data-r177-peer-insufficient" in body
    )


def test_existing_cards_name_already_lived_without_innerhtml() -> None:
    customer = (ROOT / "templates" / "customer_360.html").read_text(encoding="utf-8")
    ask_ai = (ROOT / "static" / "js" / "r177_peer_guidance_card.js").read_text(
        encoding="utf-8"
    )
    for body in (customer, ask_ai):
        assert "Peer path already completed" in body
        assert "data-r179-peer-already-lived" in body
    assert "textContent" in ask_ai
    assert "innerHTML" not in ask_ai
    assert "ready_for_live_cisco === true" in ask_ai
    assert set(VIEW_KEYS) == set(rcc._R177_VIEW_KEYS)
    assert "already_lived" in rcc._R177_REASONS
