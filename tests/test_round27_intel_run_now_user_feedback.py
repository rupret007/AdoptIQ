"""Round 27 - intel_status.js "Run now" user-feedback contract.

Pins the user-visible feedback wired up in static/js/intel_status.js so
the regression that left the button greying out for ~2 s with no other
signal cannot recur silently.

Static substring / regex assertions only -- no JS runtime is required.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import re
from pathlib import Path

import pytest

JS_PATH = Path(__file__).parent.parent / "static" / "js" / "intel_status.js"
JS_SRC = JS_PATH.read_text(encoding="utf-8")


def test_intel_run_now_has_set_refresh_feedback_helper() -> None:
    """The Round 27 fix introduces a single helper that writes
    user-visible feedback into [data-intel-banner-summary].  Pin both
    the function definition and its DOM target so a future refactor
    cannot silently route the feedback elsewhere (or drop it)."""
    assert "function setRefreshFeedback(" in JS_SRC, (
        "Round 27 regression: setRefreshFeedback helper missing -- the "
        "Run now click no longer surfaces user-visible feedback."
    )
    # The helper must target the banner summary element (the only
    # narrative text surface in the AdoptIQ Intelligence banner).
    assert "[data-intel-banner-summary]" in JS_SRC


def test_intel_run_now_pending_message_set_on_click() -> None:
    """As soon as the user clicks Run now the summary must show a
    'Refresh requested' line so the user sees their click was
    received, even before the server responds."""
    assert "'Refresh requested" in JS_SRC, (
        "Round 27 regression: no 'Refresh requested' feedback shown on "
        "click -- users see only the button greying out."
    )


def test_intel_run_now_success_message_present() -> None:
    """A successful refresh (refresh_started=true in the JSON body)
    must surface 'Refresh started; indexing' so the user knows the
    server accepted the request even when the new pass finishes
    within the 2 s debounce (zero new files)."""
    assert "'Refresh started" in JS_SRC, (
        "Round 27 regression: no 'Refresh started' feedback on success."
    )


def test_intel_run_now_refresh_not_started_message_present() -> None:
    """When the server returns 200 but refresh_started=false (e.g. the
    feature flag is off), the JSON body carries refresh_error.  The UI
    must surface that string so the user can self-diagnose instead of
    seeing a silent no-op."""
    assert "'Refresh not started: '" in JS_SRC
    assert "data.refresh_error" in JS_SRC, (
        "Round 27 regression: refresh_error field is no longer "
        "consumed by the UI."
    )


def test_intel_run_now_http_failure_message_present() -> None:
    """Non-2xx responses (e.g. 403 CSRF) must show 'Refresh failed:
    HTTP <status>'.  The earlier code silently dropped the body into
    paint(data), which classified missing fields as 'idle' and quietly
    rewrote the navbar badge."""
    assert "'Refresh failed: HTTP '" in JS_SRC


def test_intel_run_now_network_error_feedback_present() -> None:
    """fetch().catch must surface 'Refresh failed: network error'
    instead of the previous silent swallow."""
    assert "'Refresh failed: network error'" in JS_SRC


def test_intel_run_now_does_not_paint_on_non_2xx() -> None:
    """The non-2xx branch must NOT call paint(data) -- a 403 body of
    {ok:false,error:'CSRF validation failed'} has no enabled/boot
    keys, and classifyState falls through to 'idle', silently flipping
    the badge.  Verify the early-return idiom is preserved by reading
    the slice between the !result.ok guard and the next branch."""
    m = re.search(
        r"if \(!result\.ok\) \{(?P<body>.*?)\n\s*\}",
        JS_SRC,
        re.DOTALL,
    )
    assert m is not None, (
        "Round 27 regression: !result.ok guard is missing from "
        "intel_status.js."
    )
    body = m.group("body")
    assert "paint(" not in body, (
        "Round 27 regression: non-2xx response is being passed to "
        "paint(data) again -- a 403 will silently flip the badge."
    )
    # Must surface the failure to the user.
    assert_in_source(body, "setRefreshFeedback", label='body')
    # Must short-circuit so the success branch below doesn't run.
    assert_in_source(body, "return;", label='body')


def test_intel_run_now_immediate_poll_on_success() -> None:
    """The success branch (refresh_started=true) must call pollOnce()
    inline so the pill flips to 'Indexing' immediately rather than
    waiting the full 2 s debounce."""
    success_idx = JS_SRC.find("data.refresh_started === true")
    assert success_idx != -1, (
        "Round 27 regression: refresh_started boolean check missing."
    )
    # Look ahead a small window for the inline pollOnce call.
    success_block = JS_SRC[success_idx:success_idx + 1200]
    assert "pollOnce()" in success_block, (
        "Round 27 regression: success path no longer triggers an "
        "immediate pollOnce()."
    )


def test_intel_run_now_feedback_classes_use_classlist() -> None:
    """The state classes the helper applies must be set via
    classList (not via assigning to className) so we never clobber
    Bootstrap utility classes already on the summary element.  Also
    confirms textContent is the only sink for the message string,
    upholding codeguard-0-client-side-web-security."""
    assert "summary.classList.remove(" in JS_SRC
    assert "summary.classList.add(" in JS_SRC
    # The message itself must land in textContent, never innerHTML.
    assert "summary.textContent = " in JS_SRC
    assert ".innerHTML" not in JS_SRC


@pytest.mark.parametrize(
    "feedback_class",
    [
        "intel-refresh-pending",
        "intel-refresh-success",
        "intel-refresh-error",
    ],
)
def test_intel_run_now_feedback_state_class_present(feedback_class: str) -> None:
    """The three feedback state hooks must remain present so future
    CSS / accessibility work has stable selectors to target."""
    assert feedback_class in JS_SRC, (
        f"Round 27 regression: feedback state class '{feedback_class}' "
        f"is missing from intel_status.js."
    )
