"""Round 182: Ask AI peer guidance must use the question's lived path.

Existing Ask AI / Customer 360 / Historical Context surfaces only.
A general status question keeps Round 175 ranking. A named theme
(SSO/login, latency, …) must not publish next-step / likely-next from
a stronger unrelated path. Thin named paths hard-stop with
``Not enough evidence from peers who lived that path.``
Fixtures / mocks only. ready_for_live_cisco stays false.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import corpus_retriever as cr
import report_corpus_context as rcc
from source_shape_utils import assert_in_source

ROOT = Path(__file__).resolve().parents[1]
METHOD = "Rotated service token and updated documentation for SSO setup."
NEXT_STEP = "apply that observed method to the current open work next"
UNLIVED = "Not enough evidence from peers who lived that path."


def _evidence(**overrides: object) -> cr.PeerGuidanceEvidence:
    payload: dict[str, object] = {
        "theme": "authentication",
        "technology": "security",
        "peer_customer_count": 3,
        "resolved_peer_count": 2,
        "dominant_method_text": METHOD,
        "dominant_method_peers": 2,
        "closed_peer_count": 2,
        "open_peer_count": 1,
        "close_time_median_days": 12.0,
        "pulse_recovered_count": 0,
        "pulse_worsened_count": 0,
        "likely_next": "closure",
        "next_step": NEXT_STEP,
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


def _barriers() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(theme="authentication", technology="security", occurrences=3),
        SimpleNamespace(theme="performance", technology="collaboration", occurrences=1),
    ]


def _load_for_theme(theme: str, tech: str) -> cr.PeerGuidanceEvidence:
    if theme.casefold() == "authentication":
        return _evidence(theme="authentication", technology=tech)
    return _evidence(
        theme=theme,
        technology=tech,
        likely_next="insufficient",
        next_step="",
        evidence_sufficient=False,
        dominant_method_peers=0,
        dominant_method_text="",
        method_closed_peer_count=0,
        method_barrier_closed_peer_count=0,
        peer_customer_count=1,
        resolved_peer_count=0,
    )


def test_question_path_theme_general_is_empty() -> None:
    assert rcc._r182_question_path_theme("") == ""
    assert rcc._r182_question_path_theme("How is customer Synthetic Alpha doing?") == ""
    assert rcc._r182_normalize_prefer_theme("general") == ""
    assert rcc._r182_normalize_prefer_theme("  ") == ""


def test_question_path_theme_names_login_and_latency() -> None:
    assert rcc._r182_question_path_theme(
        "SSO login errors for customer Synthetic Alpha?"
    ) == "authentication"
    assert rcc._r182_question_path_theme(
        "What about import latency for customer Synthetic Alpha?"
    ) == "performance"


def test_prefer_theme_does_not_publish_stronger_other_path(monkeypatch) -> None:
    monkeypatch.setattr(
        rcc,
        "_r175_load_peer_evidence",
        lambda customer, theme, tech: _load_for_theme(theme, tech),
    )
    default = rcc.select_ranked_peer_guidance(
        customer="Synthetic Alpha",
        barriers=_barriers(),
    )
    assert default is not None
    assert default.theme == "authentication"
    assert default.likely_next == "closure"

    named = rcc.select_ranked_peer_guidance(
        customer="Synthetic Alpha",
        barriers=_barriers(),
        prefer_theme="performance",
    )
    assert named is not None
    assert named.theme == "performance"
    assert named.likely_next == "insufficient"
    assert named.evidence_sufficient is False


def test_prefer_theme_with_no_matching_barrier_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(
        rcc,
        "_r175_load_peer_evidence",
        lambda customer, theme, tech: _load_for_theme(theme, tech),
    )
    ranked = rcc.select_ranked_peer_guidance(
        customer="Synthetic Alpha",
        barriers=_barriers(),
        prefer_theme="licensing",
    )
    assert ranked is None


def test_general_prefer_theme_keeps_existing_ranking(monkeypatch) -> None:
    monkeypatch.setattr(
        rcc,
        "_r175_load_peer_evidence",
        lambda customer, theme, tech: _load_for_theme(theme, tech),
    )
    ranked = rcc.select_ranked_peer_guidance(
        customer="Synthetic Alpha",
        barriers=_barriers(),
        prefer_theme="general",
    )
    assert ranked is not None
    assert ranked.theme == "authentication"
    assert ranked.likely_next == "closure"


def test_unlived_path_view_is_honest_and_never_live() -> None:
    view = rcc.empty_peer_guidance_view(reason="unlived_path")
    assert view["status"] == "insufficient"
    assert view["insufficient_reason"] == "unlived_path"
    assert view["insufficient_copy"] == UNLIVED
    assert view["next_step"] == ""
    assert view["likely_next"] == ""
    assert view["source_id"] == ""
    assert view["ready_for_live_cisco"] is False
    assert "likely-next" not in view["insufficient_copy"]
    public = rcc.public_peer_guidance_view(view)
    assert public["insufficient_reason"] == "unlived_path"
    assert public["insufficient_copy"] == UNLIVED
    assert public["ready_for_live_cisco"] is False
    assert public["source_id"] == ""


def test_public_view_stomps_live_flag_on_unlived_path() -> None:
    stuffed = dict(rcc.empty_peer_guidance_view(reason="unlived_path"))
    stuffed["ready_for_live_cisco"] = True
    stuffed["next_step"] = "call jane@cisco.com about TAC9001"
    public = rcc.public_peer_guidance_view(stuffed)
    assert public["ready_for_live_cisco"] is False
    assert public["next_step"] == ""
    assert "jane@" not in str(public)
    assert "TAC9001" not in str(public)


def test_ask_ai_named_path_withholds_other_theme(monkeypatch) -> None:
    import ask_ai_corpus

    history = SimpleNamespace(
        name="Synthetic Alpha",
        manager=None,
        technology="security",
        first_seen="2026-01-01",
        last_seen="2026-08-01",
        cases=(),
        barriers=_barriers(),
    )

    monkeypatch.setattr(cr, "is_configured", lambda: True)
    monkeypatch.setattr(cr, "get_customer_history", lambda *a, **k: history)
    monkeypatch.setattr(cr, "get_recurring_themes", lambda *a, **k: [])
    monkeypatch.setattr(cr, "search_playbook", lambda *a, **k: [])
    monkeypatch.setattr(cr, "search_playbook_hybrid", lambda *a, **k: [])
    monkeypatch.setattr(
        rcc,
        "_r175_load_peer_evidence",
        lambda customer, theme, tech: _load_for_theme(theme, tech),
    )

    general = ask_ai_corpus.build_corpus_block(
        question="How is customer Synthetic Alpha doing?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    assert general.stats.get("likely_next") == "closure"
    assert general.stats.get("insufficient_peer_evidence") is False
    assert "Observed-in-peers:" in general.block
    assert any(item.startswith("CORPUS:PG-") for item in general.allowed_ids)

    latency = ask_ai_corpus.build_corpus_block(
        question="What about import latency for customer Synthetic Alpha?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    assert latency.stats.get("likely_next") == "insufficient"
    assert latency.stats.get("insufficient_peer_evidence") is True
    assert latency.stats.get("peer_path_theme") == "performance"
    assert "insufficient_peer_evidence=true" in latency.block
    assert "no likely-next is asserted" in latency.block
    assert "Observed-in-peers:" not in latency.block
    assert not any(item.startswith("CORPUS:PG-") for item in latency.allowed_ids)
    view = latency.stats.get("peer_guidance") or {}
    assert view.get("insufficient_reason") == "unlived_path"
    assert view.get("insufficient_copy") == UNLIVED
    assert view.get("next_step") == ""
    assert view.get("likely_next") == ""
    assert view.get("ready_for_live_cisco") is False
    for leaked in ("Peer A", "jane@cisco.com", "TAC9001"):
        assert leaked not in latency.block
        assert leaked not in str(view)


def test_ask_ai_named_matching_path_still_publishes(monkeypatch) -> None:
    import ask_ai_corpus

    history = SimpleNamespace(
        name="Synthetic Alpha",
        manager=None,
        technology="security",
        first_seen="2026-01-01",
        last_seen="2026-08-01",
        cases=(),
        barriers=_barriers(),
    )
    monkeypatch.setattr(cr, "is_configured", lambda: True)
    monkeypatch.setattr(cr, "get_customer_history", lambda *a, **k: history)
    monkeypatch.setattr(cr, "get_recurring_themes", lambda *a, **k: [])
    monkeypatch.setattr(cr, "search_playbook", lambda *a, **k: [])
    monkeypatch.setattr(cr, "search_playbook_hybrid", lambda *a, **k: [])
    monkeypatch.setattr(
        rcc,
        "_r175_load_peer_evidence",
        lambda customer, theme, tech: _load_for_theme(theme, tech),
    )

    out = ask_ai_corpus.build_corpus_block(
        question="SSO login errors for customer Synthetic Alpha?",
        technology="security",
        enabled=True,
        top_k=3,
    )
    assert out.stats.get("likely_next") == "closure"
    assert out.stats.get("peer_theme") == "authentication"
    assert out.stats.get("peer_path_theme") == "authentication"
    assert "Observed-in-peers:" in out.block
    assert any(item.startswith("CORPUS:PG-") for item in out.allowed_ids)
    view = out.stats.get("peer_guidance") or {}
    assert view.get("status") == "actionable"
    assert view.get("ready_for_live_cisco") is False


def test_round182_source_shape_pins() -> None:
    ctx = (ROOT / "report_corpus_context.py").read_text(encoding="utf-8")
    ask = (ROOT / "ask_ai_corpus.py").read_text(encoding="utf-8")
    assert_in_source(ctx, "unlived_path")
    assert_in_source(ctx, "Not enough evidence from peers who lived that path.")
    assert_in_source(ctx, "prefer_theme")
    assert_in_source(ctx, "_r182_question_path_theme")
    assert_in_source(ctx, "if wanted and theme.casefold() != wanted")
    assert_in_source(ask, "prefer_theme=question_theme")
    assert_in_source(ask, "path_unlived")
    assert_in_source(ask, 'reason="unlived_path"')
    assert "history.technology or technology" not in ask
    # Customer 360 / Historical Context stay unfiltered (no question).
    assert "prefer_theme=" not in ctx  # no call-site kwarg in this module
    assert_in_source(ctx, 'prefer_theme: str = ""')
    assert_in_source(
        ctx,
        "return select_ranked_peer_guidance(\n"
        '        customer=str(getattr(history, "name", customer) or customer),\n'
        '        barriers=getattr(history, "barriers", ()) or (),\n'
        '        fallback_technology=str(tech or ""),\n'
        "    )",
    )
    assert_in_source(
        ctx,
        "ranked = select_ranked_peer_guidance(\n"
        "                customer=_safe_str(history.name, limit=200),\n"
        "                barriers=history.barriers,\n"
        '                fallback_technology="",  # Round 175.4: never history.technology\n'
        "            )",
    )
