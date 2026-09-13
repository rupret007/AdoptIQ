"""Round 177: make existing peer-guidance surfaces usable and fail-closed.

Interactive Ask AI / Customer 360 cards always show a scannable view
(actionable / method_only / insufficient). Word/Historical Context still
omits thin evidence. ready_for_live_cisco is always false. Fixtures only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import app_simple
import corpus_retriever as cr
import report_corpus_context as rcc
from source_shape_utils import assert_in_source

ROOT = Path(__file__).resolve().parents[1]
METHOD = "Rotated service token and updated documentation for SSO setup."
NEXT_STEP = (
    "Test the peer-observed method on the current barrier, then verify closure "
    "before marking it resolved."
)
SENTINEL = "token=abc host=db.internal path=/Users/private"
PII_METHOD = (
    "Rotated service token for Peer A (jane@cisco.com, TAC9001, notes.csv)"
)
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


def _assert_view_shape(view: dict) -> None:
    assert set(view) == VIEW_KEYS
    assert view["ready_for_live_cisco"] is False
    assert view["honesty_label"] == (
        "Local encrypted corpus only. Not live Cisco validation."
    )
    serialized = json.dumps(view, sort_keys=True)
    assert SENTINEL not in serialized
    assert "will " not in serialized.casefold()


def test_view_actionable_leads_with_next_step_and_never_live() -> None:
    view = rcc.build_peer_guidance_view(_evidence())
    _assert_view_shape(view)
    assert view["status"] == "actionable"
    assert view["next_step"] == NEXT_STEP
    assert view["likely_next"] == "closure"
    assert "not a certainty" in str(view["likely_next_label"])
    assert view["source_id"].startswith("CORPUS:PG-")
    lines = rcc.format_peer_guidance_scan_lines(_evidence())
    assert lines[0].startswith("Next step:")
    assert any(line.startswith("Observed-in-peers:") for line in lines)


def test_view_method_only_mixed_trajectory_has_no_likely_next() -> None:
    evidence = _evidence(
        likely_next="insufficient",
        next_step="",
        method_barrier_closed_peer_count=1,
        method_barrier_open_peer_count=1,
        method_closed_peer_count=1,
        method_open_peer_count=1,
    )
    view = rcc.build_peer_guidance_view(evidence)
    _assert_view_shape(view)
    assert view["status"] == "method_only"
    assert view["next_step"] == ""
    assert view["likely_next"] == ""
    assert view["likely_next_label"] == ""
    assert view["method"] == METHOD
    assert view["insufficient_reason"] == "mixed_evidence"
    assert "next step" in str(view["insufficient_copy"]).casefold()
    assert "likely-next" not in json.dumps(view)


def test_view_thin_cohort_is_insufficient() -> None:
    thin = _evidence(
        evidence_sufficient=False,
        peer_customer_count=1,
        dominant_method_peers=1,
        likely_next="insufficient",
        next_step="",
    )
    view = rcc.build_peer_guidance_view(thin)
    _assert_view_shape(view)
    assert view["status"] == "insufficient"
    assert view["insufficient_reason"] == "thin_cohort"
    assert view["next_step"] == ""
    assert view["method"] == ""
    assert view["source_id"] == ""
    scan = rcc.format_peer_guidance_scan_lines(thin)
    assert scan[0].startswith("Peer guidance: Not enough evidence")
    assert scan[-1] == "Local encrypted corpus only. Not live Cisco validation."


def test_view_unsafe_method_fails_closed() -> None:
    view = rcc.build_peer_guidance_view(
        _evidence(dominant_method_text=PII_METHOD)
    )
    _assert_view_shape(view)
    assert view["status"] == "insufficient"
    assert view["insufficient_reason"] == "unsafe_method"
    assert view["method"] == ""
    blob = json.dumps(view)
    assert "jane@cisco.com" not in blob
    assert "TAC9001" not in blob
    assert "Peer A" not in blob


def test_view_strips_fortune_telling_will() -> None:
    view = rcc.build_peer_guidance_view(
        _evidence(next_step="CS will close this tomorrow after the method")
    )
    _assert_view_shape(view)
    assert view["next_step"] == ""
    assert view["status"] == "method_only"
    assert "will " not in json.dumps(view).casefold()


def test_public_view_stomps_live_flag_and_drops_extra_keys() -> None:
    stuffed = rcc.build_peer_guidance_view(_evidence())
    stuffed["ready_for_live_cisco"] = True
    stuffed["provider_path"] = SENTINEL
    stuffed["extra_secret"] = SENTINEL
    public = rcc.public_peer_guidance_view(stuffed)
    _assert_view_shape(public)
    assert public["status"] == "actionable"
    assert "provider_path" not in public
    assert "extra_secret" not in public
    serialized = json.dumps(public)
    assert SENTINEL not in serialized


def test_public_view_pii_method_becomes_unsafe() -> None:
    public = rcc.public_peer_guidance_view(
        {
            "status": "actionable",
            "method": PII_METHOD,
            "next_step": NEXT_STEP,
            "likely_next": "closure",
            "ready_for_live_cisco": True,
        }
    )
    _assert_view_shape(public)
    assert public["status"] == "insufficient"
    assert public["insufficient_reason"] == "unsafe_method"


def test_r147_public_ai_corpus_attaches_sanitized_peer_guidance() -> None:
    corpus = {
        "available": True,
        "banner": SENTINEL,
        "stats": {
            "chunks": 3,
            "provider_path": SENTINEL,
            "peer_guidance": {
                "status": "actionable",
                "next_step": NEXT_STEP,
                "likely_next": "closure",
                "method": METHOD,
                "ready_for_live_cisco": True,
                "provider_path": SENTINEL,
                "extra_secret": SENTINEL,
            },
        },
    }
    public = app_simple._r147_public_ai_corpus(corpus)
    serialized = json.dumps(public, sort_keys=True, default=str)
    assert SENTINEL not in serialized
    assert "provider_path" not in serialized
    assert "extra_secret" not in serialized
    view = public["peer_guidance"]
    _assert_view_shape(view)
    assert view["status"] == "actionable"
    assert view["ready_for_live_cisco"] is False
    assert public["stats"] == {"chunks": 3}


def test_ask_ai_js_card_is_iife_textcontent_only() -> None:
    body = (ROOT / "static" / "js" / "r177_peer_guidance_card.js").read_text(
        encoding="utf-8"
    )
    assert body.lstrip().startswith("(function () {")
    assert_in_source(body, "textContent", label="body")
    assert "innerHTML" not in body
    assert "eval(" not in body
    assert "window.AdoptIQPeerGuidanceCard" in body
    assert "ready_for_live_cisco === true" in body
    template = (ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    assert 'id="r177PeerGuidanceCard"' in template
    assert "js/r177_peer_guidance_card.js" in template
    # Round 177: comments mention the module before the script tags; pin
    # load order on the actual url_for script srcs (ask_ai.js first).
    ask_ai_src = "filename='js/ask_ai.js'"
    r177_src = "filename='js/r177_peer_guidance_card.js'"
    assert ask_ai_src in template
    assert r177_src in template
    assert template.index(ask_ai_src) < template.index(r177_src)
    client = (ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
    assert "AdoptIQPeerGuidanceCard.renderPeerGuidance(data)" in client
    assert "AdoptIQPeerGuidanceCard.renderPeerGuidance(metaPayload)" in client
    assert "AdoptIQPeerGuidanceCard.hidePeerGuidance()" in client


def test_customer_360_template_shows_honesty_not_hidden_thin() -> None:
    html = (ROOT / "templates" / "customer_360.html").read_text(encoding="utf-8")
    assert "data-r177-peer-guidance" in html
    assert "data-r177-peer-insufficient" in html
    assert "r177-next-step" in html
    assert "{% if peer_guidance_line %}" not in html
    # Round 177: the header comment says "never use |safe"; pin that no
    # Jinja expression or tag actually applies the filter.
    assert re.search(r"\{\{[^}]*\|\s*safe", html) is None
    assert re.search(r"\{%[^%]*\|\s*safe", html) is None
    assert "Not live Cisco validation." in html
    # CSS selectors mention the marker first; the card attribute is later
    # and must sit inside the history gate.
    assert html.index("{% if history %}") < html.rindex("data-r177-peer-guidance")


def test_operator_surfaces_cannot_claim_live_cisco() -> None:
    intel = (ROOT / "static" / "js" / "intel_status.js").read_text(encoding="utf-8")
    assert "r177PeerGuidanceHonesty" in intel
    assert "not live Cisco validation" in intel
    admin = (ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    assert "is not live Cisco validation" in admin
    sanitizer = (ROOT / "report_corpus_context.py").read_text(encoding="utf-8")
    assert "never honor a true flag" in sanitizer
    r147 = (ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "public_peer_guidance_view" in r147


@pytest.mark.parametrize(
    "reason",
    [
        "unavailable",
        "thin_cohort",
        "no_dominant_method",
        "mixed_evidence",
        "unsafe_method",
        "already_lived",
        "unlived_path",  # Round 182
    ],
)
def test_insufficient_copy_never_uses_hyphenated_likely_next(reason: str) -> None:
    view = rcc.empty_peer_guidance_view(reason=reason)
    _assert_view_shape(view)
    blob = json.dumps(view)
    assert "likely-next" not in blob
    assert view["insufficient_copy"]
    assert view["status"] == "insufficient"
