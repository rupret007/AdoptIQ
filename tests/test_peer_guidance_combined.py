"""Cross-draft contracts: lived paths, question scope, and both severity gates.

Only synthetic corpus records; never credentials or external providers.
"""
from types import SimpleNamespace

import pytest

import ask_ai_corpus
import corpus_retriever as cr
import report_corpus_context as rcc
from test_round179_lived_peer_paths import _lived_path
from test_round181_comparable_case_severity import _index_corpus, _peer_barrier, _peer_case
from test_round182_question_path_peer_guidance import _barriers, _evidence, _load_for_theme


def test_case_fallback_uses_barrier_comparable_intersection(tmp_path):
    barriers = [_peer_barrier(f"Peer {n}", suffix=str(n)) for n in range(3)]
    barriers[2]["SEVERITY_C"] = ""
    cases = [_peer_case(f"Peer {n}", suffix=str(n), severity="P2") for n in range(3)]
    cases[2]["Severity"] = "P4"
    connection = _index_corpus(tmp_path, {"barriers": barriers, "cases": cases}, copy_bundled_cases=False)
    try:
        evidence = cr.get_peer_guidance_evidence(
            "authentication", "security", exclude_customer="Fresh Fixture",
            target_severity="Critical", target_case_severity="P2",
        )
        # The unknown-barrier peer cannot poison the valid two-peer TAC cohort.
        assert evidence.comparable_method_peer_count == 2
        assert evidence.comparable_case_method_peer_count == 2
        assert evidence.method_closed_peer_count == 2
        assert evidence.likely_next == "closure"
        assert evidence.likely_next_basis == "case"
        assert rcc.build_peer_guidance_view(evidence)["ready_for_live_cisco"] is False
    finally:
        cr.configure_connection(None)
        connection.close()


@pytest.mark.parametrize("dimension", ["barrier", "case"])
def test_mixed_severity_within_every_peer_never_becomes_unknown_fallback(tmp_path, dimension):
    barriers, cases = [], []
    for n in range(2):
        barriers.append(_peer_barrier(f"Peer {n}", suffix=str(n), ab_status="Closed" if dimension == "barrier" else ""))
        if dimension == "barrier":
            second = _peer_barrier(f"Peer {n}", suffix=f"{n}B", ab_status="Closed")
            second["SEVERITY_C"] = "Low"
            barriers.append(second)
        else:
            cases.extend([
                _peer_case(f"Peer {n}", suffix=f"{n}A", severity="P1"),
                _peer_case(f"Peer {n}", suffix=f"{n}B", severity="P4"),
            ])
    rows = {"barriers": barriers}
    if cases:
        rows["cases"] = cases
    connection = _index_corpus(tmp_path, rows, copy_bundled_cases=False)
    try:
        evidence = cr.get_peer_guidance_evidence("authentication", "security", exclude_customer="Fresh Fixture")
        assert evidence.evidence_sufficient is True  # Method observation survives.
        assert evidence.likely_next == "insufficient"
        view = rcc.public_peer_guidance_view(rcc.build_peer_guidance_view(evidence))
        expected = "incomparable_severity" if dimension == "barrier" else "incomparable_case_severity"
        assert view["insufficient_reason"] == expected
        assert view["next_step"] == view["likely_next"] == ""
        assert view["ready_for_live_cisco"] is False
        assert "CORPUS:PG-" not in rcc.format_peer_guidance_ask_ai_line(evidence)
    finally:
        cr.configure_connection(None)
        connection.close()


def test_strict_question_path_cannot_be_overridden_by_soft_hint(monkeypatch):
    monkeypatch.setattr(rcc, "_r175_load_peer_evidence", lambda customer, theme, tech, **kw: _load_for_theme(theme, tech))
    assert rcc.select_ranked_peer_guidance(
        customer="Fresh Fixture", barriers=_barriers(), fallback_technology="",
        prefer_theme="billing", question_theme="authentication",
    ) is None


@pytest.mark.parametrize("state,expected", [("Open", "remains_open"), ("Closed", "insufficient")])
def test_ask_ai_lived_path_summary_matches_card_and_publication(tmp_path, monkeypatch, state, expected):
    evidence = _lived_path(tmp_path, account="Synthetic Alpha", account_status=state)
    assert evidence.comparable_severity_band == "critical"
    history = SimpleNamespace(name="Synthetic Alpha", manager=None, technology="security", first_seen="",
                              last_seen="", cases=(), barriers=_barriers())
    monkeypatch.setattr(cr, "is_configured", lambda: True)
    monkeypatch.setattr(cr, "get_customer_history", lambda *a, **k: history)
    for method in ("get_recurring_themes", "search_playbook", "search_playbook_hybrid"):
        monkeypatch.setattr(cr, method, lambda *a, **k: [])
    monkeypatch.setattr(rcc, "_r175_load_peer_evidence", lambda *a, **k: evidence)
    out = ask_ai_corpus.build_corpus_block(question="SSO login errors for customer Synthetic Alpha?", technology="security", enabled=True)
    view = out.stats["peer_guidance"]
    assert out.stats["likely_next"] == expected
    assert (view["likely_next"] or "insufficient") == expected
    assert f"likely_next={expected}" in out.block
    assert view["ready_for_live_cisco"] is False
    if state == "Closed":
        assert out.stats["decision"] == "already_lived"
        assert not any(s.startswith("CORPUS:PG-") for s in out.allowed_ids)
    else:
        assert out.stats["decision"] == "do_not_repeat"
        assert any(s.startswith("CORPUS:PG-") for s in out.allowed_ids)


def test_receipt_distinguishes_both_severity_gates_and_lived_history():
    from dataclasses import replace
    evidence = _evidence(comparable_severity_band="critical", comparable_method_peer_count=3,
                         comparable_case_severity_band="high", comparable_case_method_peer_count=2,
                         target_path="not_tried")
    variants = [evidence, replace(evidence, comparable_severity_band="low"),
                replace(evidence, comparable_case_method_peer_count=1),
                replace(evidence, target_path="already_open")]
    receipts = {rcc.peer_guidance_source_id(v) for v in variants}
    assert len(receipts) == len(variants)
    assert all(s.startswith("CORPUS:PG-") for s in receipts)
