"""Round 95 / Phase D - Ask AI confidence-band UI source-shape tests."""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _js() -> str:
    return (ROOT / "static" / "js" / "r95_confidence_band.js").read_text(encoding="utf-8")


def test_round95_confidence_band_js_is_iife_and_textcontent_only():
    body = _js()
    assert body.lstrip().startswith("(function () {")
    assert_in_source(body, "textContent", label='body')
    assert "innerHTML" not in body
    assert "eval(" not in body


def test_round95_confidence_band_exposes_classify_and_render_api():
    body = _js()
    assert_in_source(body, "window.AdoptIQConfidenceBand", label='body')
    assert_in_source(body, "classifyConfidence: classifyConfidence", label='body')
    assert_in_source(body, "renderConfidenceBand: renderConfidenceBand", label='body')


def test_round95_confidence_band_uses_only_server_scored_branches():
    body = _js()
    assert_in_source(body, "level !== 'High'", label='body')
    assert_in_source(body, "level !== 'Medium'", label='body')
    assert_in_source(body, "level !== 'Low'", label='body')
    assert_in_source(body, "Server trust score", label='body')
    assert "canonical_corrections" not in body


def test_round95_confidence_band_legacy_and_missing_scores_are_unscored():
    body = _js()
    assert body.count("level: 'Unscored'") == 2
    assert_in_source(body, "legacy ungrounded responses are not server-scored", label='body')
    assert_in_source(body, "did not include a server trust score", label='body')


def test_round95_ask_ai_template_loads_confidence_band_after_main_client():
    template = (ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    assert_in_source(template, 'id="r95ConfidenceBand"', label='template')
    assert "js/ask_ai.js" in template
    assert "js/r95_confidence_band.js" in template
    assert template.index("js/ask_ai.js") < template.index("js/r95_confidence_band.js")


def test_round95_ask_ai_client_invokes_confidence_band_renderer():
    client = (ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
    assert "AdoptIQConfidenceBand.renderConfidenceBand(data)" in client
    assert "AdoptIQConfidenceBand.renderConfidenceBand(metaPayload)" in client
    assert "r95ConfidenceBand" in client


def test_round147_ask_ai_renders_explicit_response_states_and_string_warnings():
    template = (ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    client = (ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
    assert_in_source(template, 'id="r147ResponseState"', label='template')
    assert_in_source(template, 'aria-live="polite"', label='template')
    for state in (
        "partial", "stale", "no_data", "retrieval_failed",
        "model_unavailable", "validation_failed",
    ):
        assert state in client
    assert "_r147RenderResponseState(data)" in client
    assert "_r147RenderResponseState(metaPayload)" in client
    assert "typeof warning === 'string'" in client
