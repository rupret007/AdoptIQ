"""Round 184 — operator finish (offline usability) regression pins."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from data_normalization import format_operator_display_datetime
from leader_scope import is_leader_aggregate_manager

_REPO = Path(__file__).resolve().parents[1]


class TestG1LeaderScopePreview:
    def test_aggregate_manager_is_recognized(self):
        assert is_leader_aggregate_manager("All Managers") is True
        assert is_leader_aggregate_manager("Brian Frazier") is False

    def test_leader_scope_options_all_managers_returns_200_not_roster_error(self, client):
        response = client.get(
            "/api/leader_scope_options",
            query_string={"manager": "All Managers"},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload.get("success") is True
        assert payload.get("members") == []
        assert payload.get("needs_manager_selection") is True

    def test_scope_preview_all_managers_ok(self, client):
        response = client.get(
            "/api/decision-workspace/scope-preview",
            query_string={
                "report_type": "leader",
                "manager": "All Managers",
                "technology": "All",
                "days": 90,
                "scope_type": "team",
            },
        )
        assert response.status_code == 200
        assert response.get_json().get("ok") is True

    def test_start_leader_report_blocks_all_managers(self, client):
        import app_simple

        manager = next(m for m in app_simple.MANAGERS if m != "All Managers")
        # control: real manager should not hit the aggregate gate
        assert manager
        response = client.post(
            "/start_leader_report",
            data={
                "manager": "All Managers",
                "days": 90,
                "scope_type": "team",
                "scope_value": "",
            },
        )
        assert response.status_code == 400
        assert "specific manager" in response.get_json().get("error", "").lower()

    def test_workspace_js_pins_aggregate_guidance(self):
        js = (_REPO / "static/js/manager_decision_workspace.js").read_text(encoding="utf-8")
        assert "isAggregateManager" in js
        assert "Choose your manager to preview the exact scope" in js


class TestG2OperatorDatetime:
    def test_iso_z_renders_friendly_with_raw_preserved_for_title(self):
        assert format_operator_display_datetime("2026-05-10T12:00:00Z") == "May 10, 2026"

    def test_blank_and_invalid_graceful(self):
        assert format_operator_display_datetime("") == ""
        assert format_operator_display_datetime(None) == ""
        assert format_operator_display_datetime("not-a-date") == "not-a-date"

    def test_operator_datetime_filter_registered(self):
        import app_simple

        assert "operator_datetime" in app_simple.app.jinja_env.filters


class TestG3Customer360DeadEnd:
    def test_fixture_corpus_miss_shows_next_steps_without_portfolio_claim(self, client, monkeypatch):
        import app_simple
        import corpus_retriever as cr

        monkeypatch.setattr(app_simple, "_r17_corpus_enabled", lambda: True)
        monkeypatch.setattr(cr, "is_configured", lambda: True)

        def _raise_miss(*_a, **_k):
            raise cr.CorpusUnavailable("customer is not in the local fixture corpus")

        monkeypatch.setattr(cr, "get_customer_history", _raise_miss)

        resp = client.get("/customer/Gamma%20Public%20Sector")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "What you can do next" in body
        assert "Open Ask AI" in body
        assert "Run Analysis" in body
        assert "AdoptIQ Intelligence" in body
        assert "local acceptance corpus" in body.lower()

    def test_unknown_corpus_miss_does_not_claim_fixture_portfolio(self, client, monkeypatch):
        import app_simple
        import corpus_retriever as cr

        monkeypatch.setattr(app_simple, "_r17_corpus_enabled", lambda: True)
        monkeypatch.setattr(cr, "is_configured", lambda: True)

        def _raise_miss(*_a, **_k):
            raise cr.CorpusUnavailable("customer 'Nobody' is not in the corpus")

        monkeypatch.setattr(cr, "get_customer_history", _raise_miss)

        resp = client.get("/customer/Nobody")
        body = resp.get_data(as_text=True)
        assert "may still appear in Snowflake" in body


class TestG4IntelCorpusPill:
    def test_classify_never_returns_unknown(self):
        js = (_REPO / "static/js/intel_status.js").read_text(encoding="utf-8")
        assert "return 'unknown';" not in js.split("function classifyCorpusPanel")[1].split("function corpusPanelLabel")[0]
        assert "status_pending" in js
        assert "POLL_STATUS_TIMEOUT_MS" in js
        assert "__adoptiqIntelStatusPaintFromBoot" in js

    def test_analyze_seeds_intel_boot_json(self):
        html = (_REPO / "templates/analyze.html").read_text(encoding="utf-8")
        assert "adoptiq-intel-boot-json" in html
        assert "__adoptiqIntelStatusPaintFromBoot" in html


class TestG5AskAiEmptyState:
    def test_ask_ai_layout_not_55vh_min(self):
        html = (_REPO / "templates/ask_ai.html").read_text(encoding="utf-8")
        assert "min-height: 55vh" not in html
        assert "r184AskEmptyHint" in html

    def test_ask_ai_js_hides_empty_hint_on_message(self):
        js = (_REPO / "static/js/ask_ai.js").read_text(encoding="utf-8")
        assert "_r184HideAskEmptyHint" in js
