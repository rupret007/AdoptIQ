"""Round 162.1 -- global navigation clarity and responsive layout pins."""

from __future__ import annotations

import re

from tests.source_shape_utils import assert_in_source, read_repo_file


def _base() -> str:
    return read_repo_file("templates/base.html")


def _analyze() -> str:
    return read_repo_file("templates/analyze.html")


def test_primary_nav_has_one_clear_analysis_destination() -> None:
    """The index is the analysis workspace, so two labels were misleading."""
    body = _base()
    nav = body.split('<ul class="navbar-nav ms-auto">', 1)[1].split("</ul>", 1)[0]
    assert ">Dashboard<" not in nav
    assert nav.count(">Run Analysis") == 1


def test_primary_nav_is_named_and_collapses_before_it_overflows() -> None:
    body = _base()
    assert_in_source(
        body,
        '<nav class="navbar navbar-expand-xxl navbar-dark" aria-label="Primary navigation">',
    )
    assert "navbar-expand-lg" not in body
    assert_in_source(body, "@media (max-width: 1399.98px)")
    assert_in_source(body, ".navbar-nav .nav-item.d-flex")
    assert_in_source(body, ".adoptiq-quit-divider-item,")


def test_analyze_render_inherits_only_the_global_skip_link(client) -> None:
    """Duplicate identical skip links create a confusing first tab stop."""
    assert 'href="#main-content"' not in _analyze()

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.count('href="#main-content"') == 1
    assert html.count("Skip to main content") == 1


def test_every_internal_primary_destination_can_expose_current_page() -> None:
    body = _base()
    for endpoint in ("index", "history", "external_intelligence", "help", "preferences", "ask_ai_page"):
        active_branch = re.search(
            rf"request\.endpoint == ['\"]{endpoint}['\"][\s\S]{{0,180}}aria-current=\"page\"",
            body,
        )
        assert active_branch, f"{endpoint} nav link cannot expose aria-current=page"
