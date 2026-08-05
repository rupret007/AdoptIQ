"""Round 147 visible AI trust and failure-state contracts."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]


def test_portfolio_ask_ai_exposes_server_states_without_inference() -> None:
    template = (ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    client = (ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")

    assert 'id="r147ResponseState"' in template
    assert 'aria-live="polite"' in template
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
        assert f'id="{element_id}"' in template
    assert "renderIntelTrust(data)" in template
    assert "data.response_state" in template
    assert "data.confidence" in template
    assert "data.partial_data_warnings" in template
    assert "replaceChildren()" in template
    assert "item.textContent" in template
