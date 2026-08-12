"""Focused regressions for Ask AI's live chat request lifecycle."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
ASK_AI_JS = ROOT / "static" / "js" / "ask_ai.js"


def _source() -> str:
    return ASK_AI_JS.read_text(encoding="utf-8")


def _function_source(source: str, name: str) -> str:
    """Return one ordinary JS function declaration using brace balancing."""

    start = source.index(f"function {name}(")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JavaScript function: {name}")


def test_each_chat_turn_owns_a_non_global_answer_target() -> None:
    src = _source()
    assert "function _r127GetOrCreateTurn(question, opts)" in src
    assert "answerEl: _r127BeginAssistantBubble()" in src
    assert "live.className = 'r127-live-answer'" in src
    assert "r127LiveAnswer" not in src
    assert "_r127AnswerTarget" not in src


def test_retry_and_stream_fallback_reuse_the_original_turn() -> None:
    src = _source()
    assert "nextOpts._r68_attempt = opts._r68_attempt + 1" in src
    assert "nextOpts._r127_turn = _turn" in src
    assert "nextOpts._r74_force_sync = true" in src
    assert "nextOpts._r127_turn = opts._r127_turn" in src
    # A user bubble and assistant bubble are created only by the turn factory.
    assert src.count("_r127AppendUserBubble(") == 2
    assert src.count("_r127BeginAssistantBubble(") == 2


def test_live_chat_container_owns_citation_delegation() -> None:
    src = _source()
    assert "function _r74WireSourceBadgeDelegation(container)" in src
    assert "_r74WireSourceBadgeDelegation(r127ChatMessages)" in src
    assert "container.addEventListener('click'" in src
    assert "container.addEventListener('keydown'" in src
    assert "evt.key !== 'Enter' && evt.key !== ' '" in src


def test_sync_history_uses_the_actual_response_payload() -> None:
    src = _source()
    assert "_r68RecordHistoryEntry(_turn.question, data.answer, data)" in src
    assert "_origRenderDebugChip" not in src
    assert "answerContent ? (answerContent.textContent || '')" not in src


def test_busy_state_blocks_all_user_dispatch_and_enter_submission() -> None:
    src = _source()
    dispatcher = _function_source(src, "askAI")
    assert "if (askBtn && askBtn.disabled) { return; }" in dispatcher
    key_handler = src[src.index("questionInput.addEventListener('keydown'") :]
    assert "if (askBtn && askBtn.disabled) { return; }" in key_handler[:700]


def test_manual_cancel_and_timeout_have_distinct_abort_reasons() -> None:
    src = _source()
    assert "runtime.abortReason = 'manual'" in src
    assert "_r68MarkAbortReason(_abortCtl, 'timeout')" in src
    assert "_r68MarkAbortReason(abortCtl, 'timeout')" in src
    assert "abortReason === 'manual'" in src
    assert "Request cancelled. No answer was generated." in src
    assert "Request timed out after 90 seconds." in src
    assert "resolve({ partial: false, aborted: true, reason: abortReason })" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_turn_reuse_and_live_citation_delegation_execute_in_node(tmp_path: Path) -> None:
    """Execute the small DOM-independent helpers against a minimal fake DOM."""

    src = _source()
    helpers = "\n\n".join(
        _function_source(src, name)
        for name in (
            "_r127ScrollChatToBottom",
            "_r127AppendUserBubble",
            "_r127BeginAssistantBubble",
            "_r127GetOrCreateTurn",
            "_r74WireSourceBadgeDelegation",
        )
    )
    harness = f"""
'use strict';
function element(tag) {{
  return {{
    tagName: tag,
    className: '',
    textContent: '',
    children: [],
    parentNode: null,
    appendChild(child) {{ child.parentNode = this; this.children.push(child); }},
    removeChild(child) {{ this.children = this.children.filter((item) => item !== child); }},
    addEventListener(type, handler) {{ this.listeners = this.listeners || {{}}; this.listeners[type] = handler; }}
  }};
}}
const document = {{ createElement: element }};
const r127ChatMessages = element('div');
const r127ChatScroll = {{ scrollTop: 0, scrollHeight: 10 }};
const answerContent = element('div');
let activations = [];
function _r74OnSourceBadgeActivate(sourceId) {{ activations.push(sourceId); }}
{helpers}

const firstOpts = {{}};
const first = _r127GetOrCreateTurn('First question', firstOpts);
const retry = _r127GetOrCreateTurn('First question', firstOpts);
const second = _r127GetOrCreateTurn('Second question', {{}});
if (first !== retry || first.answerEl !== retry.answerEl) throw new Error('retry created a new turn');
if (first.answerEl === second.answerEl) throw new Error('distinct turns share an answer target');
if (r127ChatMessages.children.length !== 4) throw new Error('unexpected duplicate chat rows');
if (first.answerEl.id || second.answerEl.id) throw new Error('answer target has a global id');

const liveContainer = element('div');
_r74WireSourceBadgeDelegation(liveContainer);
const badge = {{
  classList: {{ contains(name) {{ return name === 'r74-source-badge'; }} }},
  getAttribute(name) {{ return name === 'data-source-id' ? 'CASE-42' : ''; }},
  parentNode: liveContainer
}};
let prevented = 0;
liveContainer.listeners.click({{ target: badge, preventDefault() {{ prevented += 1; }} }});
liveContainer.listeners.keydown({{ key: 'Enter', target: badge, preventDefault() {{ prevented += 1; }} }});
liveContainer.listeners.keydown({{ key: ' ', target: badge, preventDefault() {{ prevented += 1; }} }});
if (activations.join(',') !== 'CASE-42,CASE-42,CASE-42') throw new Error('citation activation failed');
if (prevented !== 3) throw new Error('citation activation did not prevent default');
"""
    script = tmp_path / "ask_ai_frontend_harness.js"
    script.write_text(harness, encoding="utf-8")
    subprocess.run(
        ["node", str(script)],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_ask_ai_javascript_parses() -> None:
    subprocess.run(
        ["node", "--check", str(ASK_AI_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
