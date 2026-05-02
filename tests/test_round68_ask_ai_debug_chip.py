"""Round 68 / Build 42 (C5): pin the Ask AI debug chip.

The chip surfaces:
  * ``query_id`` -- so an operator filing a support ticket can paste
    the full ID and we can pull the persisted retrieval diag from
    ``/api/ask-ai/diagnostics/<id>`` (which now persists to SQLite
    via R68/C2).
  * ``retrieval_method`` -- so the operator can see at a glance which
    retrieval path served their answer (vector / lexical / hybrid /
    legacy_ungrounded / unknown).
  * Copy-to-clipboard button -- so the long token-urlsafe id is
    one-click copyable.

Pre-R68 the operator could only screenshot the answer; we had no
way to correlate to the server-side diag.  These pins guarantee the
chip is wired through both grounded and legacy_ungrounded paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parent.parent
_ASK_AI_JS = _ROOT / "static" / "js" / "ask_ai.js"
_ASK_AI_HTML = _ROOT / "templates" / "ask_ai.html"
_APP_SIMPLE = _ROOT / "app_simple.py"


def _ask_ai_js() -> str:
    return _ASK_AI_JS.read_text(encoding="utf-8")


def _ask_ai_html() -> str:
    return _ASK_AI_HTML.read_text(encoding="utf-8")


def _app_simple() -> str:
    return _APP_SIMPLE.read_text(encoding="utf-8")


# --- Template wiring ---------------------------------------------------------


def test_r68_debug_chip_div_present_in_template() -> None:
    """``id="r68DebugChip"`` div + the three children (method
    badge, id badge, copy button) must all be present in the
    template so the JS can populate them."""
    html = _ask_ai_html()
    assert 'id="r68DebugChip"' in html, "debug chip wrapper missing"
    assert 'id="r68DebugChipMethod"' in html, "method badge missing"
    assert 'id="r68DebugChipId"' in html, "id badge missing"
    assert 'id="r68DebugChipCopyBtn"' in html, "copy button missing"


def test_r68_debug_chip_hidden_by_default() -> None:
    """The chip must be ``display:none`` by default so it doesn't
    flash empty content during the loading spinner."""
    html = _ask_ai_html()
    chip_idx = html.find('id="r68DebugChip"')
    assert chip_idx != -1
    snippet = html[chip_idx : chip_idx + 200]
    assert 'style="display:none' in snippet, (
        "debug chip not hidden by default -- empty chip would flash on load"
    )


# --- JS wiring ---------------------------------------------------------------


def test_r68_render_debug_chip_function_present() -> None:
    src = _ask_ai_js()
    assert "function _r68RenderDebugChip(" in src, "_r68RenderDebugChip missing"


def test_r68_render_debug_chip_uses_textContent_not_innerHTML() -> None:
    """XSS guard -- the chip text comes from server data and MUST
    use textContent, never innerHTML."""
    src = _ask_ai_js()
    body_start = src.find("function _r68RenderDebugChip(")
    assert body_start != -1
    # Find the end of this function (matching closing brace at column 4).
    body_end = src.find("\n    }\n", body_start)
    assert body_end != -1
    body = src[body_start:body_end]
    assert "innerHTML" not in body, "_r68RenderDebugChip uses innerHTML (XSS sink)"
    assert ".textContent" in body, "_r68RenderDebugChip must use textContent"


def test_r68_render_debug_chip_called_on_success() -> None:
    """The chip must be rendered in the success branch of the fetch
    handler so it appears the moment the answer lands."""
    src = _ask_ai_js()
    # The call site lives inside ``if (data.ok) {`` block.
    success_idx = src.find("if (data.ok) {")
    assert success_idx != -1
    # Look forward 1000 chars for the call.
    snippet = src[success_idx : success_idx + 1500]
    assert "_r68RenderDebugChip(data)" in snippet, (
        "_r68RenderDebugChip not called from success branch -- chip won't"
        " appear after a successful answer"
    )


def test_r68_debug_chip_hidden_on_new_question() -> None:
    """When a new question is asked, the previous chip must hide so
    a stale debug ID doesn't show during the new spinner."""
    src = _ask_ai_js()
    # The hide call lives inside hideAllBanners.
    helper_idx = src.find("function hideAllBanners(")
    assert helper_idx != -1
    body_end = src.find("\n    }\n", helper_idx)
    body = src[helper_idx:body_end]
    assert "r68DebugChip.style.display = 'none'" in body, (
        "debug chip not hidden on new question -- stale id would flash"
    )


def test_r68_copy_button_listener_wired() -> None:
    """The Copy button must have an addEventListener('click', ...)
    so the operator can one-click copy the ID."""
    src = _ask_ai_js()
    # Locate the listener registration.
    assert "r68DebugChipCopyBtn.addEventListener" in src, (
        "Copy button has no click listener -- the chip's main affordance is dead"
    )


def test_r68_copy_uses_clipboard_api_with_fallback() -> None:
    """The copy logic must use the modern ``navigator.clipboard`` API
    when available AND fall back to ``execCommand('copy')`` for
    insecure-context / older browsers.  Without the fallback, the
    copy button silently fails on insecure connections."""
    src = _ask_ai_js()
    assert "navigator.clipboard" in src, "no Clipboard API path"
    assert "execCommand('copy')" in src, (
        "no execCommand fallback -- copy button broken on insecure contexts"
    )


def test_r68_copy_full_id_not_truncated_display() -> None:
    """The copy button must put the FULL ``r68LastQueryId`` on the
    clipboard, not the truncated display value (which we elide for
    chrome reasons)."""
    src = _ask_ai_js()
    # The handler must read r68LastQueryId, not the displayed text.
    handler_idx = src.find("r68DebugChipCopyBtn.addEventListener")
    assert handler_idx != -1
    snippet = src[handler_idx : handler_idx + 1200]
    assert "r68LastQueryId" in snippet, (
        "copy handler doesn't read the full id -- copies the truncated display value"
    )


# --- Server-side payload -----------------------------------------------------


def test_r68_grounded_path_returns_query_id_and_method() -> None:
    """The grounded portfolio Ask AI path must include ``query_id``
    and ``retrieval_method`` in its success response."""
    src = _app_simple()
    # The grounded success branch lives in /api/ask-ai-portfolio.
    grounded_idx = src.find("'corpus': grounded_result.get('corpus')")
    assert grounded_idx != -1, "grounded success branch not found"
    snippet = src[grounded_idx : grounded_idx + 600]
    assert "'query_id'" in snippet, "grounded path missing query_id"
    assert "'retrieval_method'" in snippet, "grounded path missing retrieval_method"


def test_r68_legacy_ungrounded_path_returns_query_id_and_method() -> None:
    """The legacy ungrounded fallback path must ALSO publish
    ``query_id`` + ``retrieval_method='legacy_ungrounded'`` so the
    chip works after the operator opts into the fallback."""
    src = _app_simple()
    # The legacy success branch.
    legacy_idx = src.find("'mode': 'legacy_ungrounded'")
    assert legacy_idx != -1, "legacy_ungrounded branch not found"
    snippet = src[legacy_idx : legacy_idx + 800]
    assert "'query_id'" in snippet, "legacy path missing query_id"
    assert "'retrieval_method': 'legacy_ungrounded'" in snippet, (
        "legacy path missing retrieval_method='legacy_ungrounded' -- the chip"
        " would show 'unknown' after a fallback"
    )


def test_r68_legacy_ungrounded_records_diag() -> None:
    """The legacy path's query_id must be recorded via
    ``_record_ask_ai_query_diag`` so a follow-up
    ``/api/ask-ai/diagnostics/<id>`` request returns 200 not 404."""
    src = _app_simple()
    legacy_idx = src.find("'mode': 'legacy_ungrounded'")
    legacy_end = src.find("'mode': 'legacy_ungrounded'", legacy_idx + 1)
    # Search the surrounding region (the recording call lives just
    # above the jsonify(...)).
    region = src[max(0, legacy_idx - 800) : legacy_idx]
    assert "_record_ask_ai_query_diag(_legacy_query_id" in region, (
        "legacy_ungrounded query_id never persisted -- /diagnostics endpoint"
        " would always 404"
    )


# --- Smoke -------------------------------------------------------------------


def test_r68_module_imports_cleanly() -> None:
    import app_simple  # noqa: F401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
