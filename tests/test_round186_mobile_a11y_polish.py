"""Round 186 — offline mobile + accessibility polish regression pins."""

from __future__ import annotations

from pathlib import Path

from data_normalization import format_operator_display_datetime
from source_shape_utils import assert_in_source, assert_not_in_source

_REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


class TestG4CorpusPillSsrSeed:
    def test_analyze_template_never_seeds_checking(self):
        html = _read("templates/analyze.html")
        assert_not_in_source(html, "checking&hellip;", label="analyze.html")
        assert_not_in_source(html, "self_healed_baked", label="analyze.html")
        assert_in_source(html, "data-r186-ssr-corpus-pill", label="analyze.html")
        assert_in_source(html, "Status pending", label="analyze.html")
        assert_in_source(html, "Active &bull; local corpus", label="analyze.html")
        assert_in_source(html, 'aria-live="polite"', label="analyze.html")

    def test_home_fixture_payload_seeds_active_not_checking(self, client, monkeypatch):
        import app_simple

        monkeypatch.setitem(app_simple.app.config, "LOCAL_ACCEPTANCE_MODE", True)
        monkeypatch.setattr(
            app_simple,
            "_r17_corpus_status_payload",
            lambda: app_simple._r185_annotate_intel_boot(
                {
                    "ok": True,
                    "enabled": True,
                    "available": True,
                    "boot": {
                        "in_progress": False,
                        "last_finished_at": "2026-08-03T21:00:00Z",
                        "source": "local_acceptance_fixture",
                    },
                }
            ),
        )
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        pill = body.split("data-sharepoint-state-text", 1)[1][:180]
        assert "checking" not in pill.lower()
        assert "Active" in pill
        assert "local corpus" in pill


class TestE2ExternalIntelFriendlyDates:
    def test_truncated_iso_without_z_is_friendly(self):
        assert format_operator_display_datetime("2026-07-16T14:00") == "July 16, 2026"
        assert format_operator_display_datetime("2026-07-29T09:00") == "July 29, 2026"

    def test_incident_and_maintenance_rows_use_operator_datetime(self):
        html = _read("templates/external_intelligence.html")
        assert_in_source(html, "inc.published|operator_datetime", label="extintel")
        assert_in_source(html, "m.published|operator_datetime", label="extintel")
        assert_not_in_source(html, "inc.published[:16]", label="extintel")
        assert_not_in_source(html, "m.published[:16]", label="extintel")
        assert_not_in_source(html, "newest[:16]", label="extintel")


class TestA11yDuplicateMainAndSkip:
    def test_templates_do_not_restamp_main_content_id(self):
        analyze = _read("templates/analyze.html")
        previous = _read("templates/previous_reports.html")
        base = _read("templates/base.html")
        assert_not_in_source(analyze, '<h1 id="main-content"', label="analyze.html")
        assert_not_in_source(previous, 'id="main-content"', label="previous_reports.html")
        assert_in_source(base, '<main id="main-content" tabindex="-1">', label="base.html")

    def test_home_and_previous_have_one_main_content_id(self, client):
        for path in ("/", "/previous-reports"):
            resp = client.get(path)
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert body.count('id="main-content"') == 1, path
            assert 'lang="en"' in body
            assert "adoptiq-skip-link" in body


class TestA11yNamedControls:
    def test_ask_ai_question_has_visible_hidden_label(self):
        html = _read("templates/ask_ai.html")
        assert_in_source(html, 'for="aiQuestion"', label="ask_ai.html")
        assert_in_source(html, "Ask a question about your portfolio", label="ask_ai.html")
        assert_in_source(html, 'aria-label="Hide recent questions"', label="ask_ai.html")
        assert_in_source(html, 'aria-expanded="true"', label="ask_ai.html")

    def test_ask_ai_js_toggles_aria_expanded(self):
        js = _read("static/js/ask_ai.js")
        assert_in_source(js, "setAttribute('aria-expanded'", label="ask_ai.js")
        assert_in_source(js, "Show recent questions", label="ask_ai.js")

    def test_external_intel_inputs_have_names(self):
        html = _read("templates/external_intelligence.html")
        assert_in_source(html, 'for="aiQuestion"', label="extintel")
        assert_in_source(html, 'aria-label="Import intelligence JSON file"', label="extintel")

    def test_flash_close_button_has_accessible_name(self):
        html = _read("templates/base.html")
        assert_in_source(
            html,
            'class="btn-close" data-bs-dismiss="alert" aria-label="Close"',
            label="base.html",
        )


class TestMobileSharedCss:
    def test_base_tables_scroll_and_tap_targets(self):
        html = _read("templates/base.html")
        # Round 186.1: wide tables must scroll inside .table-responsive.
        start = html.index(".table-responsive")
        block = html[start : html.index("}", start) + 1]
        assert "overflow-x: auto;" in block, block
        assert_not_in_source(html, "overflow-x: clip", label="base.html")
        assert_in_source(html, "overflow-wrap: break-word;", label="base.html")
        assert_in_source(html, "flex-shrink: 0;", label="base.html")
        assert_in_source(html, "min-height: 44px;", label="base.html")
        assert_in_source(html, "min-height: 32px;", label="base.html")
        assert_in_source(html, "@media (max-width: 575.98px)", label="base.html")
        assert_in_source(html, ".table-responsive", label="base.html")
        assert_in_source(html, "Round 186", label="base.html")
