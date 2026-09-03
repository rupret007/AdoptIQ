"""Round 178: outcome-aware peer decisions on existing product surfaces.

All evidence is generated from synthetic offline corpus fixtures. Positive and
negative paths produce different next-step decisions; thin and mixed paths
publish an explicit hard-stop rather than a fabricated future.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import corpus_retriever as cr
import report_corpus_context as rcc
from test_round175_peer_guidance_knowledge import (
    PEER_METHOD,
    _index_corpus,
    _peer_barrier,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_corpus_connection():
    cr.configure_connection(None)
    yield
    cr.configure_connection(None)


def _peer_path(tmp_path: Path, statuses: tuple[str, ...]) -> cr.PeerGuidanceEvidence:
    rows = [
        _peer_barrier(
            f"Peer {index}",
            resolution=PEER_METHOD,
            suffix=str(index),
            ab_status=status,
        )
        for index, status in enumerate(statuses, start=1)
    ]
    connection = _index_corpus(tmp_path, {"barriers": rows})
    try:
        return cr.get_peer_guidance_evidence(
            "authentication",
            "security",
            exclude_customer="Synthetic Alpha",
        )
    finally:
        cr.configure_connection(None)
        connection.close()


def test_closed_peer_path_recommends_method_trial_then_verification(tmp_path: Path) -> None:
    evidence = _peer_path(tmp_path, ("Closed", "Closed"))
    view = rcc.build_peer_guidance_view(evidence)

    assert evidence.likely_next == "closure"
    assert evidence.likely_next_basis == "barrier"
    assert view["status"] == "actionable"
    assert view["next_step"] == (
        "Test the peer-observed method on the current barrier, then verify "
        "closure before marking it resolved."
    )
    assert view["likely_next"] == "closure"
    assert "Likely next from peer paths: closure" in str(view["likely_next_label"])
    assert "2 of 2 method peers with a known outcome closed" in str(view["evidence_line"])
    assert view["source_id"].startswith("CORPUS:PG-")
    assert view["ready_for_live_cisco"] is False


def test_open_peer_path_warns_not_to_repeat_method_as_resolution(tmp_path: Path) -> None:
    evidence = _peer_path(tmp_path, ("Open", "Open"))
    view = rcc.build_peer_guidance_view(evidence)

    assert evidence.likely_next == "remains_open"
    assert view["status"] == "actionable"
    assert view["next_step"] == (
        "Do not treat the peer-observed method as resolution; keep the barrier "
        "open and choose another intervention."
    )
    assert "Likely next from peer paths: the barrier remains open" in str(
        view["likely_next_label"]
    )
    assert "2 of 2 method peers with a known outcome remain open" in str(
        view["evidence_line"]
    )
    assert "Test the peer-observed method" not in str(view["next_step"])


def test_one_peer_hard_stops_every_guidance_projection(tmp_path: Path) -> None:
    evidence = _peer_path(tmp_path, ("Closed",))
    view = rcc.build_peer_guidance_view(evidence)
    scan = rcc.format_peer_guidance_scan_lines(evidence)
    ask_ai_line = rcc.format_peer_guidance_ask_ai_line(evidence)

    assert evidence.evidence_sufficient is False
    assert view["status"] == "insufficient"
    assert str(view["insufficient_copy"]).startswith("Not enough evidence")
    assert view["next_step"] == ""
    assert view["likely_next"] == ""
    assert view["source_id"] == ""
    assert scan[0].startswith("Peer guidance: Not enough evidence")
    assert "Not live Cisco validation" in scan[-1]
    assert "insufficient_peer_evidence=true" in ask_ai_line
    assert "not_enough_evidence=true" in ask_ai_line
    assert "next_step=withheld" in ask_ai_line
    assert "no next step is recommended" in ask_ai_line
    assert "CORPUS:PG-" not in ask_ai_line


def test_mixed_peer_outcomes_keep_method_but_hard_stop_future(tmp_path: Path) -> None:
    evidence = _peer_path(tmp_path, ("Closed", "Open"))
    view = rcc.build_peer_guidance_view(evidence)
    scan = rcc.format_peer_guidance_scan_lines(evidence)

    assert evidence.evidence_sufficient is True
    assert evidence.likely_next == "insufficient"
    assert evidence.next_step == ""
    assert view["status"] == "method_only"
    assert view["method"] == PEER_METHOD
    assert view["next_step"] == ""
    assert view["likely_next"] == ""
    assert str(view["insufficient_copy"]).startswith(
        "Not enough outcome evidence"
    )
    assert scan[0].startswith("Next step: Not enough outcome evidence")
    assert all("likely-next" not in line for line in scan)


def test_receipt_changes_when_the_underlying_peer_path_changes(tmp_path: Path) -> None:
    closed = _peer_path(tmp_path / "closed", ("Closed", "Closed"))
    changed_count = replace(closed, method_barrier_closed_peer_count=3)
    changed_basis = replace(
        closed,
        likely_next_basis="case",
        method_barrier_closed_peer_count=0,
        method_closed_peer_count=2,
    )

    base_receipt = rcc.peer_guidance_source_id(closed)
    assert base_receipt.startswith("CORPUS:PG-")
    assert rcc.peer_guidance_source_id(changed_count) != base_receipt
    assert rcc.peer_guidance_source_id(changed_basis) != base_receipt


def test_existing_cards_explain_positive_negative_and_thin_paths() -> None:
    customer = (ROOT / "templates" / "customer_360.html").read_text(encoding="utf-8")
    ask_ai = (ROOT / "static" / "js" / "r177_peer_guidance_card.js").read_text(
        encoding="utf-8"
    )

    for body in (customer, ask_ai):
        assert "Peer-backed next step" in body
        assert "Caution: peer path stalled" in body
        assert "Peer method not to repeat unchanged" in body
        assert "Not enough outcome evidence" in body
    assert "textContent" in ask_ai
    assert "innerHTML" not in ask_ai
