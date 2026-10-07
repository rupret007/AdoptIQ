"""Round 187 — leftover a11y + real 390 CSS-px + keyboard operator polish."""

from __future__ import annotations

from pathlib import Path

from source_shape_utils import assert_in_source, assert_not_in_source

_REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


class TestQuitAndFooterHeadingOrder:
    def test_quit_confirm_title_is_not_a_heading(self):
        html = _read("templates/base.html")
        assert_not_in_source(
            html,
            '<h5 class="modal-title" id="adoptiq-quit-confirm-title">',
            label="base.html",
        )
        assert_in_source(
            html,
            '<p class="modal-title h5 mb-0" id="adoptiq-quit-confirm-title">',
            label="base.html",
        )
        assert_in_source(html, "Round 187", label="base.html")

    def test_footer_site_name_is_h2_not_h5(self):
        html = _read("templates/base.html")
        footer = html.split("<footer", 1)[1]
        assert "<h5" not in footer.split("</footer>", 1)[0]
        assert_in_source(footer, '<h2 class="h5 mb-3">', label="footer")
        assert_in_source(footer, "AdoptIQ Portfolio Analyzer", label="footer")

    def test_home_heading_outline_skips_hidden_quit_h5(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'id="adoptiq-quit-confirm-title"' in body
        assert "<h5" not in body.split('id="adoptiq-quit-confirm-modal"', 1)[1].split(
            "</div>", 8
        )[0]
        assert 'id="adoptiq-quit-confirm-title">Analyses still running</p>' in body


class TestPrefsSsrPending:
    def test_preferences_template_never_seeds_checking(self):
        html = _read("templates/preferences.html")
        assert_not_in_source(html, "checking&hellip;", label="preferences.html")
        assert ">checking" not in html.lower()
        assert_in_source(html, "Status pending", label="preferences.html")
        assert_in_source(html, "data-r187-ssr-pending", label="preferences.html")

    def test_preferences_render_seeds_pending_not_checking(self, client):
        resp = client.get("/preferences")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        pills = body.split("data-corpus-share-url-source-text", 1)[1][:80]
        assert "checking" not in pills.lower()
        assert "Status pending" in pills
        assert "checking" not in body.lower()


class TestMobile390Contracts:
    def test_viewport_and_hamburger_room(self):
        html = _read("templates/base.html")
        assert_in_source(
            html,
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            label="base.html",
        )
        assert_in_source(
            html,
            '<nav class="navbar navbar-expand-xxl navbar-dark" aria-label="Primary navigation">',
            label="base.html",
        )
        assert_in_source(html, "min-width: 44px;", label="base.html")
        assert_in_source(html, "max-width: calc(100% - 3.5rem);", label="base.html")
        assert_not_in_source(html, "overflow-x: clip", label="base.html")

    def test_tables_scroll_in_wrapper_not_page(self):
        html = _read("templates/base.html")
        start = html.index(".table-responsive")
        block = html[start : html.index("}", start) + 1]
        assert "overflow-x: auto;" in block
        assert_in_source(html, "html, body {", label="base.html")
        assert_in_source(html, "max-width: 100%;", label="base.html")

    def test_report_grid_is_single_column_on_phone(self):
        css = _read("static/css/manager_decision_workspace.css")
        assert_in_source(css, "minmax(min(100%, 12rem), 1fr)", label="workspace css")
        assert_in_source(css, "@media (max-width: 575.98px)", label="workspace css")
        assert_in_source(css, "grid-template-columns: 1fr;", label="workspace css")
        assert_in_source(css, "@media (min-width: 1400px)", label="workspace css")
        assert_in_source(css, "Round 187", label="workspace css")
        five_at = css.index("repeat(5, minmax(0, 1fr))")
        assert "@media (min-width: 1400px)" in css[max(0, five_at - 280) : five_at]


class TestKeyboardOperatorFlow:
    def test_skip_link_is_first_and_focus_visible(self):
        html = _read("templates/base.html")
        body = html.split("<body>", 1)[1]
        skip_idx = body.find("adoptiq-skip-link")
        nav_idx = body.find("<nav ")
        assert 0 <= skip_idx < nav_idx
        assert_in_source(html, "a:focus-visible", label="base.html")
        assert_in_source(html, ".adoptiq-skip-link:focus-visible", label="base.html")
        assert_in_source(html, "Skip to main content", label="base.html")

    def test_generate_and_ask_controls_are_named(self):
        analyze = _read("templates/analyze.html")
        ask = _read("templates/ask_ai.html")
        assert_in_source(analyze, 'id="submitBtn"', label="analyze.html")
        assert_in_source(analyze, "Generate Report", label="analyze.html")
        assert_in_source(ask, 'id="askBtn"', label="ask_ai.html")
        assert_in_source(ask, 'aria-hidden="true"', label="ask_ai.html")
        assert_in_source(ask, 'for="aiQuestion"', label="ask_ai.html")

    def test_home_and_preferences_have_one_skip_link(self, client):
        for path in ("/", "/preferences", "/ask-ai", "/history"):
            resp = client.get(path)
            assert resp.status_code == 200, path
            body = resp.get_data(as_text=True)
            assert body.count("Skip to main content") == 1, path
            assert body.count('href="#main-content"') == 1, path


class TestHistoryHeadingOrder:
    def test_previous_analyses_is_h2(self):
        html = _read("templates/history.html")
        assert_not_in_source(html, '<h5 class="mb-0">', label="history.html")
        assert_in_source(html, '<h2 class="h5 mb-0">', label="history.html")
        assert_in_source(html, "Previous Analyses", label="history.html")
