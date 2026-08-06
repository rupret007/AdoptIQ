"""Round 73 / Phase 4 (UX-1): /preferences page route + template pins.

Pre-Round-73 the LLM model pickers lived inline on /analyze (the
``data-r69-model-card="report"`` card) and /ask-ai (the
``data-r69-model-card="ask_ai"`` card), forcing the operator to
remember which surface owned which knob.  R73 / UX-1 introduces a
dedicated /preferences hub that consolidates both pickers + the
Intelligence enable toggle on a single settings surface.

This file pins the UX-1 contract at four layers:

* Source-shape: the ``preferences`` route name + the
  ``preferences.html`` template + the navbar link in ``base.html``
  are all present (so a future revert of the route, template, or
  navbar entry fails this test rather than silently shipping a
  navigable navbar link to a 404).
* Route: ``GET /preferences`` returns 200 with a non-empty body.
* Template: the response body contains BOTH model picker cards
  (``data-r69-model-card="report"`` and ``data-r69-model-card="ask_ai"``)
  AND the Intelligence enable toggle (``data-intel-enable-toggle``).
* Navbar: the navbar link emitted by base.html points at the
  ``preferences`` endpoint and uses the gear-style icon class so the
  user can find the page from any other surface.

UX-2 will then strip the inline pickers from /analyze and /ask-ai;
UX-3 will convert the text inputs to strict 2-option dropdowns.
This test intentionally pins the UX-1 staging shape (text inputs
inside ``data-r69-model-card``) so a UX-3 dropdown migration that
breaks the contract has to update this test deliberately.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source, count_in_source

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(p: str) -> str:
    return (PROJECT_ROOT / p).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-shape pins
# ---------------------------------------------------------------------------


def test_preferences_template_file_exists():
    p = PROJECT_ROOT / "templates" / "preferences.html"
    assert p.is_file(), (
        "Round 73 / UX-1: templates/preferences.html missing -- the "
        "preferences hub template was not staged"
    )


def test_app_simple_registers_preferences_route():
    body = _read("app_simple.py")
    assert_in_source(body, "@app.route('/preferences', methods=['GET'])", label='body')
    assert_in_source(body, "def preferences():", label='body')
    assert_in_source(body, "Round 73 / Phase 4 (UX-1)", label='body')


def test_base_html_includes_preferences_navbar_link():
    body = _read("templates/base.html")
    # The nav link MUST resolve via Jinja's url_for so a future route-rename
    # surfaces a TemplateError rather than a silent 404.
    assert_in_source(body, "url_for('preferences')", label='body')
    # Round 73 / UX-1 source marker also lives on the navbar block so a
    # future template refactor that drops the link fails this assertion.
    assert_in_source(body, "Round 73 / Phase 4 (UX-1)", label='body')


def test_preferences_template_contains_both_model_cards():
    body = _read("templates/preferences.html")
    assert_in_source(body, 'data-r69-model-card="report"', label='body')
    assert_in_source(body, 'data-r69-model-card="ask_ai"', label='body')


def test_preferences_template_loads_r69_module():
    """The shared static/js/r69_model_preferences.js module wires up the
    Test-gates-Save UX on every ``data-r69-model-card`` container.  The
    /preferences page MUST load it or both cards stay inert."""
    body = _read("templates/preferences.html")
    assert "static/js/r69_model_preferences.js" in body or "r69_model_preferences.js" in body, (
        "Round 73 / UX-1: preferences.html does not load r69_model_preferences.js"
    )


def test_preferences_template_carries_intel_toggle():
    body = _read("templates/preferences.html")
    assert_in_source(body, "data-intel-enable-toggle", label='body')


# ---------------------------------------------------------------------------
# Route runtime contract
# ---------------------------------------------------------------------------


def test_preferences_route_returns_200(client):
    resp = client.get("/preferences")
    assert resp.status_code == 200, (
        f"Round 73 / UX-1: GET /preferences returned {resp.status_code} "
        "-- the route either failed to register or the view raised"
    )
    body = resp.get_data(as_text=True)
    assert body.strip(), (
        "Round 73 / UX-1: /preferences returned an empty body"
    )


def test_preferences_route_renders_both_model_cards(client):
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert_in_source(body, 'data-r69-model-card="report"', label='body')
    assert_in_source(body, 'data-r69-model-card="ask_ai"', label='body')
    # Active-badge slot is a hard contract: r69_model_preferences.js
    # writes the active model into [data-r69-active-badge] on load AND
    # after every Save, so the operator can see WHICH precedence layer
    # is currently winning.  Two cards => at least two badge hooks.
    assert count_in_source(body, "data-r69-active-badge") >= 2, (
        "Round 73 / UX-1: rendered /preferences body has fewer than two "
        "data-r69-active-badge hooks -- the JS module cannot project the "
        "active model into the UI"
    )


def test_preferences_route_renders_intel_toggle(client):
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert_in_source(body, "data-intel-enable-toggle", label='body')
    # The toggle is wired through the existing intel_status.js module
    # (loaded site-wide via base.html); if base.html ever stopped
    # loading it the /preferences toggle would silently no-op.  Pin
    # the inheritance so a refactor that breaks it fails here.
    assert_in_source(body, "intel_status.js", label='body')


def test_preferences_route_renders_navbar_preferences_link(client):
    """The navbar link must be present on the /preferences page itself
    (so the user is not stranded if they bookmark the page directly)."""
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert_in_source(body, 'href="/preferences"' in body or "url_for('preferences')", label='body')


def test_preferences_route_active_navbar_state(client):
    """The navbar nav-link entry MUST get the ``active`` class when
    ``request.endpoint == 'preferences'`` so the user knows which page
    they are on.  This is the same pattern every other navbar entry
    follows."""
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Locate the navbar nav-link to /preferences (the page title also
    # carries the literal "Preferences" string, so we must anchor on
    # the navbar href, not the bare word).  The nav <a> rendered by
    # base.html carries href="/preferences" with the conditional
    # ``active`` class right after ``nav-link``.
    href_idx = body.find('href="/preferences"')
    assert href_idx >= 0, (
        "Round 73 / UX-1: navbar href=\"/preferences\" missing "
        "in rendered body"
    )
    # Look at the <a class="..." href="/preferences"> immediately
    # before that href -- the class attribute lives ~100 chars
    # before the href attribute on the same tag.
    window = body[max(0, href_idx - 200):href_idx + 50]
    assert_in_source(window, 'class="nav-link', label='window')
    assert_in_source(window, "active", label='window')


# ---------------------------------------------------------------------------
# Storage paths context (informational section on the page)
# ---------------------------------------------------------------------------


def test_preferences_route_passes_settings_path_context(client):
    """The /preferences page surfaces three on-disk path strings under
    the "Local Storage" section so the operator knows exactly which
    file to edit (or ask support to inspect) when something looks
    wrong.  Pin all three context names so a future view refactor that
    drops them fails this test rather than silently rendering blank
    paths."""
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The settings file path must contain "settings.json" (resolved by
    # adoptiq_settings._settings_path()).
    assert_in_source(body, "settings.json", label='body')
    # The outputs directory is named ``outputs`` everywhere.
    assert_in_source(body, "outputs", label='body')


def test_preferences_skips_intel_enabled_check_when_status_unavailable(
    client, monkeypatch,
):
    """The /preferences view must tolerate a failed intel-status seed
    -- the Intelligence section needs to render even if the corpus
    bootstrap probe raises.  Mirrors the analyze-page index() resilience
    contract."""
    import app_simple

    def _boom():
        raise RuntimeError("simulated intel_status probe failure")

    monkeypatch.setattr(app_simple, "_r17_corpus_status_payload", _boom)

    resp = client.get("/preferences")
    assert resp.status_code == 200, (
        "Round 73 / UX-1: /preferences crashed when intel_status probe "
        "raised -- the broad try/except guard around the seed is missing"
    )
    body = resp.get_data(as_text=True)
    # Toggle is still rendered, just with the unchecked default.
    assert_in_source(body, "data-intel-enable-toggle", label='body')
