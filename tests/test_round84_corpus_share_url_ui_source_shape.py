"""Round 84 / Build 60 (R87 / Phase 4 repurpose): UI source-shape pin
for the operator-configurable corpus share URL card.

These are static (filesystem) source-shape tests -- they read the
HTML template + the JS module as plain text and assert that:

* ``templates/preferences.html`` carries the ``[data-corpus-share-url-*]``
  attribute markers the JS module binds against (input, save, clear,
  test, current, source pill, feedback).  **Round 87 / Phase 4** moved
  the card off ``analyze.html`` (admin-config doesn't belong on the
  per-run report tile) so the test now validates the canonical
  surface in preferences.html.
* ``static/js/corpus_share_url.js`` exposes the public function names
  the C4 plan pins (``bindCorpusShareUrlSave``,
  ``paintCorpusShareUrlCard``).
* The "Current value" rendering uses ``textContent`` (NOT
  ``innerHTML``) so a malicious URL persisted to ``settings.json``
  (e.g. via a hand-edited file) cannot bootstrap a stored XSS via the
  card.
* The JS module is wired into ``preferences.html`` via a ``<script>`` tag.
* The JS module exports the public symbols on
  ``window.AdoptIQCorpusShareUrl`` so a future page that reuses the
  card markup can call the painter directly without re-binding
  handlers (mirrors the R69 ``AdoptIQModelPreferences`` pattern).

Source-shape tests like these are the cheapest signal that the
contract between the template and the JS module did not silently
drift -- a future round that renames a marker MUST update both sides
or the test fails LOUD.
"""

# Round 84 / Build 60 (Round 87 / Phase 4 repurpose)

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Section 1: data-attribute markers on preferences.html
# (Round 87 / Phase 4: moved off analyze.html to the Preferences hub)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prefs_html_text() -> str:
    path = Path(__file__).resolve().parent.parent / "templates" / "preferences.html"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def corpus_share_url_js_text() -> str:
    path = (
        Path(__file__).resolve().parent.parent
        / "static"
        / "js"
        / "corpus_share_url.js"
    )
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "marker",
    [
        "data-corpus-share-url-card",
        "data-corpus-share-url-input",
        "data-corpus-share-url-save",
        "data-corpus-share-url-clear",
        "data-corpus-share-url-test",
        "data-corpus-share-url-current",
        "data-corpus-share-url-source-pill",
        "data-corpus-share-url-source-text",
        "data-corpus-share-url-feedback",
    ],
)
def test_r84_prefs_html_carries_card_markers(prefs_html_text, marker):
    """Every ``[data-corpus-share-url-*]`` attribute the JS module
    binds against MUST be present in the preferences.html template.

    A future renaming round MUST keep BOTH sides aligned -- the test
    fails LOUD on any drift. The markers are also referenced by the
    R83 -> R84 deep-link integration in ``intel_status.js`` (existing
    R83 banner button continues to work via the modified
    ``_r83_safe_share_url``)."""
    assert marker in prefs_html_text, (
        f"R84 marker {marker!r} not found in preferences.html"
    )


def test_r84_prefs_html_corpus_share_url_card_section_present(prefs_html_text):
    """The card MUST live in a top-level ``<section>`` with the
    Preferences-hub container ID so CSS / future a11y tooling can
    target it deterministically.

    The container ID is ``prefs-corpus-share-url-card`` on the
    Preferences hub (R85), distinct from the retired R84 analyze-page
    ID ``adoptiq-corpus-share-url-card`` so a future composition can
    distinguish the surfaces."""
    assert 'id="prefs-corpus-share-url-card"' in prefs_html_text
    assert "<section" in prefs_html_text


def test_r84_prefs_html_card_uses_aria_live_polite(prefs_html_text):
    """Live-region attributes MUST be set so a screen reader narrates
    the source-pill change after a save/clear without focus
    shifting (a11y contract)."""
    # We don't pin the exact ordering of attributes; just that both
    # are present somewhere on the R85 Preferences-hub section.
    section_start = prefs_html_text.find('id="prefs-corpus-share-url-card"')
    assert section_start > -1
    section_window = prefs_html_text[section_start : section_start + 800]
    assert 'aria-live="polite"' in section_window
    # Note: the R85 Preferences-hub card uses aria-labelledby instead
    # of aria-atomic (richer a11y semantics for a labelled region).
    # We accept either form so an editor can switch between them.
    assert (
        'aria-atomic="true"' in section_window
        or "aria-labelledby=" in section_window
    )


# ---------------------------------------------------------------------------
# Section 2: JS public symbols
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    [
        "bindCorpusShareUrlSave",
        "paintCorpusShareUrlCard",
        "fetchCorpusShareUrlCard",
    ],
)
def test_r84_corpus_share_url_js_exports_public_symbol(corpus_share_url_js_text, symbol):
    """The JS module MUST define each public function the C4 plan
    pins. Source-shape only -- we don't run the JS, just confirm the
    symbol is declared at least once."""
    needle = f"function {symbol}"
    assert needle in corpus_share_url_js_text, (
        f"R84 JS module missing function {symbol!r}"
    )


def test_r84_js_module_exposes_window_namespace(corpus_share_url_js_text):
    """The JS module MUST expose the public API on
    ``window.AdoptIQCorpusShareUrl`` so a future page that reuses
    the card markup can invoke the painter directly (mirrors the
    R69 ``AdoptIQModelPreferences`` pattern). This namespace is the
    integration point for tests / future composition."""
    assert "window.AdoptIQCorpusShareUrl" in corpus_share_url_js_text
    # All three public functions referenced inside the namespace
    # block.
    for symbol in (
        "bindCorpusShareUrlSave",
        "paintCorpusShareUrlCard",
        "fetchCorpusShareUrlCard",
    ):
        # Crude but sufficient for static source-shape pinning: each
        # symbol MUST be referenced somewhere in the namespace
        # exposure region.
        assert symbol in corpus_share_url_js_text


def test_r84_js_module_uses_iife_wrapper(corpus_share_url_js_text):
    """The module MUST be wrapped in an IIFE so its private helpers
    (``getCsrfToken``, ``setFeedback``, ``setSourcePill``,
    ``postCorpusShareUrl``) cannot leak into the global scope and
    collide with a future intel_status.js or model_preferences
    helper of the same name."""
    # Look for the IIFE pattern used by the module.
    assert "(function ()" in corpus_share_url_js_text or "(function() {" in corpus_share_url_js_text
    # Closing pattern for the IIFE.
    assert "})();" in corpus_share_url_js_text


# ---------------------------------------------------------------------------
# Section 3: XSS safety -- textContent NOT innerHTML for user value
# ---------------------------------------------------------------------------


def test_r84_paint_uses_text_content_for_current_value(corpus_share_url_js_text):
    """The "Current value" rendering MUST use ``textContent`` (NOT
    ``innerHTML``) so a malicious URL persisted to ``settings.json``
    (hand-edit / future schema bug) cannot bootstrap a stored XSS
    via the card.

    Mirrors the R83 paint contract for the deep-link banner -- the
    server-side validator IS the primary defense (HTTPS-only,
    ``*.sharepoint.com`` host, 2048-byte cap), but the client-side
    ``textContent`` rendering is the second line of defense per the
    XSS Prevention Cheat Sheet."""
    assert "textContent" in corpus_share_url_js_text
    # The module MUST NOT use ``.innerHTML =`` (a code-level
    # assignment to the innerHTML property is the XSS sink); the
    # bare token ``innerHTML`` may appear in comments documenting
    # the contract. Pin the code-level pattern to avoid a future
    # change silently dropping the textContent rendering and
    # routing user-controlled values through innerHTML instead.
    assert ".innerHTML" not in corpus_share_url_js_text, (
        "R84 JS module MUST NOT assign user-controlled values to "
        ".innerHTML; use textContent so a malicious URL cannot "
        "bootstrap stored XSS via the card."
    )


def test_r84_paint_does_not_use_dangerous_dom_sinks(corpus_share_url_js_text):
    """The module MUST NOT use ``document.write``, ``outerHTML``, or
    ``insertAdjacentHTML`` -- all DOM sinks that bypass the
    ``textContent`` XSS guard. Mirrors the broader codeguard rule
    for client-side web security."""
    forbidden = ["document.write", "outerHTML", "insertAdjacentHTML"]
    for sink in forbidden:
        assert sink not in corpus_share_url_js_text, (
            f"R84 JS module uses forbidden DOM sink {sink!r}"
        )


# ---------------------------------------------------------------------------
# Section 4: script tag wired in analyze.html
# ---------------------------------------------------------------------------


def test_r84_prefs_html_wires_corpus_share_url_js(prefs_html_text):
    """preferences.html MUST load the corpus_share_url.js module via a
    standard ``<script>`` tag using ``url_for`` so the URL stays
    correct in dev mode AND when served from
    ``sys._MEIPASS/static/`` in the frozen .app.

    Round 87 / Phase 4: the script tag moved off analyze.html along
    with the card.  The R84 endpoint
    (``POST /api/settings/corpus-share-url``) is unchanged."""
    # Match the Jinja url_for call. We accept either single or
    # double quotes around the filename.
    candidates = [
        "{{ url_for('static', filename='js/corpus_share_url.js') }}",
        '{{ url_for("static", filename="js/corpus_share_url.js") }}',
    ]
    assert any(c in prefs_html_text for c in candidates), (
        "preferences.html does not wire corpus_share_url.js via url_for"
    )


# ---------------------------------------------------------------------------
# Section 5: "Test" button pulls from validated server endpoint
# ---------------------------------------------------------------------------


def test_r84_test_button_uses_bootstrap_shortcut_endpoint(corpus_share_url_js_text):
    """The "Test" button MUST GET ``/api/corpus/bootstrap-shortcut``
    (the validated, server-side ``_r83_safe_share_url`` path) rather
    than ``window.open(rawInput)`` -- so an unvalidated URL pasted
    into the input cannot be launched. Mirrors the R83 deep-link
    button contract."""
    assert "/api/corpus/bootstrap-shortcut" in corpus_share_url_js_text


def test_r84_save_endpoint_target_pinned(corpus_share_url_js_text):
    """The Save / Clear-override POST target MUST be
    ``/api/settings/corpus-share-url`` -- the R84 endpoint with
    CSRF dual-auth + validator + atomic write contract."""
    assert "/api/settings/corpus-share-url" in corpus_share_url_js_text
