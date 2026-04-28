"""Round 29 / L3 -- the early-paint script in base.html honours
``prefers-color-scheme``.

Round 28 wired ``static/js/theme-toggle.js::detectPreferredTheme()``
to consult ``window.matchMedia('(prefers-color-scheme: light)')`` for
first-time visitors.  But that script runs after ``DOMContentLoaded``,
so the early-paint inline ``<script>`` at the top of ``base.html``
(which runs synchronously to set ``data-bs-theme`` before any styles
apply) still painted dark on first visit, and the toggle.js then
flipped it to light, producing a visible flash.

Round 29 mirrors the same OS-preference detection inside the
early-paint script so the very first paint already matches the OS
preference when ``localStorage`` has no stored choice.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_HTML = REPO_ROOT / "templates" / "base.html"


def _extract_first_inline_script(src: str) -> str:
    """Return the body of the first ``<script>...</script>`` block
    in ``base.html`` (no ``src=`` attribute).  This is the early-paint
    bootstrap by convention.
    """
    pattern = re.compile(r"<script>(?P<body>.*?)</script>", re.DOTALL)
    match = pattern.search(src)
    assert match is not None, "base.html must contain an inline <script> block"
    return match.group("body")


def test_early_paint_script_consults_prefers_color_scheme():
    src = BASE_HTML.read_text(encoding="utf-8")
    body = _extract_first_inline_script(src)

    assert "matchMedia" in body, (
        "Round 29 / L3: the early-paint inline <script> in base.html "
        "must call ``window.matchMedia(...)`` so a first-visit on a "
        "light-mode OS paints light from the very first frame "
        "(otherwise theme-toggle.js flips it after DOMContentLoaded "
        "and the user sees a flash)."
    )
    assert "(prefers-color-scheme: light)" in body, (
        "Round 29 / L3: the early-paint inline <script> in base.html "
        "must query the ``(prefers-color-scheme: light)`` media "
        "query to mirror what theme-toggle.js does later."
    )


def test_early_paint_script_falls_back_to_dark_when_matchmedia_missing():
    """In private-browsing / SecurityError contexts ``matchMedia`` can
    throw or be undefined.  The early-paint script must keep painting
    the dark default in that case so the page is always deterministic.
    """
    src = BASE_HTML.read_text(encoding="utf-8")
    body = _extract_first_inline_script(src)

    assert "try" in body and "catch" in body, (
        "Round 29 / L3: the early-paint <script> in base.html must "
        "wrap matchMedia in try/catch so a SecurityError in private "
        "mode falls back to the dark default instead of throwing."
    )
    # Double-check the dark fallback string is present somewhere in
    # the script body (the prior implementation already had this; we
    # keep pinning it so a refactor cannot drop the safety net).
    assert "'dark'" in body, (
        "Round 29 / L3: the early-paint <script> in base.html must "
        "retain the ``'dark'`` string fallback so an unparseable "
        "stored value or a missing matchMedia API still paints "
        "deterministically."
    )


def test_early_paint_script_only_consults_os_preference_when_localstorage_empty():
    """The OS-preference branch must NOT run if ``localStorage``
    already has a stored choice -- otherwise an explicit user toggle
    would be overridden every visit when the OS reports the opposite.
    """
    src = BASE_HTML.read_text(encoding="utf-8")
    body = _extract_first_inline_script(src)

    # The script reads ``stored = localStorage.getItem(...)``, then
    # branches on ``theme === null`` (i.e. ``stored`` was neither
    # 'light' nor 'dark') to decide whether to consult matchMedia.
    assert "adoptiq-theme" in body, (
        "Round 29 / L3: the early-paint <script> must read "
        "``localStorage.getItem('adoptiq-theme')`` so an explicit "
        "user toggle persists across visits."
    )
    # Look for a guard like ``theme === null`` or ``stored === null``
    # that gates the matchMedia branch on an empty localStorage.
    assert "=== null" in body, (
        "Round 29 / L3: the early-paint <script> must gate the "
        "matchMedia branch on the stored value being null/missing "
        "(``=== null``) so an explicit user toggle is not "
        "overridden by the OS preference on subsequent visits."
    )
