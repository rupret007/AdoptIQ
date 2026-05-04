"""Round 74 / Build 48 / Phase 5 (P5): Ask AI multi-turn tests.

Pin the conversation toggle + history cap + prompt-prefix injection
contract.  These tests do NOT exercise the LLM end-to-end; they
pin the contract so a future refactor cannot silently break the
multi-turn UX (the operator's expectation: toggle ON =
conversational follow-ups land in context; toggle OFF = each
question is independent).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"
_ASK_AI_JS = _REPO_ROOT / "static" / "js" / "ask_ai.js"
_ASK_AI_HTML = _REPO_ROOT / "templates" / "ask_ai.html"


# ---------------------------------------------------------------------------
# Backend tests
# ---------------------------------------------------------------------------


def test_apply_conversation_history_helper_exists():
    """``_r74_apply_conversation_history`` must exist as a standalone
    helper so both the streaming and (future) sync paths can use it.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_apply_conversation_history

    assert callable(_r74_apply_conversation_history)


def test_apply_conversation_history_prepends_block_to_question():
    """A non-empty history must produce a prompt that starts with a
    "Conversation context" block followed by the current question.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_apply_conversation_history

    history = [
        {'q': 'What are the top renewals?', 'a': 'Acme, Beta, and Gamma all due Q2.'},
        {'q': 'How about by ARR?', 'a': 'Acme is highest at $1.2M.'}
    ]
    out = _r74_apply_conversation_history('Tell me about Acme', history)
    assert 'Conversation context' in out, (
        "Round 74 / P5: helper must prepend a 'Conversation context' block"
    )
    assert 'Q: What are the top renewals?' in out, (
        "Round 74 / P5: each prior turn's question must be carried"
    )
    assert 'A: Acme, Beta, and Gamma' in out, (
        "Round 74 / P5: each prior turn's answer must be carried"
    )
    assert 'Tell me about Acme' in out, (
        "Round 74 / P5: the current question must still be present"
    )


def test_apply_conversation_history_caps_at_five_turns():
    """History longer than 5 turns must be truncated to the last 5
    so the prompt stays bounded.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_apply_conversation_history

    history = [
        {'q': f'Question {i}', 'a': f'Answer {i}'} for i in range(10)
    ]
    out = _r74_apply_conversation_history('Current', history)
    # The first 5 questions should NOT appear (they got truncated).
    assert 'Question 0' not in out
    assert 'Question 4' not in out
    # The last 5 should appear.
    assert 'Question 5' in out
    assert 'Question 9' in out


def test_apply_conversation_history_handles_empty_history():
    """Empty / None / non-list history must return the question unchanged."""
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_apply_conversation_history

    assert _r74_apply_conversation_history('q', []) == 'q'
    assert _r74_apply_conversation_history('q', None) == 'q'  # type: ignore[arg-type]
    assert _r74_apply_conversation_history('q', 'not-a-list') == 'q'  # type: ignore[arg-type]


def test_apply_conversation_history_truncates_long_answers():
    """Answers > 1500 chars must be truncated to bound prompt size."""
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_apply_conversation_history

    long_answer = 'X' * 5000
    history = [{'q': 'Big answer', 'a': long_answer}]
    out = _r74_apply_conversation_history('Current', history)
    assert '...' in out, (
        "Round 74 / P5: long answers must be truncated with '...' marker"
    )
    # The full 5000-char answer must NOT survive verbatim.
    assert long_answer not in out


def test_apply_conversation_history_skips_malformed_entries():
    """Non-dict / empty entries must be silently dropped."""
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_apply_conversation_history

    history = [
        'not a dict',  # type: ignore[list-item]
        None,  # type: ignore[list-item]
        {'q': '', 'a': ''},  # empty
        {'q': 'Real Q', 'a': 'Real A'},
    ]
    out = _r74_apply_conversation_history('Current', history)
    assert 'Real Q' in out
    assert 'Real A' in out
    # Malformed entries shouldn't crash + shouldn't appear as literal strings
    assert 'not a dict' not in out


def test_streaming_endpoint_calls_apply_conversation_history():
    """The streaming endpoint must funnel ``conversation_history``
    through the canonical helper.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    m = re.search(
        r"def ask_ai_portfolio_stream\([^)]*\):[\s\S]*?(?=\n@app\.route|\ndef\s)",
        src,
    )
    assert m, "could not locate streaming endpoint body"
    body = m.group(0)
    assert '_r74_apply_conversation_history' in body, (
        "Round 74 / P5: streaming endpoint must call the helper "
        "before passing the question to the grounded pipeline"
    )


# ---------------------------------------------------------------------------
# Template tests
# ---------------------------------------------------------------------------


def test_conversation_toggle_in_template():
    """The toggle + count badge + reset button must be present."""
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    assert 'id="r74ConversationToggle"' in html, (
        "Round 74 / P5: ask_ai.html must declare #r74ConversationToggle"
    )
    assert 'id="r74ConversationCount"' in html, (
        "Round 74 / P5: ask_ai.html must declare #r74ConversationCount"
    )
    assert 'id="r74ConversationResetBtn"' in html, (
        "Round 74 / P5: ask_ai.html must declare #r74ConversationResetBtn"
    )


def test_conversation_toggle_uses_form_check_switch():
    """The toggle must be a Bootstrap form-check-switch for the
    expected on/off UX.
    """
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    assert 'form-check form-switch' in html, (
        "Round 74 / P5: conversation toggle must use Bootstrap's "
        "form-switch styling"
    )


# ---------------------------------------------------------------------------
# Client-side tests
# ---------------------------------------------------------------------------


def test_ask_ai_js_defines_conversation_state():
    """``_r74ConversationHistory`` + cap + active checker must exist."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert '_r74ConversationHistory' in src, (
        "Round 74 / P5: client must maintain _r74ConversationHistory"
    )
    assert '_R74_CONVO_MAX_TURNS' in src, (
        "Round 74 / P5: client must declare a cap constant"
    )
    assert 'function _r74ConversationActive()' in src, (
        "Round 74 / P5: client must have an _r74ConversationActive helper"
    )


def test_ask_ai_js_caps_history_at_five_turns():
    """Client-side cap must match the server-side cap (5 turns)."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    # The cap constant should be 5.
    m = re.search(r"_R74_CONVO_MAX_TURNS\s*=\s*(\d+)", src)
    assert m, "could not locate _R74_CONVO_MAX_TURNS declaration"
    assert int(m.group(1)) == 5, (
        f"Round 74 / P5: client-side history cap must be 5 turns "
        f"(matches server-side cap in _r74_apply_conversation_history); "
        f"saw {m.group(1)}"
    )


def test_ask_ai_js_streaming_payload_carries_conversation_history():
    """When the toggle is ON the streaming payload must carry
    ``conversation_history``.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert 'payload.conversation_history' in src, (
        "Round 74 / P5: streaming payload must include conversation_history "
        "when the toggle is active"
    )


def test_ask_ai_js_reset_button_clears_history():
    """The reset button must clear the in-memory history."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert 'function _r74ResetConversation()' in src, (
        "Round 74 / P5: client must define _r74ResetConversation"
    )
    assert 'r74ConversationResetBtn.addEventListener' in src, (
        "Round 74 / P5: reset button must be wired to the handler"
    )


def test_ask_ai_js_records_turn_on_successful_answer():
    """Successful answers must be recorded into the conversation
    history when the toggle is active.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert 'function _r74ConversationPushTurn(' in src, (
        "Round 74 / P5: client must define _r74ConversationPushTurn"
    )
    # The push call should appear in the answer-success branch.
    assert '_r74ConversationPushTurn(' in src
