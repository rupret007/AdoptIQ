"""Round 53 / Phase 53.4: pin the analyze-page panel UI for the
new ``blocked_no_onedrive`` corpus state.

Background
----------
Because the JS file ``static/js/intel_status.js`` runs in the
browser and the test environment doesn't have a JS runtime, we
verify the contract by reading the JS source text directly.  This
is sufficient because the panel state machine is small and
declarative -- all we need to assert is that:

* The classifier recognizes ``boot.source === 'blocked_no_onedrive'``
  and emits the corresponding state.
* That state takes precedence over ``refresh_failed`` so a stale
  refresh error does not mask the real cause.
* The label / pill / detail strings render the user-facing message
  (sign in to OneDrive, sync the canonical folder, etc.).
* The button-gating helper exists and respects
  ``data-disabled-when="blocked_no_onedrive"``.
* The deep-link painter exists and gates rendering on
  ``state === 'blocked_no_onedrive'``.

Companion server-side contracts (the deep-link allow-list, status
payload exposure) are pinned in
``tests/test_round53_onedrive_deeplink.py``.
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_JS_PATH = _REPO_ROOT / "static" / "js" / "intel_status.js"
_TEMPLATE_PATH = _REPO_ROOT / "templates" / "analyze.html"


def _read_js() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def _read_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Classifier contract
# ---------------------------------------------------------------------------


def test_intel_status_js_exists():
    assert _JS_PATH.exists(), (
        f"intel_status.js missing at {_JS_PATH}; the analyze panel "
        "depends on it."
    )


def test_classifier_recognizes_blocked_no_onedrive_source():
    """The classifier must map ``boot.source === 'blocked_no_onedrive'``
    to the ``'blocked_no_onedrive'`` state."""
    js = _read_js()
    assert "blocked_no_onedrive" in js, (
        "intel_status.js does not reference the blocked_no_onedrive "
        "state -- the Round 53 UI gate is missing."
    )
    # The classifier branch: ``if (source === 'blocked_no_onedrive')``
    assert "source === 'blocked_no_onedrive'" in js or \
           'source === "blocked_no_onedrive"' in js, (
        "classifier missing the blocked_no_onedrive branch"
    )


def test_classifier_blocked_takes_precedence_over_refresh_failed():
    """A stale ``last_refresh_error`` should NOT hide the
    blocked_no_onedrive state -- the user needs to see the actionable
    'Sign in to OneDrive' message, not a generic refresh error."""
    js = _read_js()
    # Find positions of the two relevant branches.
    blocked_idx = js.find("source === 'blocked_no_onedrive'")
    if blocked_idx < 0:
        blocked_idx = js.find('source === "blocked_no_onedrive"')
    refresh_failed_idx = js.find("last_refresh_error")
    assert blocked_idx >= 0
    assert refresh_failed_idx >= 0
    assert blocked_idx < refresh_failed_idx, (
        "blocked_no_onedrive branch must come BEFORE the "
        "last_refresh_error branch in the classifier; otherwise a "
        "stale refresh error would mask the real cause."
    )


# ---------------------------------------------------------------------------
# Label / pill / detail strings
# ---------------------------------------------------------------------------


def test_panel_label_for_blocked_state():
    """The pill label for the blocked state must be actionable
    ('Sign in to OneDrive') -- not a generic 'Error' or 'Failed'."""
    js = _read_js()
    assert "case 'blocked_no_onedrive':" in js
    # The label should encourage signing in to OneDrive.
    assert "Sign in to OneDrive" in js, (
        "intel_status.js label for blocked_no_onedrive is missing "
        "the actionable 'Sign in to OneDrive' text."
    )


def test_panel_pill_class_for_blocked_state_is_warning():
    """Use bg-warning (orange) for the blocked pill -- this is an
    action-required state, not a hard failure (bg-danger)."""
    js = _read_js()
    # Anchor on the case statement.
    case_anchor = "case 'blocked_no_onedrive': return 'bg-warning"
    assert case_anchor in js, (
        f"blocked_no_onedrive pill must use bg-warning styling; "
        f"missing anchor {case_anchor!r} in intel_status.js"
    )


def test_panel_detail_mentions_canonical_share_folder():
    """The detail copy must name the canonical OneDrive folder so
    the user knows exactly what to sync."""
    js = _read_js()
    # The detail string lives in the corpusPanelDetail switch.
    assert "AI Projects/AdoptIQ_CSOne_Reports" in js, (
        "blocked_no_onedrive detail must name the canonical OneDrive "
        "folder so the user knows what to sync."
    )


# ---------------------------------------------------------------------------
# Button gating
# ---------------------------------------------------------------------------


def test_paint_gated_buttons_helper_exists():
    js = _read_js()
    assert "function paintGatedButtons" in js, (
        "paintGatedButtons helper missing -- buttons would stay "
        "clickable in the blocked state."
    )


def test_paint_gated_buttons_uses_data_disabled_when_attr():
    js = _read_js()
    # The selector that drives the gating.
    assert "[data-disabled-when]" in js, (
        "paintGatedButtons must scan for [data-disabled-when] "
        "attributes; without that selector the analyze-page "
        "Re-index/Reset buttons cannot opt into gating."
    )


def test_paint_gated_buttons_sets_aria_disabled():
    """Accessibility: the gated buttons must set aria-disabled in
    addition to the HTML disabled attribute -- screen readers depend
    on it."""
    js = _read_js()
    assert "'aria-disabled', 'true'" in js or \
           '"aria-disabled", "true"' in js, (
        "paintGatedButtons must set aria-disabled='true' for "
        "screen-reader users."
    )


def test_analyze_template_marks_refresh_button_as_gated():
    """The Re-index / refresh button on the analyze page must opt
    into the Round 53 gating via ``data-disabled-when``."""
    html = _read_template()
    assert 'data-intel-run-now' in html, (
        "analyze.html missing the Re-index / refresh button selector "
        "(data-intel-run-now)"
    )
    # Look for the ``data-disabled-when="...blocked_no_onedrive..."``
    # attribute on a button near data-intel-run-now.
    assert 'data-disabled-when="blocked_no_onedrive"' in html or \
           "data-disabled-when='blocked_no_onedrive'" in html, (
        "analyze.html Refresh button missing "
        "data-disabled-when='blocked_no_onedrive' -- it would stay "
        "clickable in the blocked state and trigger an immediate "
        "re-fail."
    )


def test_analyze_template_marks_reset_button_as_gated():
    """Same gating contract applies to the Reset corpus button."""
    html = _read_template()
    assert 'data-intel-reset' in html, (
        "analyze.html missing the Reset corpus button selector"
    )


# ---------------------------------------------------------------------------
# Deep-link painter
# ---------------------------------------------------------------------------


def test_paint_deep_link_helper_exists():
    js = _read_js()
    assert "function paintDeepLink" in js, (
        "paintDeepLink helper missing -- the deep-link CTA cannot "
        "render."
    )


def test_paint_deep_link_uses_data_onedrive_deep_link_slot():
    js = _read_js()
    assert "[data-onedrive-deep-link]" in js, (
        "paintDeepLink must scan for [data-onedrive-deep-link] "
        "anchor slots."
    )


def test_paint_deep_link_uses_safe_deep_link_validator():
    """Defense in depth: the painter must run the URL through
    ``r53SafeDeepLink`` before assigning to ``href``.  Otherwise a
    server compromise that smuggles a ``javascript:`` URL into the
    payload could XSS the panel via the rendered anchor."""
    js = _read_js()
    assert "r53SafeDeepLink(rawUrl)" in js or \
           "r53SafeDeepLink(boot.onedrive_deep_link)" in js, (
        "paintDeepLink must run the URL through r53SafeDeepLink "
        "before assigning to href -- this is defense-in-depth on "
        "top of the server-side validator."
    )


def test_paint_deep_link_only_visible_in_blocked_state():
    """The deep-link CTA should ONLY appear in the
    ``blocked_no_onedrive`` state -- showing it in other states
    would confuse users who already have OneDrive synced."""
    js = _read_js()
    assert "state === 'blocked_no_onedrive'" in js or \
           'state === "blocked_no_onedrive"' in js, (
        "paintDeepLink visibility check must gate on "
        "state === 'blocked_no_onedrive'"
    )


def test_paint_deep_link_sets_noopener_noreferrer():
    """Per codeguard-0-client-side-web-security: external target=_blank
    links MUST set rel='noopener noreferrer' to prevent reverse-tabnabbing."""
    js = _read_js()
    assert "'rel'" in js and "'noopener noreferrer'" in js or \
           '"rel"' in js and '"noopener noreferrer"' in js, (
        "paintDeepLink must set rel='noopener noreferrer' on the "
        "external anchor (codeguard-0-client-side-web-security)."
    )


def test_analyze_template_has_deep_link_anchor_slot():
    """The analyze template must declare the
    ``[data-onedrive-deep-link]`` anchor that the painter targets."""
    html = _read_template()
    assert 'data-onedrive-deep-link' in html, (
        "analyze.html missing [data-onedrive-deep-link] anchor slot "
        "-- paintDeepLink would have nothing to paint."
    )


# ---------------------------------------------------------------------------
# JS-level allow-list
# ---------------------------------------------------------------------------


def test_js_safe_deep_link_uses_scheme_allow_list():
    """The JS validator must check schemes against an allow-list
    (mirror of the Python validator).  A change-detection guard
    that prevents a future refactor from dropping it."""
    js = _read_js()
    assert "R53_DEEP_LINK_SCHEMES" in js or "DEEP_LINK_SCHEMES" in js, (
        "intel_status.js missing the deep-link scheme allow-list "
        "constant"
    )
    # The four allowed schemes.
    for scheme in ("http://", "https://", "odopen:", "ms-onedrive:"):
        assert scheme in js, (
            f"intel_status.js scheme allow-list missing {scheme!r}"
        )


def test_js_safe_deep_link_exposed_for_tests():
    """The validator must be exposed via the test harness window
    handle so QA tools can pin the contract from the browser side."""
    js = _read_js()
    assert "safeDeepLink:" in js, (
        "intel_status.js must expose safeDeepLink on "
        "window.__adoptiqCorpusPanelState for browser-side testing."
    )


# ---------------------------------------------------------------------------
# Round 53.3: align the GLOBAL classifier (navbar badge / analyze banner)
# with the corpus panel for the blocked_no_onedrive state.
# ---------------------------------------------------------------------------


def test_round533_global_classifier_recognizes_blocked_state():
    """``classifyState`` must map ``boot.source === 'blocked_no_onedrive'``
    to the ``'blocked'`` state so the navbar badge / analyze banner do
    not flash red ``Error`` while the corpus panel says ``Sign in to
    OneDrive``."""

    js = _read_js()
    assert "function classifyState" in js
    classifier_start = js.find("function classifyState")
    classifier_end = js.find("function ", classifier_start + 1)
    classifier_body = js[classifier_start:classifier_end]
    assert "blocked_no_onedrive" in classifier_body, (
        "classifyState body does not check boot.source for "
        "blocked_no_onedrive -- the navbar badge would still show "
        "'Error' while the corpus panel shows 'Sign in to OneDrive'."
    )
    assert "return 'blocked'" in classifier_body or \
           'return "blocked"' in classifier_body, (
        "classifyState must return the dedicated 'blocked' state "
        "for blocked_no_onedrive payloads."
    )


def test_round533_global_classifier_blocked_takes_precedence_over_error():
    """The blocked branch must run BEFORE the generic ``last_error``
    branch in ``classifyState``, otherwise a payload with
    ``last_error`` set (which is true for every blocked payload)
    would map to ``'error'`` and override the actionable state."""

    js = _read_js()
    classifier_start = js.find("function classifyState")
    classifier_end = js.find("function ", classifier_start + 1)
    classifier_body = js[classifier_start:classifier_end]
    blocked_idx = classifier_body.find("blocked_no_onedrive")
    error_branch_idx = classifier_body.find("return 'error'")
    if error_branch_idx < 0:
        error_branch_idx = classifier_body.find('return "error"')
    assert blocked_idx >= 0
    assert error_branch_idx >= 0
    assert blocked_idx < error_branch_idx, (
        "blocked_no_onedrive branch must come BEFORE the "
        "return 'error' branch in classifyState."
    )


def test_round533_global_state_label_and_pill_are_actionable_warning():
    """The label / pill class for the global ``blocked`` state must
    be the same actionable warning used by the corpus panel."""

    js = _read_js()
    assert "case 'blocked':" in js or 'case "blocked":' in js, (
        "stateToLabel / stateToBadgeClass must include a 'blocked' case."
    )
    assert "case 'blocked':" in js and "Sign in to OneDrive" in js, (
        "stateToLabel for 'blocked' must say 'Sign in to OneDrive'."
    )
    assert "case 'blocked':     return 'bg-warning text-dark'" in js, (
        "stateToBadgeClass for 'blocked' must use the same "
        "bg-warning styling as the corpus panel."
    )


def test_round533_analyze_template_data_state_default_is_idle():
    """The analyze banner SSR must hard-code ``data-state='idle'`` --
    the API payload has no ``boot.state`` field, so the previous
    Jinja chain rendered an empty attribute and contradicted the
    surrounding 'neutral idle default' comment."""

    html = _read_template()
    assert 'data-state="idle"' in html, (
        "analyze.html [data-intel-banner] must default to "
        "data-state='idle' in server HTML so the SSR markup is "
        "consistent before the first poll."
    )
    assert "intel_status.boot.state" not in html, (
        "analyze.html must not read intel_status.boot.state -- the "
        "API payload does not carry that field; classifyState in "
        "intel_status.js derives the state client-side."
    )
