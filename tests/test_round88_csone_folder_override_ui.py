"""Round 88 / Build 64 -- CSOne OneDrive folder override UI source-shape pins.

Mirrors ``tests/test_round84_corpus_share_url_ui_source_shape.py``.  These
are static source-shape assertions on the HTML template + JS module --
the behaviour itself is covered by
``tests/test_round88_csone_folder_override.py`` (validator + endpoint).

The contract being pinned:

  1. ``static/js/r88_csone_folder_override.js`` is an IIFE-wrapped
     module (no globals leaked, except the explicit
     ``window.AdoptIQCsoneFolderOverride`` test export) that targets
     ``[data-csone-folder-*]`` markers.
  2. The paint helper renders all server-echoed values via
     ``textContent`` -- never ``innerHTML``, ``eval``,
     ``setAttribute('on*', ...)``, or any other dangerous DOM sink.
  3. ``templates/preferences.html`` carries the matching card with
     the canonical ``[data-csone-folder-*]`` markers and includes the
     JS module via ``url_for('static', filename='js/r88_csone_folder_override.js')``.
  4. The Save endpoint URL pinned in the JS module agrees with the
     ``api_settings_csone_onedrive_folder`` route registered in
     ``app_simple.py``.

These assertions are deliberately structural (not behavioural) so they
catch refactors that silently alter the security posture (e.g. a future
contributor swapping ``textContent`` for ``innerHTML`` to render a
Markdown-formatted error message).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_JS_PATH = _REPO_ROOT / "static" / "js" / "r88_csone_folder_override.js"
_PREFS_PATH = _REPO_ROOT / "templates" / "preferences.html"


# Round 88 / Build 64
def test_r88_js_module_exists() -> None:
    """Pin the canonical filename so a future move breaks the test
    instead of silently dropping the UI."""

    assert _JS_PATH.is_file(), (
        f"Round 88 / F5: expected JS module at {_JS_PATH}; was the "
        "card's JS module renamed or deleted?"
    )


# Round 88 / Build 64
def test_r88_js_module_uses_iife_wrapper() -> None:
    """The module MUST be IIFE-wrapped so it does not leak helpers
    (``getCsrfToken``, ``setFeedback``, etc.) into the global
    namespace where a future XSS could hijack them.
    """

    src = _JS_PATH.read_text(encoding="utf-8")
    assert re.search(r"\(function\s*\(\)\s*\{", src), (
        "Round 88 / F5: r88_csone_folder_override.js must be wrapped in "
        "``(function () { ... })();`` -- IIFE prevents helper leakage."
    )
    assert re.search(r"\}\)\(\);?\s*$", src.strip()), (
        "Round 88 / F5: r88_csone_folder_override.js IIFE must be invoked "
        "at the bottom of the file (``})();``)."
    )
    assert "'use strict';" in src, (
        "Round 88 / F5: ``'use strict';`` MUST be the first statement "
        "inside the IIFE so a typo in a variable name throws instead of "
        "silently creating a global."
    )


# Round 88 / Build 64
def test_r88_js_module_renders_via_text_content_only() -> None:
    """Every server-echoed value (active_path, source label, persisted_value,
    error message) MUST land in the DOM via ``textContent``, never
    ``innerHTML`` or any sink that could parse HTML.
    """

    src = _JS_PATH.read_text(encoding="utf-8")

    # ``textContent`` MUST be the rendering primitive.
    assert ".textContent" in src, (
        "Round 88 / F5: r88_csone_folder_override.js must use ``.textContent`` "
        "to render server-echoed values -- canonical XSS-safe sink."
    )


# Round 88 / Build 64
def test_r88_js_module_does_not_use_dangerous_dom_sinks() -> None:
    """Negative control: the module MUST NOT use any HTML-parsing or
    code-evaluating sink.  This catches a future contributor who
    swaps ``textContent`` for ``innerHTML`` to render a Markdown
    formatted error message (the Round 84 / B5 lesson learned).
    """

    src = _JS_PATH.read_text(encoding="utf-8")

    forbidden = [
        ".innerHTML",
        ".outerHTML",
        "document.write",
        "insertAdjacentHTML",
        "eval(",
        "new Function(",
        # Round 76 lesson: attribute-style event handlers are an
        # injection vector even when set via setAttribute.
        "setAttribute('onclick'",
        'setAttribute("onclick"',
    ]
    for sink in forbidden:
        assert sink not in src, (
            f"Round 88 / F5: r88_csone_folder_override.js must not use "
            f"``{sink}`` -- bypasses the textContent XSS guard."
        )


# Round 88 / Build 64
def test_r88_js_module_pins_endpoint_url() -> None:
    """The Save endpoint URL must agree with the Flask route in
    ``app_simple.py`` (canonical: ``/api/settings/csone-onedrive-folder``).
    A drift here would land an honest 404 in production but pre-deploy
    the test suite stays green -- pinning here keeps the contract
    visible to the reviewer.
    """

    src = _JS_PATH.read_text(encoding="utf-8")
    assert "/api/settings/csone-onedrive-folder" in src, (
        "Round 88 / F5: r88_csone_folder_override.js must POST to "
        "``/api/settings/csone-onedrive-folder`` -- pinned by "
        "``app_simple.py:api_settings_csone_onedrive_folder``."
    )


# Round 88 / Build 64
def test_r88_js_module_uses_csrf_header_for_post() -> None:
    """Save / Clear are POST mutations -- the request MUST carry the
    CSRF token via ``X-CSRFToken`` (Flask-WTF canonical) AND
    ``X-CSRF-Token`` (legacy alias preserved by R69 model preferences
    for symmetric clients).  A future contributor dropping the header
    would silently fail in production but the test would catch it.
    """

    src = _JS_PATH.read_text(encoding="utf-8")
    assert "'X-CSRFToken':" in src or '"X-CSRFToken":' in src, (
        "Round 88 / F5: POST handler must send the ``X-CSRFToken`` header."
    )
    assert "'X-CSRF-Token':" in src or '"X-CSRF-Token":' in src, (
        "Round 88 / F5: POST handler must also send the legacy "
        "``X-CSRF-Token`` header (R69 parity)."
    )


# Round 88 / Build 64
def test_r88_js_module_exposes_test_hooks() -> None:
    """``window.AdoptIQCsoneFolderOverride`` MUST expose
    ``bindCsoneFolderSave`` + ``paintCsoneFolderCard`` +
    ``fetchCsoneFolderCard`` so future composition tests can call
    the helpers directly without re-binding handlers.
    """

    src = _JS_PATH.read_text(encoding="utf-8")
    assert "window.AdoptIQCsoneFolderOverride" in src, (
        "Round 88 / F5: must export ``window.AdoptIQCsoneFolderOverride`` "
        "for test composition."
    )
    for hook in (
        "bindCsoneFolderSave",
        "paintCsoneFolderCard",
        "fetchCsoneFolderCard",
    ):
        assert hook in src, (
            f"Round 88 / F5: window.AdoptIQCsoneFolderOverride must expose "
            f"``{hook}`` -- missing from the IIFE export."
        )


# Round 88 / Build 64
def test_r88_preferences_template_contains_card() -> None:
    """The Preferences template MUST contain the ``[data-csone-folder-card]``
    section so the JS module finds something to bind to.  Modeled on
    the corpus-share-url card.
    """

    src = _PREFS_PATH.read_text(encoding="utf-8")
    assert "data-csone-folder-card" in src, (
        "Round 88 / F5: templates/preferences.html must declare the "
        "``[data-csone-folder-card]`` section."
    )
    # Pin every marker the JS module looks for.  Drift here would
    # produce a card the JS cannot bind to.
    for marker in (
        "data-csone-folder-input",
        "data-csone-folder-save",
        "data-csone-folder-clear",
        "data-csone-folder-current",
        "data-csone-folder-persisted",
        "data-csone-folder-exists",
        "data-csone-folder-source-pill",
        "data-csone-folder-source-text",
        "data-csone-folder-feedback",
    ):
        assert marker in src, (
            f"Round 88 / F5: templates/preferences.html must declare "
            f"``{marker}`` -- the JS module's selector targets it."
        )


# Round 88 / Build 64
def test_r88_preferences_template_includes_js_module() -> None:
    """The Preferences template MUST include the JS module via
    ``url_for('static', filename='js/r88_csone_folder_override.js')``.
    A bare relative ``src`` would break in PyInstaller frozen builds
    where ``static/`` is mounted at a different prefix.
    """

    src = _PREFS_PATH.read_text(encoding="utf-8")
    assert "js/r88_csone_folder_override.js" in src, (
        "Round 88 / F5: templates/preferences.html must include the "
        "``js/r88_csone_folder_override.js`` module via url_for()."
    )
    assert re.search(
        r"url_for\(['\"]static['\"],\s*filename=['\"]js/r88_csone_folder_override\.js['\"]\)",
        src,
    ), (
        "Round 88 / F5: the JS module include must use Flask's "
        "url_for() helper -- bare relative paths break in frozen builds."
    )


# Round 88 / Build 64
def test_r88_preferences_template_input_has_4096_max_length() -> None:
    """Defense-in-depth: the input element MUST cap input at 4096 chars
    so the server-side validator's 4096-byte cap cannot be bypassed
    by a paste of (e.g.) a multi-MB blob designed to OOM the
    ``settings.json`` writer.  The cap MUST agree with the
    ``adoptiq_settings._is_valid_csone_folder_path`` server cap.
    """

    src = _PREFS_PATH.read_text(encoding="utf-8")
    # The maxlength MAY appear with single or double quotes; pin both.
    assert re.search(
        r"maxlength=[\"']4096[\"']", src
    ), (
        "Round 88 / F5: input element must cap value at 4096 chars "
        "(matches adoptiq_settings._is_valid_csone_folder_path)."
    )


# Round 88 / Build 64
@pytest.mark.parametrize("forbidden_attr", [
    "onclick=",
    "onload=",
    "onerror=",
    "onfocus=",
    "javascript:",
])
def test_r88_preferences_card_no_inline_event_handlers(forbidden_attr: str) -> None:
    """Negative control: the card MUST NOT carry any inline event
    handlers (``onclick=``, ``onload=``, etc.) or ``javascript:``
    URLs -- both are XSS sinks and break the strict CSP we ship.
    """

    src = _PREFS_PATH.read_text(encoding="utf-8")
    # Scope the check to the CSOne folder card section so we don't
    # trip on inline handlers in unrelated parts of the template.
    start = src.find("data-csone-folder-card")
    assert start > 0, "card section anchor missing"
    end = src.find("</section>", start)
    assert end > start, "card section close tag missing"
    card_src = src[start:end]
    assert forbidden_attr not in card_src.lower(), (
        f"Round 88 / F5: the CSOne folder card must NOT carry the "
        f"``{forbidden_attr}`` inline handler / URL scheme -- "
        "this is the XSS / CSP class of bug."
    )


# Round 88 / Build 64
def test_r88_round_88_marker_present_in_js_module() -> None:
    """Round-marker convention: every R88 source file should carry a
    ``Round 88`` comment so ``git diff <file> | grep 'Round 88'`` gives
    a per-file footprint.
    """

    src = _JS_PATH.read_text(encoding="utf-8")
    assert "Round 88" in src, (
        "Round 88 / F5: r88_csone_folder_override.js must carry the "
        "``Round 88`` source marker."
    )


# Round 88 / Build 64
def test_r88_round_88_marker_present_in_preferences_template() -> None:
    """Same convention for the template change."""

    src = _PREFS_PATH.read_text(encoding="utf-8")
    assert "Round 88" in src, (
        "Round 88 / F5: templates/preferences.html must carry a ``Round 88`` "
        "source marker for the new CSOne folder card section."
    )
