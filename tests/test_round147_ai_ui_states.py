"""Round 147 visible AI trust and failure-state contracts."""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]


def test_portfolio_ask_ai_exposes_server_states_without_inference() -> None:
    template = (ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    client = (ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")

    assert_in_source(template, 'id="r147ResponseState"', label='template')
    assert_in_source(template, 'aria-live="polite"', label='template')
    assert "_r147RenderResponseState(data)" in client
    assert "_r147RenderResponseState(metaPayload)" in client
    for state in (
        "partial", "stale", "no_data", "retrieval_failed",
        "model_unavailable", "validation_failed",
    ):
        assert state in client
    assert "textContent" in client


def test_ask_intel_renders_trust_state_and_limitations_safely() -> None:
    template = (
        ROOT / "templates" / "external_intelligence.html"
    ).read_text(encoding="utf-8")
    Environment(autoescape=True).parse(template)

    for element_id in (
        "aiTrustState", "aiResponseState", "aiConfidence", "aiLimitations",
    ):
        assert_in_source(template, f'id="{element_id}"', label='template')
    assert_in_source(template, "renderIntelTrust(data)", label='template')
    assert_in_source(template, "data.response_state", label='template')
    assert_in_source(template, "data.confidence", label='template')
    assert_in_source(template, "data.partial_data_warnings", label='template')
    assert_in_source(template, "replaceChildren()", label='template')
    assert_in_source(template, "item.textContent", label='template')
