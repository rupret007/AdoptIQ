"""Round 28 / Phase 1 -- ``/progress/<id>`` is a real Jinja child of base.html.

Pre-Round-28 the progress page was an inline f-string in
``app_simple.py`` with hardcoded light-mode hex colours
(``#f9fafb`` / ``#1a1a2e`` / ``#3b82f6``) and no ``data-bs-theme``
attribute.  That meant the navbar sun/moon toggle could not flip
this page without a reload, and on first paint a dark-mode user
saw a flash of light cards before any handler ran.

Round 28 migrated the body to ``templates/progress.html`` which
``{% extends "base.html" %}``.  This test pins:

1. The route renders successfully (200 OK) for a known status entry.
2. ``<html ... data-bs-theme="...">`` and the ``#theme-toggle``
   button from base.html are present in the response, so the
   site-wide toggle works without a reload.
3. The semantic CSS-variable system is in use (``var(--bg-surface)``
   appears at least once -- proving the migrated styles were
   rerouted through tokens rather than re-hardcoded).
4. None of the old hardcoded hex strings (``#f9fafb`` / ``#1a1a2e``
   / ``#3b82f6``) leak through, so a future drop-in revert would be
   caught immediately.
5. The user-visible contract strings ("AdoptIQ Analysis Progress",
   "AI-Powered Executive Analytics", "Cancel Analysis") still
   render verbatim because the polling JS reads / writes them by
   id and operators have learnt the wording.

A second test confirms that the old f-string body is no longer
emitted by the route, because relying solely on the template
content can mask a half-finished migration.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source, assert_not_in_source

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def _seeded_progress_status(app):
    """Inject a deterministic status row so /progress/<id> renders."""
    import app_simple as app_mod

    aid = "round28-progress-fixture"
    snapshot = None
    with app_mod.analysis_status_lock:
        snapshot = dict(app_mod.analysis_status)
        app_mod.analysis_status[aid] = {
            "status": "running",
            "progress": 42,
            "message": "Crunching the numbers",
            "current_step": "Analyzing customer data",
            "manager": "Jane Doe",
            "technology": "Webex",
            "days": 90,
            "start_time": "2026-04-25T10:00:00",
            "report_type": "comprehensive",
            "csone_import_status": "processing",
            "csone_import_message": "CSOne data successfully processed",
        }
    try:
        yield aid
    finally:
        with app_mod.analysis_status_lock:
            app_mod.analysis_status.clear()
            app_mod.analysis_status.update(snapshot)


def test_progress_route_renders_template_with_theme_chrome(client, _seeded_progress_status):
    aid = _seeded_progress_status
    resp = client.get(f"/progress/{aid}")
    assert resp.status_code == 200, resp.data[:500]

    body = resp.get_data(as_text=True)

    # base.html chrome -- proves the page extends base.html.
    assert_in_source(body, 'data-bs-theme=', label='body')
    assert_in_source(body, 'id="theme-toggle"', label='body')

    # Semantic-token usage -- proves we replaced hardcoded hex with
    # the CSS variable system.
    assert_in_source(body, "var(--bg-surface)", label='body')

    # Old hardcoded hex must not leak back in.
    for stale_hex in ("#f9fafb", "#1a1a2e", "#3b82f6"):
        assert stale_hex.lower() not in body.lower(), (
            "Round 28 / Phase 1: hardcoded light-mode hex %s leaked "
            "back into /progress/<id>; that defeats the dark/light "
            "toggle.  Use var(--bg-surface), var(--bg-base), "
            "var(--accent-primary) instead." % stale_hex
        )

    # Visible contract strings -- operators have learnt these.
    for needed in (
        "AdoptIQ Analysis Progress",
        "AI-Powered Executive Analytics",
        "Cancel Analysis",
        "CSOne data successfully processed",
    ):
        assert needed in body, (
            "Round 28 / Phase 1: visible contract string %r missing "
            "from /progress/<id>." % needed
        )


def test_progress_route_no_longer_returns_inline_fstring(client, _seeded_progress_status):
    """The route used to return inline HTML built via ``html_content = f'''...'''``
    and ``return html_content``.  After the migration it must hand off to
    ``render_template('progress.html', ...)`` -- so a sniff of the source
    catches anyone who tries to revive the inline path.
    """
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "render_template('progress.html'", label='src')
    assert_not_in_source(src, "_r28_legacy_progress_html_unused", label='src')
