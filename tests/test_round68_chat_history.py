"""Round 68 / Build 42 (C8): pin the localStorage chat-history panel.

Pre-R68 the Ask AI page lost every Q/A pair the moment the user
navigated away from the page.  Operators repeatedly asked us for
a "what did I ask yesterday?" feature.  R68 adds:

  * localStorage-backed history (keyed at ``r68_ask_ai_history``)
    capped at 20 entries via FIFO eviction.  Lives ENTIRELY in the
    browser -- the questions never reach the server-side persisted
    diag (which only stores the sanitised retrieval pipeline).
  * A collapsible card on the Ask AI page that lists the entries
    newest-first with manager / tech / days context badges.
  * Four per-row actions: Re-ask / Refine / Copy / Delete.
  * Top-level "Clear all" + collapse/expand toggle.

These tests pin both the template wiring and the JS contract so
a future refactor can't silently drop the history feature.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parent.parent
_ASK_AI_JS = _ROOT / "static" / "js" / "ask_ai.js"
_ASK_AI_HTML = _ROOT / "templates" / "ask_ai.html"


def _ask_ai_js() -> str:
    return _ASK_AI_JS.read_text(encoding="utf-8")


def _ask_ai_html() -> str:
    return _ASK_AI_HTML.read_text(encoding="utf-8")


# --- Template wiring ---------------------------------------------------------


def test_r68_history_panel_present_in_template() -> None:
    html = _ask_ai_html()
    assert 'id="r68ChatHistoryCard"' in html, "history card missing"
    assert 'id="r68ChatHistoryList"' in html, "history list missing"
    assert 'id="r68ChatHistoryClearBtn"' in html, "clear button missing"
    assert 'id="r68ChatHistoryToggleBtn"' in html, "toggle button missing"


def test_r68_history_panel_hidden_until_first_entry() -> None:
    """The card must be ``display:none`` until the JS unhides it
    after the first answer is recorded -- otherwise an empty
    history card flashes on every page load."""
    html = _ask_ai_html()
    idx = html.find('id="r68ChatHistoryCard"')
    snippet = html[idx : idx + 400]
    assert 'style="display:none' in snippet, (
        "history card not hidden until first entry -- empty card flash"
    )


# --- JS contract -------------------------------------------------------------


def test_r68_history_storage_key_namespaced() -> None:
    """The localStorage key must be namespaced (``r68_`` prefix) so
    other apps on the same origin can't collide."""
    src = _ask_ai_js()
    assert "R68_HISTORY_KEY = 'r68_ask_ai_history'" in src, (
        "history key missing or not namespaced"
    )


def test_r68_history_max_default_is_20() -> None:
    src = _ask_ai_js()
    assert "R68_HISTORY_MAX = 20" in src, (
        "history cap regressed -- changes the FIFO contract"
    )


def test_r68_history_load_safe_against_corrupt_json() -> None:
    """``_r68LoadHistory`` must catch JSON.parse errors and return
    [] -- otherwise a corrupt entry would crash the entire page."""
    src = _ask_ai_js()
    fn_idx = src.find("function _r68LoadHistory(")
    assert fn_idx != -1
    body = src[fn_idx : fn_idx + 600]
    assert "try {" in body and "catch" in body, (
        "_r68LoadHistory has no try/catch -- corrupt localStorage crashes the page"
    )


def test_r68_history_save_uses_fifo_slice() -> None:
    """``_r68SaveHistory`` must slice to the FIFO cap before
    persisting -- otherwise an unbounded entry list would
    eventually blow the localStorage quota and silently fail."""
    src = _ask_ai_js()
    assert "entries.slice(-R68_HISTORY_MAX)" in src, (
        "save doesn't enforce FIFO cap -- localStorage quota risk"
    )


def test_r68_history_records_after_each_answer() -> None:
    """Every successful answer must trigger ``_r68RecordHistoryEntry``
    with the raw synchronous response, not text scraped from a legacy node."""
    src = _ask_ai_js()
    assert "_r68RecordHistoryEntry(_turn.question, data.answer, data)" in src, (
        "sync history must record the actual response on its owning turn"
    )
    assert "_origRenderDebugChip" not in src, (
        "history must not be coupled to debug-chip rendering"
    )
    assert "answerContent ? (answerContent.textContent || '')" not in src, (
        "history still scrapes the hidden legacy answer node"
    )


def test_r68_history_dedupes_consecutive_same_question() -> None:
    """If the user clicks Re-ask on the same question twice, we
    overwrite the most recent entry instead of stacking duplicates."""
    src = _ask_ai_js()
    assert "entries[entries.length - 1].q === question" in src, (
        "history doesn't dedupe consecutive same-question entries"
    )


def test_r68_history_caps_question_and_answer_text() -> None:
    """Each stored q/a string must be capped so a verbose answer
    can't blow the ~5MB localStorage quota in a single entry."""
    src = _ask_ai_js()
    assert "String(question).slice(0, 600)" in src, "question not capped"
    assert "String(answer || '').slice(0, 1200)" in src, "answer not capped"


def test_r68_history_renders_with_textContent_not_innerHTML() -> None:
    """XSS guard -- the q/a strings come from user-supplied input
    and server data; they must use textContent, never innerHTML."""
    src = _ask_ai_js()
    fn_idx = src.find("function _r68BuildHistoryRow(")
    assert fn_idx != -1
    body_end = src.find("\n    }\n", fn_idx)
    body = src[fn_idx:body_end]
    assert "innerHTML" not in body, (
        "_r68BuildHistoryRow uses innerHTML (XSS sink)"
    )
    assert ".textContent" in body, "_r68BuildHistoryRow must use textContent"


def test_r68_history_has_four_actions() -> None:
    """Each row must have all four actions (Re-ask, Refine, Copy,
    Delete).  Dropping one would break the operator workflow."""
    src = _ask_ai_js()
    fn_idx = src.find("function _r68BuildHistoryRow(")
    body_end = src.find("\n    }\n", fn_idx)
    body = src[fn_idx:body_end]
    for label in ("'Re-ask'", "'Refine'", "'Copy'", "'Delete'"):
        assert label in body, f"history row missing {label} action button"


def test_r68_history_renders_newest_first() -> None:
    """Newer entries should appear at the top so the operator
    sees their most-recent question first."""
    src = _ask_ai_js()
    fn_idx = src.find("function _r68RenderHistory(")
    body_end = src.find("\n    }\n", fn_idx)
    body = src[fn_idx:body_end]
    assert "for (var i = entries.length - 1; i >= 0; i--)" in body, (
        "history not rendered newest-first"
    )


def test_r68_history_clear_all_button_wired() -> None:
    """The Clear all button must drop the localStorage entry AND
    re-render the panel (which then hides itself when empty)."""
    src = _ask_ai_js()
    assert "r68HistoryClearBtn.addEventListener" in src, (
        "Clear all button has no listener"
    )
    assert "removeItem(R68_HISTORY_KEY)" in src, (
        "Clear all doesn't drop the localStorage entry"
    )


def test_r68_history_copy_action_uses_clipboard_with_fallback() -> None:
    """Copy must use the modern Clipboard API when available with a
    fallback for insecure-context / older browsers."""
    src = _ask_ai_js()
    fn_idx = src.find("function _r68BuildHistoryRow(")
    body_end = src.find("\n    }\n", fn_idx)
    body = src[fn_idx:body_end]
    assert "navigator.clipboard" in body, "Copy missing Clipboard API"
    assert "execCommand('copy')" in body, (
        "Copy missing fallback for insecure-context / older browsers"
    )


def test_r68_history_records_scope_metadata() -> None:
    """Each entry must capture ``manager`` / ``technology`` / ``days``
    so the operator can see what scope produced the answer."""
    src = _ask_ai_js()
    fn_idx = src.find("function _r68RecordHistoryEntry(")
    body_end = src.find("\n    }\n", fn_idx)
    body = src[fn_idx:body_end]
    for fld in ("manager:", "technology:", "days:", "query_id:", "retrieval_method:"):
        assert fld in body, f"history entry missing {fld} field"


def test_r68_history_initial_render_on_page_load() -> None:
    """After DOMContentLoaded, the history must be rendered so a
    returning user sees their previous-session questions
    immediately.  C9 added code after the initial render call, so
    the gate now checks for the explicit "Initial render" comment
    immediately preceding the call - that comment is the contract
    between C8 and any future feature that wants to add code below."""
    src = _ask_ai_js()
    assert "Initial render" in src and "_r68RenderHistory();" in src, (
        "history not rendered on initial page load"
    )
    init_marker = src.find("Initial render")
    assert init_marker != -1
    snippet = src[init_marker : init_marker + 400]
    assert "_r68RenderHistory();" in snippet, (
        "expected _r68RenderHistory() call in the 'Initial render' block"
    )


# --- Smoke -------------------------------------------------------------------


def test_r68_module_imports_cleanly() -> None:
    import app_simple  # noqa: F401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
