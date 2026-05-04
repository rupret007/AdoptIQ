"""Round 74 / Build 48 / Phase 6 (P6): Ask AI follow-up suggestion tests.

Pin the ``_r74_generate_follow_up_suggestions`` helper, the
synchronous endpoint's new ``follow_up_suggestions`` field, the
SSE done-event ``follow_up_suggestions`` field, and the source-
shape of the chip container + click-to-ask wiring in
``static/js/ask_ai.js``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"
_ASK_AI_JS = _REPO_ROOT / "static" / "js" / "ask_ai.js"
_ASK_AI_HTML = _REPO_ROOT / "templates" / "ask_ai.html"


# ---------------------------------------------------------------------------
# Backend tests
# ---------------------------------------------------------------------------


def test_generate_follow_up_suggestions_helper_exists():
    """``_r74_generate_follow_up_suggestions`` must exist."""
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_generate_follow_up_suggestions

    assert callable(_r74_generate_follow_up_suggestions)


def test_generate_follow_up_suggestions_returns_empty_list_for_empty_answer():
    """Empty / falsy answer text must produce an empty suggestions list."""
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_generate_follow_up_suggestions

    assert _r74_generate_follow_up_suggestions('', [], {}) == []
    assert _r74_generate_follow_up_suggestions(None, [], {}) == []  # type: ignore[arg-type]
    assert _r74_generate_follow_up_suggestions('   ', [], {}) == []


def test_generate_follow_up_suggestions_returns_fallback_when_llm_fails():
    """When the secondary LLM call fails, the helper MUST return a
    heuristic fallback list (not an empty list -- the chips are
    bonus UX but should still render).
    """
    sys.path.insert(0, str(_REPO_ROOT))
    # Force the LLM call to raise so the fallback path executes.
    with patch('app_simple._r74_generate_follow_up_suggestions') as mock_helper:
        # Patch is for module-level attr; we need to import after.
        pass
    # Re-import in a separate test that doesn't rely on patching.
    from app_simple import _r74_generate_follow_up_suggestions

    # Force LLM failure by patching adoptiq_backend.generate_llm_response
    with patch('adoptiq_backend.generate_llm_response',
               side_effect=Exception('CircuIT outage')):
        out = _r74_generate_follow_up_suggestions(
            answer_text='The portfolio has 30 customers at risk.',
            evidence_records=[],
            scope_filters={'manager': 'Brian Frazier', 'technology': 'Webex', 'days': 90}
        )
    # Fallback must populate the chips even on LLM failure.
    assert isinstance(out, list)
    assert len(out) > 0, (
        "Round 74 / P6: helper must return a heuristic fallback list "
        "when the LLM call fails -- the chips are bonus UX, NEVER blocked"
    )
    # The fallback should mention the manager OR technology in the text.
    joined = ' '.join(out)
    assert 'Brian Frazier' in joined or 'Webex' in joined, (
        "Round 74 / P6: heuristic fallback must surface the active "
        "scope (manager / technology) so the chips are still actionable"
    )


def test_generate_follow_up_suggestions_caps_at_three_items():
    """Helper output must be capped at 3 items (per the plan)."""
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_generate_follow_up_suggestions

    # Force the LLM to return 10 suggestions.
    with patch('adoptiq_backend.generate_llm_response',
               return_value='["q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "q9", "q10"]'):
        out = _r74_generate_follow_up_suggestions(
            answer_text='Some answer.',
            evidence_records=[],
            scope_filters={}
        )
    assert isinstance(out, list)
    assert len(out) <= 3, (
        f"Round 74 / P6: suggestions must be capped at 3 items; saw {len(out)}"
    )


def test_generate_follow_up_suggestions_falls_back_on_malformed_llm_response():
    """Non-JSON / malformed LLM response must trigger the fallback."""
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_generate_follow_up_suggestions

    # LLM returns garbage (no JSON array anywhere)
    with patch('adoptiq_backend.generate_llm_response',
               return_value='Sorry, I cannot help with that.'):
        out = _r74_generate_follow_up_suggestions(
            answer_text='Some answer.',
            evidence_records=[],
            scope_filters={'manager': 'Anna', 'technology': 'Webex', 'days': 90}
        )
    # Must still return the fallback (not None / not crash).
    assert isinstance(out, list)
    # Fallback chips reference scope.
    joined = ' '.join(out)
    assert 'Anna' in joined or 'Webex' in joined


def test_streaming_endpoint_emits_follow_ups_in_done_event():
    """The streaming generator must call the helper + carry the
    suggestions in the ``done`` SSE event.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    m = re.search(
        r"def ask_ai_portfolio_stream\([^)]*\):[\s\S]*?(?=\n@app\.route|\ndef\s)",
        src,
    )
    assert m, "could not locate streaming endpoint body"
    body = m.group(0)
    assert '_r74_generate_follow_up_suggestions(' in body, (
        "Round 74 / P6: streaming endpoint must call the helper"
    )
    assert "'follow_up_suggestions'" in body or '"follow_up_suggestions"' in body, (
        "Round 74 / P6: done event payload must carry follow_up_suggestions"
    )


def test_sync_endpoint_response_carries_follow_up_suggestions():
    """The synchronous endpoint must add ``follow_up_suggestions`` to
    its JSON response so the client can render chips on both paths.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # Find the success jsonify response block.
    pattern = (
        r"jsonify\(\{[\s\S]*?"
        r"'evidence_records'[\s\S]*?"
        r"'follow_up_suggestions'[\s\S]*?"
        r"\}\)"
    )
    assert re.search(pattern, src), (
        "Round 74 / P6: synchronous response must include "
        "follow_up_suggestions alongside evidence_records"
    )


# ---------------------------------------------------------------------------
# Template tests
# ---------------------------------------------------------------------------


def test_follow_up_chips_container_in_template():
    """The chip container + chip slot must be present in ask_ai.html."""
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    assert 'id="r74FollowUpChipsContainer"' in html, (
        "Round 74 / P6: ask_ai.html must declare #r74FollowUpChipsContainer"
    )
    assert 'id="r74FollowUpChips"' in html, (
        "Round 74 / P6: ask_ai.html must declare #r74FollowUpChips"
    )


def test_follow_up_chips_container_default_hidden():
    """The container must be hidden by default (display:none) so it
    doesn't take up vertical space before any answer arrives.
    """
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    # Find the container declaration and check it carries style="display:none;"
    m = re.search(
        r'id="r74FollowUpChipsContainer"[\s\S]{0,200}?style="[^"]*display:none[^"]*"',
        html,
    )
    assert m, (
        "Round 74 / P6: chip container must be hidden by default "
        "(style='display:none;') so it doesn't reserve empty space"
    )


# ---------------------------------------------------------------------------
# Client-side tests
# ---------------------------------------------------------------------------


def test_ask_ai_js_defines_render_follow_up_chips():
    """``_r74RenderFollowUpChips`` must exist + populate the chip slot."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert 'function _r74RenderFollowUpChips(suggestions)' in src, (
        "Round 74 / P6: client must define _r74RenderFollowUpChips"
    )
    assert 'r74FollowUpChips.appendChild(btn)' in src, (
        "Round 74 / P6: chip renderer must append button elements to "
        "the #r74FollowUpChips container"
    )


def test_ask_ai_js_chip_click_fills_question_input():
    """Clicking a chip must populate the question input."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    # Find _r74RenderFollowUpChips body
    m = re.search(
        r'function _r74RenderFollowUpChips\([^)]*\)\s*\{[\s\S]*?\n    \}',
        src,
    )
    assert m, "could not locate _r74RenderFollowUpChips body"
    body = m.group(0)
    assert 'questionInput.value = qText' in body, (
        "Round 74 / P6: chip click must populate the question input"
    )


def test_ask_ai_js_chip_click_auto_submits_in_conversation_mode():
    """When the conversation toggle is ON, clicking a chip must
    auto-submit (askAI(qText)).  When OFF, just fill the input.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    m = re.search(
        r'function _r74RenderFollowUpChips\([^)]*\)\s*\{[\s\S]*?\n    \}',
        src,
    )
    assert m
    body = m.group(0)
    assert '_r74ConversationActive()' in body, (
        "Round 74 / P6: chip click must check the conversation toggle"
    )
    assert 'askAI(qText)' in body, (
        "Round 74 / P6: chip click must auto-submit when conversation is ON"
    )


def test_ask_ai_js_hide_follow_up_chips_helper_exists():
    """A helper to hide the container on a new question must exist
    so chips from the previous answer don't linger.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert 'function _r74HideFollowUpChips()' in src, (
        "Round 74 / P6: client must define _r74HideFollowUpChips"
    )


def test_ask_ai_js_dispatcher_hides_chips_on_new_question():
    """The askAI dispatcher must hide chips before each new question
    so a stale chip set from the previous answer doesn't confuse
    the operator.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    # Find the askAI dispatcher body
    m = re.search(
        r'function askAI\(question, opts\)\s*\{[\s\S]*?\n    \}',
        src,
    )
    assert m, "could not locate askAI dispatcher"
    body = m.group(0)
    assert '_r74HideFollowUpChips()' in body, (
        "Round 74 / P6: askAI dispatcher must hide chips on each new question"
    )


def test_ask_ai_js_chip_renderer_handles_string_or_object_suggestions():
    """The chip renderer must accept BOTH plain strings (helper output
    format) AND ``{question}`` objects (R68 chip format) so a future
    schema change can land non-disruptively.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    m = re.search(
        r'function _r74RenderFollowUpChips\([^)]*\)\s*\{[\s\S]*?\n    \}',
        src,
    )
    assert m
    body = m.group(0)
    # Should handle both ``typeof s === 'string'`` and the ``s.question`` path.
    assert "typeof s === 'string'" in body, (
        "Round 74 / P6: renderer must accept plain string suggestions"
    )
    assert 's.question' in body, (
        "Round 74 / P6: renderer must accept {question} object suggestions"
    )
