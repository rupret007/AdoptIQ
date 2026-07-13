"""Round 73 / Phase 4 (UX-2): no inline model picker on /analyze or /ask-ai.

Pre-Round-73 the LLM model pickers lived inline on /analyze (the
``data-r69-model-card="report"`` card above the analysis form) and
/ask-ai (the ``data-r69-model-card="ask_ai"`` card above the chat
history).  R73 / UX-1 introduced a dedicated /preferences hub that
owns BOTH pickers; R73 / UX-2 strips the inline cards from /analyze
and /ask-ai so each surface stays focused on its primary task and
operators have a single, discoverable place to tune model selection.

This file pins the UX-2 contract at three layers:

* Source-shape: ``analyze.html`` and ``ask_ai.html`` MUST NOT
  contain any ``data-r69-model-card`` element.
* Source-shape: neither template loads ``r69_model_preferences.js``
  any more (the bundle is only useful when at least one
  ``data-r69-model-card`` is in the DOM).
* Runtime: the rendered /analyze and /ask-ai HTML MUST NOT carry
  the picker hooks, but BOTH pages MUST still link to /preferences
  via the navbar so the user can find the relocated controls.

The R69 contract that pinned the OLD inline locations
(``test_round69_model_preferences.py::test_r69_*_template_carries_*``)
was rewritten in lockstep to pin the absence (so a future revert
that re-introduces the inline cards fails BOTH the R73 / UX-2
contract and the rewritten R69 source-shape pin).
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


def test_analyze_template_has_no_inline_report_card():
    src = _read("templates/analyze.html")
    assert 'data-r69-model-card="report"' not in src, (
        "Round 73 / UX-2: analyze.html still carries the inline "
        "report-narrative model card -- it must live only on /preferences"
    )
    # The whole r69ReportModelCard <section> wrapper must also be
    # gone -- the data-attribute pin above is the contract, but the
    # id is what an operator might still grep for.
    assert "r69ReportModelCard" not in src, (
        "Round 73 / UX-2: analyze.html still carries the legacy "
        "id=\"r69ReportModelCard\" wrapper"
    )


def test_analyze_template_does_not_load_r69_module():
    """Pin the absence of the actual ``url_for('static', filename=...)``
    load.  The substring ``r69_model_preferences.js`` itself MAY appear
    once inside an explanatory comment block (intentional: future
    operators reading the template see why the script is gone), so we
    target the ``filename='js/...'`` Jinja form that uniquely identifies
    the load itself."""
    src = _read("templates/analyze.html")
    assert "filename='js/r69_model_preferences.js'" not in src, (
        "Round 73 / UX-2: analyze.html still loads r69_model_preferences.js "
        "via url_for(static, ...) -- the page has no [data-r69-model-card] "
        "container so the script would download an inert bundle on every visit"
    )


def test_ask_ai_template_has_no_inline_ask_ai_card():
    src = _read("templates/ask_ai.html")
    assert 'data-r69-model-card="ask_ai"' not in src, (
        "Round 73 / UX-2: ask_ai.html still carries the inline Ask AI "
        "model card -- it must live only on /preferences"
    )
    assert "r69AskAiModelCard" not in src, (
        "Round 73 / UX-2: ask_ai.html still carries the legacy "
        "id=\"r69AskAiModelCard\" wrapper"
    )


def test_ask_ai_template_does_not_load_r69_module():
    """Same contract as ``test_analyze_template_does_not_load_r69_module``;
    pin the absence of the load itself, not the substring."""
    src = _read("templates/ask_ai.html")
    assert "filename='js/r69_model_preferences.js'" not in src, (
        "Round 73 / UX-2: ask_ai.html still loads r69_model_preferences.js "
        "via url_for(static, ...) -- the page has no [data-r69-model-card] "
        "container so the script would download an inert bundle on every visit"
    )


def test_ux2_source_marker_in_analyze_html():
    """The UX-2 stripping carries an inline source marker so a future
    'restore the inline picker' refactor leaves a paper trail."""
    src = _read("templates/analyze.html")
    assert "Round 73 / Phase 4 (UX-2)" in src, (
        "Round 73 / UX-2: source marker missing in analyze.html"
    )


def test_ux2_source_marker_in_ask_ai_html():
    src = _read("templates/ask_ai.html")
    assert "Round 73 / Phase 4 (UX-2)" in src, (
        "Round 73 / UX-2: source marker missing in ask_ai.html"
    )


# ---------------------------------------------------------------------------
# Runtime pins (rendered body)
# ---------------------------------------------------------------------------


def test_analyze_route_renders_without_inline_picker(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Hard pin: the data-attribute and id must both be gone.
    assert 'data-r69-model-card="report"' not in body, (
        "Round 73 / UX-2: rendered /analyze body still includes the "
        "inline report-narrative model card"
    )
    assert "r69ReportModelCard" not in body, (
        "Round 73 / UX-2: rendered /analyze body still includes the "
        "id=\"r69ReportModelCard\" wrapper"
    )


def test_ask_ai_route_renders_without_inline_picker(client):
    resp = client.get("/ask-ai")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-r69-model-card="ask_ai"' not in body, (
        "Round 73 / UX-2: rendered /ask-ai body still includes the "
        "inline Ask AI model card"
    )
    assert "r69AskAiModelCard" not in body, (
        "Round 73 / UX-2: rendered /ask-ai body still includes the "
        "id=\"r69AskAiModelCard\" wrapper"
    )


def test_analyze_route_links_to_preferences_via_navbar(client):
    """Stripping the inline picker is only acceptable if the navbar
    keeps a discoverable link to /preferences from every page."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/preferences"' in body, (
        "Round 73 / UX-2: /analyze does not include a navbar link to "
        "/preferences -- operators have nowhere to find the relocated "
        "model picker"
    )


def test_ask_ai_route_links_to_preferences_via_navbar(client):
    resp = client.get("/ask-ai")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/preferences"' in body, (
        "Round 73 / UX-2: /ask-ai does not include a navbar link to "
        "/preferences -- operators have nowhere to find the relocated "
        "model picker"
    )


def test_preferences_still_carries_both_pickers(client):
    """Final cross-check: UX-2 only removes the inline cards because
    UX-1 added the consolidated cards on /preferences.  If a future
    refactor removes both inline AND /preferences cards by mistake,
    this test catches the gap."""
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-r69-model-card="report"' in body, (
        "Round 73 / UX-2: /preferences lost the report-narrative card "
        "-- both inline AND consolidated locations are now empty"
    )
    assert 'data-r69-model-card="ask_ai"' in body, (
        "Round 73 / UX-2: /preferences lost the Ask AI card -- both "
        "inline AND consolidated locations are now empty"
    )
