"""Round 185 — offline operator polish regression pins."""

from __future__ import annotations

from pathlib import Path

import pytest

from source_shape_utils import assert_in_source

_REPO = Path(__file__).resolve().parents[1]


class TestG6FixtureLastFinishedLabel:
    def test_fixture_snapshot_uses_friendly_date_not_raw_iso(self):
        import app_simple

        fields = app_simple._r185_intel_last_finished_fields(
            {
                "last_finished_at": "2026-08-03T21:00:00Z",
                "source": "local_acceptance_fixture",
            }
        )
        assert fields["last_finished_at_display"] == "August 3, 2026"
        assert fields["fixture_snapshot"] is True
        assert fields["last_finished_summary"] == (
            "Fixture snapshot dated August 3, 2026 — not a live index run."
        )
        assert "2026-08-03T21:00:00Z" not in fields["last_finished_summary"]

    def test_live_boot_keeps_last_run_finished_stem(self, monkeypatch):
        import app_simple

        monkeypatch.setitem(app_simple.app.config, "LOCAL_ACCEPTANCE_MODE", False)
        fields = app_simple._r185_intel_last_finished_fields(
            {
                "last_finished_at": "2026-08-03T21:00:00Z",
                "source": "fresh",
            }
        )
        assert fields["fixture_snapshot"] is False
        assert fields["last_finished_summary"] == "Last run finished August 3, 2026."

    def test_empty_boot_has_no_summary(self):
        import app_simple

        fields = app_simple._r185_intel_last_finished_fields({})
        assert fields["last_finished_summary"] is None
        assert fields["last_finished_at_display"] is None

    def test_home_renders_fixture_summary_not_raw_iso(self, client, monkeypatch):
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
        assert_in_source(body, "Fixture snapshot dated August 3, 2026", label="home")
        assert "Last run finished 2026-08-03T21:00:00Z" not in body

    def test_js_and_template_use_summary_field(self):
        js = (_REPO / "static/js/intel_status.js").read_text(encoding="utf-8")
        html = (_REPO / "templates/analyze.html").read_text(encoding="utf-8")
        assert_in_source(js, "last_finished_summary", label="intel_status.js")
        assert_in_source(js, "Fixture snapshot dated", label="intel_status.js")
        assert_in_source(html, "intel_status.boot.last_finished_summary", label="analyze.html")


class TestHistoryEmptyState:
    def test_empty_history_keeps_next_step_in_first_viewport(self, client, monkeypatch):
        import app_simple

        monkeypatch.setattr(app_simple, "get_report_history", lambda: [])
        resp = client.get("/history")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert_in_source(body, "data-r185-history-empty", label="history")
        assert_in_source(body, "No Analysis History Found", label="history")
        assert_in_source(body, "Run Your First Analysis", label="history")
        assert_in_source(body, "document.querySelector('.analysis-row')", label="history")

    def test_history_js_does_not_inject_search_without_rows(self):
        html = (_REPO / "templates/history.html").read_text(encoding="utf-8")
        assert_in_source(html, "document.querySelector('.analysis-row')", label="history.html")


class TestPreviousReportsEmptyCopy:
    def test_unfiltered_empty_is_not_a_filter_miss(self):
        js = (_REPO / "static/js/report_history_workspace.js").read_text(encoding="utf-8")
        assert_in_source(
            js,
            "No reports have been generated in this session yet.",
            label="report_history_workspace.js",
        )
        assert_in_source(js, "No reports match these filters.", label="report_history_workspace.js")
        assert_in_source(js, "hasFilters", label="report_history_workspace.js")


class TestAskAiFixtureLoadingCopy:
    def test_fixture_loading_does_not_claim_live_snowflake(self, monkeypatch):
        import app_simple

        monkeypatch.setitem(app_simple.app.config, "LOCAL_ACCEPTANCE_MODE", True)
        resp = app_simple.app.test_client().get("/ask-ai")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert_in_source(body, "data-r185-ask-ai-loading", label="ask-ai")
        assert_in_source(body, "Loading fixture portfolio", label="ask-ai")
        assert "Fetching live data from Snowflake" not in body
        assert "Fetch portfolio data (Snowflake)" not in body


class TestExternalIntelFixtureRefresh:
    def test_template_has_fixture_and_live_refresh_labels(self):
        html = (_REPO / "templates/external_intelligence.html").read_text(encoding="utf-8")
        assert_in_source(html, "Reload fixture data", label="extintel")
        assert_in_source(html, "data-r185-refresh-label", label="extintel")
        assert_in_source(html, "Refresh Live Data", label="extintel")
        assert_in_source(html, "fixture snapshot", label="extintel")
        assert_in_source(html, "LOCAL_ACCEPTANCE_MODE", label="extintel")


class TestPlaybookEmptyState:
    def test_playbook_get_shows_search_next_step_when_corpus_on(self, client, monkeypatch):
        import app_simple

        monkeypatch.setattr(app_simple, "_r17_corpus_enabled", lambda: True)
        resp = client.get("/playbook")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert_in_source(body, "data-r185-playbook-empty", label="playbook")
        assert_in_source(body, "Search the playbook to see results", label="playbook")
        assert_in_source(body, "SBC handshake failure", label="playbook")


class TestC360UnknownVsPortfolioMiss:
    def test_adapter_distinguishes_unknown_from_portfolio_miss(self):
        src = (_REPO / "local_acceptance_runtime.py").read_text(encoding="utf-8")
        assert_in_source(src, "customer is not in the corpus", label="adapter")
        assert_in_source(src, "known_customers", label="adapter")
        assert_in_source(src, "Round 185", label="adapter")

    def test_unknown_customer_copy_mentions_snowflake_possibility(self, client, monkeypatch):
        import app_simple
        import corpus_retriever as cr

        monkeypatch.setattr(app_simple, "_r17_corpus_enabled", lambda: True)
        monkeypatch.setattr(cr, "is_configured", lambda: True)

        def _raise_miss(*_a, **_k):
            raise cr.CorpusUnavailable("customer is not in the corpus")

        monkeypatch.setattr(cr, "get_customer_history", _raise_miss)
        resp = client.get("/customer/Nobody%20Corp")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert_in_source(body, "may still appear in Snowflake", label="c360-unknown")
        assert "local acceptance corpus" not in body
