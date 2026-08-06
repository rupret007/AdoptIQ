"""Round 28 / Phase 2 -- ``/previous-reports`` is a real Jinja child of base.html.

Before Round 28 ``previous_reports()`` in ``app_simple.py`` returned
an inline f-string with a light-mode palette (``#f5f5f5`` body,
white container, navy ``#1e3a8a`` headings) and never rendered
the navbar or the dark/light toggle, so users browsing previous
artifacts saw a different visual language than the rest of the
app.

This test pins the migration: the route renders the new
``templates/previous_reports.html`` (which extends base.html),
the dark/light toggle is reachable, and the user-visible strings
("Previous AdoptIQ Reports", "No previous reports found in the
output folder.", "Back to Main Page") survive the rewrite so
existing links and habits still work.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_previous_reports_route_renders_template_with_theme_chrome(client):
    resp = client.get("/previous-reports")
    assert resp.status_code == 200, resp.data[:500]

    body = resp.get_data(as_text=True)

    assert_in_source(body, 'data-bs-theme=', label='body')
    assert_in_source(body, 'id="theme-toggle"', label='body')
    assert_in_source(body, "Previous AdoptIQ Reports", label='body')
    assert_in_source(body, "Back to Main Page", label='body')

    # Old hardcoded hex must not leak through.
    for stale_hex in ("#f5f5f5", "#1e3a8a"):
        assert stale_hex.lower() not in body.lower(), (
            "Round 28 / Phase 2: hardcoded hex %s leaked into "
            "/previous-reports; switch to var(--bg-base) / "
            "var(--accent-primary) so the toggle flips it." % stale_hex
        )


def test_previous_reports_route_uses_render_template():
    """Make sure the route hands off to ``previous_reports.html`` -- a
    revert to the inline f-string would silently strip the dark/light
    toggle without breaking the smoke test above (the endpoint would
    still 200).  Asserting on the source guarantees the migration
    contract.
    """
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "render_template('previous_reports.html'", label='src')

    template = REPO_ROOT / "templates" / "previous_reports.html"
    assert template.exists(), (
        "Round 28 / Phase 2: templates/previous_reports.html must "
        "exist -- it is the new view for /previous-reports."
    )
    template_src = template.read_text(encoding="utf-8")
    assert_in_source(template_src, '{% extends "base.html" %}', label='template_src')
