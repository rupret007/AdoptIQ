"""Round 68 / Build 42 (C9): pin the loading polish features.

Pre-R68 the Ask AI page showed a single rolling spinner with a
rotating one-line caption.  Operators reported:

  * "It feels stuck after 30 seconds" (no progress signal beyond
    the rolling caption).
  * No way to cancel a slow request -- they had to wait the
    full 90s timeout or close the tab.
  * First-time users had no orientation when they landed on the
    empty Ask AI page (no guidance on what to ask).

R68 adds:
  * 3-step progress indicator (fetch -> retrieve -> synthesize)
    that mirrors the canonical pipeline; each step transitions
    pending -> active -> done as the request progresses.
  * Cancel button inside the loading state that aborts the
    in-flight AbortController.
  * First-time empty-state tip card outside the answer area;
    dismissed permanently via localStorage flag.
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


# --- 3-step progress indicator -----------------------------------------------


def test_r68_three_step_indicator_present_in_template() -> None:
    """The three step list-items must be present so the JS has
    something to update."""
    html = _ask_ai_html()
    for step_id in ("r68StepFetch", "r68StepRetrieve", "r68StepSynthesize"):
        assert f'id="{step_id}"' in html, f"step indicator missing: {step_id}"


def test_r68_three_step_indicator_uses_progressbar_role() -> None:
    """The container must carry the WAI-ARIA progressbar role so
    screen readers announce the progression."""
    html = _ask_ai_html()
    container_idx = html.find('id="r68LoadingSteps"')
    assert container_idx != -1
    snippet = html[container_idx : container_idx + 400]
    assert 'role="progressbar"' in snippet, (
        "3-step indicator missing progressbar role -- a11y regression"
    )
    assert 'aria-valuemin="0"' in snippet
    assert 'aria-valuemax="3"' in snippet


def test_r68_set_step_function_present() -> None:
    src = _ask_ai_js()
    assert "function _r68SetStep(" in src, "_r68SetStep helper missing"


def test_r68_set_step_called_on_request_start() -> None:
    """The step indicator must transition to step 1 immediately
    when the fetch fires, then advance via setTimeout."""
    src = _ask_ai_js()
    assert "_r68SetStep(1)" in src, "step indicator never starts"
    assert "_r68SetStep(2)" in src, "step indicator never advances to retrieve"
    assert "_r68SetStep(3)" in src, "step indicator never advances to synthesize"


def test_r68_set_step_advances_via_setTimeout() -> None:
    """Steps 2 and 3 must advance via setTimeout so the operator
    sees the progression even when the network is slow."""
    src = _ask_ai_js()
    fn_idx = src.find("_r68SetStep(1)")
    assert fn_idx != -1
    snippet = src[fn_idx : fn_idx + 600]
    assert "setTimeout" in snippet, (
        "step indicator doesn't advance via setTimeout -- regresses to single-step"
    )


def test_r68_set_step_updates_aria_valuenow() -> None:
    """Each step transition must update aria-valuenow so screen
    readers announce the new step."""
    src = _ask_ai_js()
    assert "setAttribute('aria-valuenow'" in src, (
        "step indicator doesn't update aria-valuenow -- a11y regression"
    )


# --- Cancel button -----------------------------------------------------------


def test_r68_cancel_button_present_in_template() -> None:
    html = _ask_ai_html()
    assert 'id="r68CancelBtn"' in html, "Cancel button missing"


def test_r68_cancel_button_aborts_active_controller() -> None:
    """Click handler must call .abort() on the active controller
    AND show a toast confirming the cancellation."""
    src = _ask_ai_js()
    handler_idx = src.find("r68CancelBtn.addEventListener")
    assert handler_idx != -1
    body = src[handler_idx : handler_idx + 800]
    assert "ctl.abort()" in body, "Cancel button doesn't abort the controller"
    assert "_r68ShowToast('Request cancelled" in body, (
        "Cancel button doesn't show confirmation toast"
    )


def test_r68_cancel_button_handles_no_active_request() -> None:
    """When there's no active request, click must show a 'no
    request to cancel' toast rather than crashing."""
    src = _ask_ai_js()
    handler_idx = src.find("r68CancelBtn.addEventListener")
    body = src[handler_idx : handler_idx + 800]
    assert "No in-flight request to cancel" in body, (
        "Cancel button has no graceful handling for no-active-request case"
    )


def test_r68_active_abort_controller_registered_on_request() -> None:
    """``askAI`` must register its AbortController on the
    namespaced runtime slot so the Cancel button can find it."""
    src = _ask_ai_js()
    assert "window._R68_ASK_AI_RUNTIME.activeAbort = _abortCtl" in src, (
        "AbortController not registered on the namespaced runtime slot"
    )


def test_r68_active_abort_controller_cleared_on_completion() -> None:
    """The slot must be cleared in ``.finally`` so a completed
    request's controller doesn't get aborted by a later click."""
    src = _ask_ai_js()
    assert "window._R68_ASK_AI_RUNTIME.activeAbort = null" in src, (
        "AbortController slot not cleared on completion -- stale-controller risk"
    )


# --- Empty-state tip ---------------------------------------------------------


def test_r68_empty_state_tip_present_in_template() -> None:
    html = _ask_ai_html()
    assert 'id="r68EmptyStateTip"' in html, "empty-state tip card missing"
    assert 'id="r68EmptyStateTipDismissBtn"' in html, "tip dismiss button missing"


def test_r68_empty_state_tip_outside_answer_area() -> None:
    """The tip must live OUTSIDE the answerArea div so it shows
    on first page load (before any question)."""
    html = _ask_ai_html()
    tip_idx = html.find('id="r68EmptyStateTip"')
    answer_area_idx = html.find('id="answerArea"')
    assert tip_idx != -1 and answer_area_idx != -1
    assert tip_idx < answer_area_idx, (
        "empty-state tip rendered inside answerArea -- won't show before first answer"
    )


def test_r68_empty_state_tip_dismissed_via_localStorage_flag() -> None:
    """Once dismissed, the tip must NOT reappear on a future page
    load.  The flag lives in localStorage."""
    src = _ask_ai_js()
    assert "R68_TIP_DISMISSED_KEY = 'r68_ask_ai_tip_dismissed'" in src, (
        "tip-dismissed key missing or not namespaced"
    )
    assert "function _r68ShouldShowTip(" in src, "tip-dismissed gate missing"
    assert "function _r68DismissTip(" in src, "tip-dismiss helper missing"


def test_r68_empty_state_tip_dismiss_handles_localstorage_failure() -> None:
    """If localStorage is unavailable (e.g. private mode in some
    browsers), the dismiss must not crash."""
    src = _ask_ai_js()
    fn_idx = src.find("function _r68DismissTip(")
    body_end = src.find("\n    }\n", fn_idx)
    body = src[fn_idx:body_end]
    assert "try {" in body and "catch" in body, (
        "_r68DismissTip has no try/catch -- crashes when localStorage is disabled"
    )


def test_r68_empty_state_tip_dismiss_button_wired() -> None:
    src = _ask_ai_js()
    assert "r68EmptyStateTipDismissBtn.addEventListener('click', _r68DismissTip)" in src, (
        "Dismiss button click handler not wired to _r68DismissTip"
    )


# --- Smoke -------------------------------------------------------------------


def test_r68_module_imports_cleanly() -> None:
    import app_simple  # noqa: F401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
