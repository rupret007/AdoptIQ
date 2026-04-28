"""Round 39 / corpus crypto self-heal -- pin the analyze-page panel
"Reset corpus" button:

* The button stub MUST be rendered into ``analyze.html`` so the
  status poller has a target element to unhide.
* The button MUST be hidden by default so a healthy install never
  sees a destructive control.
* The visibility toggle in ``intel_status.js`` MUST be tied to
  ``boot.last_error_kind === 'crypto'`` so non-crypto error states
  (network failure, SQL drift, etc.) do not surface a button that
  cannot help them.
* The click handler MUST confirm() before POSTing so a careless
  click cannot wipe the corpus, and MUST send the CSRF token in the
  ``X-CSRFToken`` header.
* The handler MUST use ``addEventListener`` (no inline ``onclick``)
  per the codeguard-0-client-side-web-security CSP rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app_simple


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ANALYZE_HTML = _PROJECT_ROOT / "templates" / "analyze.html"
_INTEL_JS = _PROJECT_ROOT / "static" / "js" / "intel_status.js"


# ---------------------------------------------------------------------------
# Rendered template: button stub is present + hidden by default
# ---------------------------------------------------------------------------


def test_analyze_template_contains_reset_button_stub(client):
    """Render the analyze page via the Flask test client and assert
    the ``data-intel-reset`` button is present (so the JS poller has
    a target to unhide), but starts in the hidden state."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert "data-intel-reset" in body, (
        "analyze.html must render a [data-intel-reset] button stub "
        "so the status poller can unhide it on crypto errors"
    )

    # The stub must start hidden (the ``hidden`` attribute is
    # boolean -- presence is sufficient).  Match the stub as a
    # contiguous span so we are not fooled by an unrelated ``hidden``
    # attribute elsewhere on the page.
    idx = body.find("data-intel-reset")
    assert idx > 0
    snippet = body[idx : idx + 240]
    assert "hidden" in snippet, (
        "Reset corpus button must start hidden so healthy installs "
        "never expose a destructive control"
    )


def test_analyze_template_reset_button_label_and_class(client):
    """Source-shape pin: the button label, type, and Bootstrap class
    pattern stay stable so visual/CSS regressions are caught here."""
    resp = client.get("/")
    body = resp.get_data(as_text=True)
    idx = body.find("data-intel-reset")
    assert idx > 0
    # Span both directions: the ``type=`` and ``class=`` attributes
    # commonly precede the data-attribute on the same <button> tag.
    snippet = body[max(0, idx - 400) : idx + 400]
    # Must be a proper button (no anchor href that bots could follow).
    assert 'type="button"' in snippet, (
        "Reset corpus must be a <button type=\"button\">, not a link"
    )
    # Outline-warning conveys "destructive but recoverable" at a glance.
    assert "btn-outline-warning" in snippet, (
        "Reset corpus button must use btn-outline-warning to signal "
        "a destructive action"
    )
    # Label uses the agreed wording.
    assert "Reset corpus" in snippet, (
        "Reset corpus button label must read 'Reset corpus' (matches "
        "the user-facing terminology in the documentation)"
    )


# ---------------------------------------------------------------------------
# Source-shape pins on intel_status.js
# ---------------------------------------------------------------------------


def test_intel_status_js_visibility_tied_to_crypto_kind():
    """``paintResetButtonVisibility`` MUST gate visibility on
    ``last_error_kind === 'crypto'`` -- a regression that drops the
    kind check would surface the destructive button on every
    error state."""
    src = _INTEL_JS.read_text(encoding="utf-8")
    # The function must exist by name so test_intel_status_js_button_binder_present
    # can also key off it.
    assert "function paintResetButtonVisibility(" in src, (
        "intel_status.js must define paintResetButtonVisibility"
    )
    assert "last_error_kind" in src, (
        "intel_status.js must read boot.last_error_kind"
    )
    assert "kind === 'crypto'" in src or 'kind === "crypto"' in src, (
        "the visibility branch must be tied to last_error_kind === 'crypto' "
        "so non-crypto errors do not unhide the destructive button"
    )


def test_intel_status_js_uses_add_event_listener_not_inline_onclick():
    """CSP regression guard (codeguard-0-client-side-web-security):
    the reset button handler must be wired via addEventListener,
    NOT via an inline ``onclick=`` attribute that a strict CSP
    would block."""
    src = _INTEL_JS.read_text(encoding="utf-8")
    # Locate the bindResetButton function and assert addEventListener
    # is used inside its body.
    idx = src.find("function bindResetButton(")
    assert idx > 0, "intel_status.js must define bindResetButton"
    body = src[idx : idx + 3000]
    assert "addEventListener" in body, (
        "bindResetButton must use addEventListener -- inline onclick "
        "violates the strict CSP we ship with the .app"
    )
    # And there must be NO inline onclick= for the reset button
    # anywhere in the JS.
    assert "data-intel-reset" not in src or "onclick" not in src.split(
        "data-intel-reset"
    )[0], (
        "intel_status.js must not bind onclick= on the reset button"
    )


def test_intel_status_js_confirms_before_destructive_action():
    """The button click handler MUST prompt confirm() before POSTing
    so a careless click cannot wipe a working corpus.  Without this
    the user has no chance to cancel after a misclick."""
    src = _INTEL_JS.read_text(encoding="utf-8")
    idx = src.find("function bindResetButton(")
    assert idx > 0
    body = src[idx : idx + 3000]
    assert "window.confirm" in body or "confirm(" in body, (
        "bindResetButton must call confirm() before POSTing -- "
        "destructive actions need a click guard"
    )


def test_intel_status_js_posts_with_csrf_header():
    """The reset POST MUST send the page CSRF token in the
    X-CSRFToken header so the main app's auth gate accepts it.
    Pin the method/URL/header trio so a refactor cannot silently
    downgrade the request to a GET or strip the header."""
    src = _INTEL_JS.read_text(encoding="utf-8")
    idx = src.find("function bindResetButton(")
    assert idx > 0
    body = src[idx : idx + 3000]
    assert "method: 'POST'" in body or 'method: "POST"' in body, (
        "Reset POST must use method: 'POST'"
    )
    assert "RESET_URL" in body or "/api/intel/reset" in body, (
        "Reset POST must target /api/intel/reset (or the named constant)"
    )
    assert "X-CSRFToken" in body, (
        "Reset POST must include the X-CSRFToken header"
    )
    # And the URL constant itself must point at the user-facing alias
    # rather than the admin URL.
    assert "var RESET_URL = '/api/intel/reset'" in src or \
           'var RESET_URL = "/api/intel/reset"' in src, (
        "RESET_URL constant must point at /api/intel/reset"
    )
