"""Round 68 / Build 42 (C6): pin the personalized suggestion-chip
endpoint and the JS that consumes it.

Pre-R68 the Ask AI page rendered six static suggestion buttons
that were the same for every operator regardless of which manager
or technology they had selected.  R68 adds:

  * GET /api/ask-ai/suggestions -- read-only, never blocks on
    Snowflake / LLM, returns 4 personalised chips + 2 static
    fallback chips for the operator's selected scope.
  * Client-side fetch on DOMContentLoaded + on every selector
    change (manager / tech / days) so chips refresh as the
    operator narrows scope.
  * Per-category colour cue (Bootstrap utility classes).

These tests pin both the endpoint behaviour AND the JS source
shape so a future refactor can't silently break either side
of the wire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def app_client():
    import app_simple
    app_simple.app.config['WTF_CSRF_ENABLED'] = False
    app_simple.app.config['TESTING'] = True
    return app_simple.app.test_client()


_ROOT = Path(__file__).resolve().parent.parent
_ASK_AI_JS = _ROOT / "static" / "js" / "ask_ai.js"
_ASK_AI_HTML = _ROOT / "templates" / "ask_ai.html"


# --- Endpoint behaviour ------------------------------------------------------


def test_r68_suggestions_endpoint_returns_200_with_payload(app_client) -> None:
    r = app_client.get('/api/ask-ai/suggestions')
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    data = json.loads(r.data)
    assert data["ok"] is True
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)
    assert len(data["suggestions"]) >= 4, (
        "endpoint must return at least 4 chips even with empty scope"
    )


def test_r68_suggestions_endpoint_returns_personalized_chips(app_client) -> None:
    """When manager + technology are supplied, the chip text must
    incorporate them so the chip is actionable for that scope."""
    r = app_client.get('/api/ask-ai/suggestions?manager=Brian%20Frazier&technology=Contact%20Center&days=90')
    assert r.status_code == 200
    data = json.loads(r.data)
    chips = data["suggestions"]
    assert any("Brian Frazier" in (c.get("question") or "") for c in chips), (
        "no chip mentions the manager -- chips not personalized"
    )
    assert any("Contact Center" in (c.get("question") or "") for c in chips), (
        "no chip mentions the technology -- chips not personalized"
    )


def test_r68_suggestions_endpoint_returns_grouped_categories(app_client) -> None:
    """Each chip must carry a ``category`` so the UI can group them."""
    r = app_client.get('/api/ask-ai/suggestions')
    data = json.loads(r.data)
    chips = data["suggestions"]
    for c in chips:
        assert "category" in c, f"chip missing category: {c}"
    categories = {c["category"] for c in chips}
    expected = {"top_risk", "stale_barriers", "recent_renewal", "last_report"}
    assert expected.issubset(categories), (
        f"missing required categories: {expected - categories}"
    )


def test_r68_suggestions_endpoint_caps_chip_count(app_client) -> None:
    """The endpoint must cap the chip count so the UI strip never
    explodes (max ~6: 4 personalised + 2 static fallback)."""
    r = app_client.get('/api/ask-ai/suggestions')
    data = json.loads(r.data)
    assert len(data["suggestions"]) <= 6, (
        f"endpoint returned {len(data['suggestions'])} chips -- "
        "would overflow the chip strip"
    )


def test_r68_suggestions_endpoint_validates_inputs(app_client) -> None:
    """Manager / technology must be length-capped + allow-listed so
    the endpoint can't be used to smuggle arbitrary text into the
    chip questions."""
    # Long input -- truncated/rejected.
    long = "A" * 500
    r = app_client.get(f'/api/ask-ai/suggestions?manager={long}&technology={long}')
    assert r.status_code == 200, "endpoint must not 500 on long input"
    data = json.loads(r.data)
    chips = data["suggestions"]
    # The 500-char manager name MUST NOT appear in any chip text
    # (it should be empty-string normalised + the chip says
    # "my portfolio" instead).
    for c in chips:
        assert long not in (c.get("question") or ""), (
            "long input bypassed the validator"
        )


def test_r68_suggestions_endpoint_clamps_days(app_client) -> None:
    """Days must be clamped to [1, 365]."""
    r = app_client.get('/api/ask-ai/suggestions?days=999999')
    assert r.status_code == 200, "endpoint must not 500 on huge days"
    data = json.loads(r.data)
    chips = data["suggestions"]
    # The 999999 must not leak verbatim.
    for c in chips:
        assert "999999" not in (c.get("question") or ""), (
            "huge days bypassed the clamp -- would put 999999 in chip text"
        )


def test_r68_suggestions_endpoint_handles_empty_scope(app_client) -> None:
    """Empty scope (no manager, no tech) must still return chips
    with a generic 'my portfolio' phrasing."""
    r = app_client.get('/api/ask-ai/suggestions?manager=&technology=&days=90')
    data = json.loads(r.data)
    chips = data["suggestions"]
    assert chips, "empty scope returned no chips"
    assert any("my portfolio" in (c.get("question") or "").lower() for c in chips), (
        "empty scope missing fallback 'my portfolio' phrasing"
    )


def test_r68_suggestions_endpoint_never_calls_llm(app_client) -> None:
    """Sanity check: the endpoint must respond fast (<1s) because
    it never blocks on Snowflake / LLM.  A regression that wired
    in a Snowflake call would surface as a multi-second response."""
    import time
    start = time.time()
    r = app_client.get('/api/ask-ai/suggestions?manager=Brian%20Frazier&technology=Webex&days=90')
    elapsed = time.time() - start
    assert r.status_code == 200
    assert elapsed < 2.0, (
        f"endpoint took {elapsed:.2f}s -- regression: now blocking on Snowflake/LLM"
    )


# --- JS wiring ---------------------------------------------------------------


def test_r68_js_fetches_suggestions_on_page_load() -> None:
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert "_r68FetchAndRenderChips()" in src, (
        "_r68FetchAndRenderChips not invoked on page load -- chips never refresh"
    )
    assert "/api/ask-ai/suggestions" in src, (
        "JS doesn't hit the suggestions endpoint"
    )


def test_r68_js_refreshes_chips_on_selector_change() -> None:
    """The chips must refresh when the operator changes manager,
    tech, or days -- otherwise the chips reflect a stale scope."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert "['aiManager', 'aiTech', 'aiDays']" in src, (
        "selector-change listener not wired for all three selectors"
    )
    assert "addEventListener('change', _r68FetchAndRenderChips)" in src, (
        "selector change doesn't refresh chips"
    )


def test_r68_js_renders_chips_with_textContent_not_innerHTML() -> None:
    """XSS guard -- chip text comes from server data and MUST use
    textContent, never innerHTML."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    body_start = src.find("function _r68RenderSuggestions(")
    assert body_start != -1
    body_end = src.find("\n    }\n", body_start)
    body = src[body_start:body_end]
    assert "innerHTML" not in body, (
        "_r68RenderSuggestions uses innerHTML (XSS sink)"
    )
    assert ".textContent" in body, (
        "_r68RenderSuggestions must use textContent"
    )


def test_r68_js_handles_endpoint_failure_silently() -> None:
    """If the suggestions fetch fails, the SSR fallback chips must
    remain on screen -- the JS must NOT clear them on error."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    # Scope to the _r68FetchAndRenderChips function body specifically.
    fn_start = src.find("function _r68FetchAndRenderChips(")
    assert fn_start != -1
    # Function ends at the next column-4 closing brace followed by a
    # blank line (matches our codebase indentation convention).  Use
    # a generous slice to catch the entire fetch chain.
    fn_body = src[fn_start:fn_start + 4500]
    assert ".catch(function" in fn_body, "no catch handler in fetch chain"
    # Look at the catch handler body and ensure it doesn't blow away
    # the SSR chips.
    catch_idx = fn_body.find(".catch(function")
    assert catch_idx != -1
    catch_body = fn_body[catch_idx:catch_idx + 800]
    assert "removeChild" not in catch_body, (
        "catch handler clears SSR chips -- chips disappear on transient failure"
    )


def test_r68_js_groups_chips_by_category_via_colour_class() -> None:
    """Each category should map to a Bootstrap colour class so the
    operator can visually group chips."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert "categoryColours" in src, "category-colour map missing"
    for category in ("top_risk", "stale_barriers", "recent_renewal", "last_report"):
        assert category in src, f"category {category!r} not mapped to a colour"


# --- Template wiring ---------------------------------------------------------


def test_r68_template_has_suggestions_container() -> None:
    """The container div must carry the id the JS targets."""
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    assert 'id="r68SuggestionChipsContainer"' in html, (
        "suggestion chips container missing -- JS can't find the slot"
    )


def test_r68_template_ssr_fallback_chips_present() -> None:
    """The SSR (no-JS) fallback must render the static chip set
    so the page is non-blank without JS."""
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    # At least one of the static fallback chips.
    assert "Which accounts have stale barriers over 90 days old" in html, (
        "SSR fallback chip missing"
    )


# --- Smoke -------------------------------------------------------------------


def test_r68_module_imports_cleanly() -> None:
    import app_simple  # noqa: F401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
