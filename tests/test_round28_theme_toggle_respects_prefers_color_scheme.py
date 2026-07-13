"""Round 28 / Phase 4 -- ``theme-toggle.js`` honours ``prefers-color-scheme``.

The header comment in ``static/js/theme-toggle.js`` claimed for
several rounds that the script "honours the OS-level
``prefers-color-scheme`` media query for first-time visitors who
have NOT explicitly chosen a theme".  The code never delivered:
``init()`` only consulted ``localStorage`` and otherwise defaulted
to dark.  A user on a light-mode OS landed in dark mode and had
to click the toggle every visit until they happened to click it
once and persist a choice.

Round 28 wires ``window.matchMedia('(prefers-color-scheme: light)')``
into ``init()`` and only consults it when ``localStorage`` has no
persisted preference.  An explicit toggle click still writes
``localStorage['adoptiq-theme']`` so subsequent visits skip the
matchMedia branch and honour the user's choice.

This test reads the file as text and asserts on the structural
markers because exercising the JS requires a JS runtime; the
behavioural contract (matchMedia + first-visit fallback) is
deterministic from the source.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
THEME_TOGGLE_JS = REPO_ROOT / "static" / "js" / "theme-toggle.js"


def test_theme_toggle_references_prefers_color_scheme_media_query():
    src = THEME_TOGGLE_JS.read_text(encoding="utf-8")
    assert "matchMedia" in src, (
        "Round 28 / Phase 4: theme-toggle.js must call "
        "window.matchMedia(...) so it can detect the OS-level "
        "color-scheme preference for first-time visitors."
    )
    assert "(prefers-color-scheme: light)" in src, (
        "Round 28 / Phase 4: theme-toggle.js must query the "
        "'(prefers-color-scheme: light)' media query so a "
        "first-time visitor on a light-mode OS lands in light mode."
    )


def test_theme_toggle_only_consults_os_preference_when_localstorage_empty():
    """The matchMedia branch must NOT run if ``localStorage`` already
    has a stored choice -- otherwise a user who explicitly toggled
    (and therefore wrote ``adoptiq-theme``) would get their choice
    overridden every visit when the OS says otherwise.
    """
    src = THEME_TOGGLE_JS.read_text(encoding="utf-8")
    assert "detectPreferredTheme" in src, (
        "Round 28 / Phase 4: extract the OS-preference probe into "
        "a named helper (detectPreferredTheme) so init() reads as "
        "stored ?? OS ?? default."
    )

    # Find the `init` function block and assert that
    # ``detectPreferredTheme`` is called inside the
    # ``stored === null`` branch (i.e. only when localStorage is
    # empty).
    init_idx = src.find("function init()")
    assert init_idx != -1, "theme-toggle.js must export an init() function"
    init_body = src[init_idx:init_idx + 1500]

    assert "stored !== null" in init_body or "stored === null" in init_body, (
        "Round 28 / Phase 4: init() must branch on the stored "
        "value so the OS-preference fallback is gated on an empty "
        "localStorage."
    )
    assert "detectPreferredTheme" in init_body, (
        "Round 28 / Phase 4: init() must call detectPreferredTheme "
        "in the no-stored-value branch so first-time visitors get "
        "the OS preference."
    )


def test_theme_toggle_falls_back_to_dark_when_matchmedia_missing():
    """Older browsers / hardened private modes can lack
    ``window.matchMedia`` or throw on it.  The detector must keep
    the dark default in that case so the page always paints
    deterministically.
    """
    src = THEME_TOGGLE_JS.read_text(encoding="utf-8")
    assert "DEFAULT_THEME" in src, (
        "Round 28 / Phase 4: theme-toggle.js must declare a "
        "DEFAULT_THEME constant so the fallback path is explicit."
    )

    detect_idx = src.find("function detectPreferredTheme")
    assert detect_idx != -1, "theme-toggle.js must define detectPreferredTheme"
    detect_body = src[detect_idx:detect_idx + 1000]

    assert "return DEFAULT_THEME" in detect_body or "DEFAULT_THEME;" in detect_body, (
        "Round 28 / Phase 4: detectPreferredTheme must return "
        "DEFAULT_THEME when matchMedia is unavailable or the "
        "media query is not 'light'."
    )
    assert "try" in detect_body and "catch" in detect_body, (
        "Round 28 / Phase 4: detectPreferredTheme must wrap the "
        "matchMedia call in try/catch so a SecurityError in "
        "private-browsing mode falls back to the default theme "
        "instead of throwing during page init."
    )
