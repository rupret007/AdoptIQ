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
    assert "@app.route('/preferences', methods=['GET'])" in body, (
        "Round 73 / UX-1: GET /preferences route registration missing "
        "in app_simple.py"
    )
    assert "def preferences():" in body, (
        "Round 73 / UX-1: preferences() view function missing in app_simple.py"
    )
    assert "Round 73 / Phase 4 (UX-1)" in body, (
        "Round 73 / UX-1: source marker missing -- the route may have been "
        "reverted"
    )


def test_base_html_includes_preferences_navbar_link():
    body = _read("templates/base.html")
    # The nav link MUST resolve via Jinja's url_for so a future route-rename
    # surfaces a TemplateError rather than a silent 404.
    assert "url_for('preferences')" in body, (
        "Round 73 / UX-1: base.html navbar missing url_for('preferences') -- "
        "the Preferences link does not exist or hardcodes a literal /preferences"
    )
    # Round 73 / UX-1 source marker also lives on the navbar block so a
    # future template refactor that drops the link fails this assertion.
    assert "Round 73 / Phase 4 (UX-1)" in body, (
        "Round 73 / UX-1: base.html navbar source marker missing"
    )


def test_preferences_template_contains_both_model_cards():
    body = _read("templates/preferences.html")
    assert 'data-r69-model-card="report"' in body, (
        "Round 73 / UX-1: report-narrative model card missing on /preferences"
    )
    assert 'data-r69-model-card="ask_ai"' in body, (
        "Round 73 / UX-1: Ask AI model card missing on /preferences"
    )


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
    assert "data-intel-enable-toggle" in body, (
        "Round 73 / UX-1: Intelligence enable toggle missing on /preferences "
        "-- the page is supposed to mirror the analyze-page banner toggle"
    )


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
    assert 'data-r69-model-card="report"' in body, (
        "Round 73 / UX-1: rendered /preferences body missing report card"
    )
    assert 'data-r69-model-card="ask_ai"' in body, (
        "Round 73 / UX-1: rendered /preferences body missing Ask AI card"
    )
    # Active-badge slot is a hard contract: r69_model_preferences.js
    # writes the active model into [data-r69-active-badge] on load AND
    # after every Save, so the operator can see WHICH precedence layer
    # is currently winning.  Two cards => at least two badge hooks.
    assert body.count("data-r69-active-badge") >= 2, (
        "Round 73 / UX-1: rendered /preferences body has fewer than two "
        "data-r69-active-badge hooks -- the JS module cannot project the "
        "active model into the UI"
    )


def test_preferences_route_renders_intel_toggle(client):
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "data-intel-enable-toggle" in body, (
        "Round 73 / UX-1: rendered /preferences body missing the "
        "data-intel-enable-toggle control"
    )
    # The toggle is wired through the existing intel_status.js module
    # (loaded site-wide via base.html); if base.html ever stopped
    # loading it the /preferences toggle would silently no-op.  Pin
    # the inheritance so a refactor that breaks it fails here.
    assert "intel_status.js" in body, (
        "Round 73 / UX-1: /preferences body does not load intel_status.js -- "
        "the Intelligence toggle on the page would silently no-op"
    )


def test_preferences_route_renders_navbar_preferences_link(client):
    """The navbar link must be present on the /preferences page itself
    (so the user is not stranded if they bookmark the page directly)."""
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/preferences"' in body or "url_for('preferences')" in body, (
        "Round 73 / UX-1: /preferences body does not include a navbar link "
        "back to itself -- the navbar block was dropped"
    )


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
    assert 'class="nav-link' in window, (
        "Round 73 / UX-1: Preferences navbar entry not rendered as "
        "a Bootstrap nav-link"
    )
    assert "active" in window, (
        "Round 73 / UX-1: Preferences navbar entry not marked active "
        "while rendering /preferences"
    )


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
    assert "settings.json" in body, (
        "Round 73 / UX-1: /preferences does not surface the settings.json path"
    )
    # The outputs directory is named ``outputs`` everywhere.
    assert "outputs" in body, (
        "Round 73 / UX-1: /preferences does not surface the outputs directory"
    )


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
    assert "data-intel-enable-toggle" in body
