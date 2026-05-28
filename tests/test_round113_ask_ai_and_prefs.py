"""Round 113 / Build 82 -- Ask AI uplift + Preferences fix-and-polish.

One block per plan phase (A1-A5 / B1-B3 / C1-C4).  Phases that are
pure-JS / template surfaces are pinned via source-shape assertions
(the contract the frontend depends on); Python-backed phases get unit
+ endpoint coverage.
"""
import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASK_AI_JS = PROJECT_ROOT / "static" / "js" / "ask_ai.js"
ASK_AI_HTML = PROJECT_ROOT / "templates" / "ask_ai.html"
PREFS_HTML = PROJECT_ROOT / "templates" / "preferences.html"
CSONE_JS = PROJECT_ROOT / "static" / "js" / "r88_csone_folder_override.js"
INTEL_JS = PROJECT_ROOT / "static" / "js" / "intel_status.js"
REPORT_DEFAULTS_JS = PROJECT_ROOT / "static" / "js" / "r113_report_defaults.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase A1 -- conversation history unified across sync + stream.
# ---------------------------------------------------------------------------
class TestA1ConversationHistory:
    def test_apply_history_prepends_prior_turns(self):
        import app_simple
        out = app_simple._r74_apply_conversation_history(
            "What changed this week?",
            [{"q": "How many barriers?", "a": "There are 12 open barriers."}],
        )
        assert "Conversation context" in out
        assert "How many barriers?" in out
        assert out.endswith("What changed this week?")

    def test_apply_history_noop_on_empty(self):
        import app_simple
        assert app_simple._r74_apply_conversation_history("q", []) == "q"
        assert app_simple._r74_apply_conversation_history("q", None) == "q"

    def test_apply_history_drops_malformed_turns(self):
        import app_simple
        out = app_simple._r74_apply_conversation_history(
            "q", ["not-a-dict", 42, {"q": "", "a": ""}]
        )
        # All turns are unusable -> question returned unchanged.
        assert out == "q"

    def test_sync_route_applies_conversation_history(self):
        """The sync /api/ask-ai-portfolio route must call
        _r74_apply_conversation_history (pre-R113 only the stream path
        did)."""
        src = _read(PROJECT_ROOT / "app_simple.py")
        # Find the sync route block and assert it threads history.
        assert "Round 113 / A1" in src
        assert src.count("_r74_apply_conversation_history(question") >= 2, (
            "both sync and stream paths must apply conversation history"
        )


# ---------------------------------------------------------------------------
# Phase A2 -- visible conversation thread (browser-local).
# ---------------------------------------------------------------------------
class TestA2ConversationThread:
    def test_js_renders_thread(self):
        js = _read(ASK_AI_JS)
        assert "_r113RenderConversationThread" in js
        assert "Round 113 / A2" in js

    def test_template_has_thread_container(self):
        html = _read(ASK_AI_HTML)
        assert "Round 113 / A2" in html


# ---------------------------------------------------------------------------
# Phase A3 -- progressive retrieval feedback in the SSE meta event.
# ---------------------------------------------------------------------------
class TestA3RetrievalSummary:
    def test_summary_from_canonical_headline(self):
        import app_simple
        s = app_simple._r113_build_retrieval_summary(
            {"total_barriers": 120, "total_cases": 45, "total_customers": 12}, 0, 0
        )
        assert "Scanned" in s
        assert "120 adoption barriers" in s
        assert "45 support cases" in s
        assert "12 customers" in s
        assert "ranking evidence" in s

    def test_summary_singular_grammar(self):
        import app_simple
        s = app_simple._r113_build_retrieval_summary(
            {"total_barriers": 1, "total_cases": 1, "total_customers": 1}, 0, 0
        )
        assert "1 adoption barrier" in s and "1 adoption barriers" not in s
        assert "1 support case" in s and "1 support cases" not in s
        assert "1 customer" in s and "1 customers" not in s

    def test_summary_falls_back_to_evidence_counts(self):
        import app_simple
        s = app_simple._r113_build_retrieval_summary({}, 8, 3)
        assert "Ranked 8 evidence records" in s
        assert "3 accounts" in s

    def test_summary_malformed_input_no_raise(self):
        import app_simple
        # Must never raise on garbage -- the meta event has to serialise.
        assert isinstance(app_simple._r113_build_retrieval_summary(None, None, None), str)

    def test_stream_meta_event_carries_summary(self):
        src = _read(PROJECT_ROOT / "app_simple.py")
        assert "'retrieval_summary'" in src
        assert "_r113_build_retrieval_summary(" in src


# ---------------------------------------------------------------------------
# Phase A4 -- multi-line textarea input.
# ---------------------------------------------------------------------------
class TestA4Textarea:
    def test_question_input_is_textarea(self):
        html = _read(ASK_AI_HTML)
        # The #aiQuestion control must now be a textarea, not <input>.
        m = re.search(r'<textarea[^>]*id="aiQuestion"', html)
        assert m, "aiQuestion must be a <textarea> (A4)"
        assert 'Shift+Enter' in html or 'Shift-Enter' in html

    def test_no_input_aiquestion(self):
        html = _read(ASK_AI_HTML)
        assert not re.search(r'<input[^>]*id="aiQuestion"', html), (
            "aiQuestion must not be a single-line <input> after A4"
        )


# ---------------------------------------------------------------------------
# Phase A5 -- per-answer copy + download controls.
# ---------------------------------------------------------------------------
class TestA5CopyExport:
    def test_js_copy_and_download_helpers(self):
        js = _read(ASK_AI_JS)
        assert "_r113CopyAnswer" in js
        assert "_r113DownloadAnswer" in js
        assert "Round 113 / A5" in js

    def test_template_has_answer_actions(self):
        html = _read(ASK_AI_HTML)
        assert "Round 113 / A5" in html


# ---------------------------------------------------------------------------
# Phase B1 -- renewal / ARR grounding in CANONICAL_HEADLINE.
# ---------------------------------------------------------------------------
class TestB1RenewalHeadline:
    def test_single_currency(self):
        import ask_ai_grounded as g
        bundle = {
            "enhanced_account_insights": {
                "contracts": {
                    "expiring_within_90d": 7,
                    "expiring_arr": 1234567.0,
                    "expiring_arr_currency": "USD",
                    "is_multi_currency": False,
                },
                "renewals": {"at_risk_total": 3},
            }
        }
        out = g._r113_renewal_headline_fields(bundle)
        assert out["contracts_expiring_90d"] == 7
        assert out["expiring_arr"] == "USD 1,234,567"
        assert out["renewals_at_risk"] == 3
        assert "expiring_arr_by_currency" not in out

    def test_multi_currency_emits_breakdown_not_single_number(self):
        import ask_ai_grounded as g
        bundle = {
            "enhanced_account_insights": {
                "contracts": {
                    "expiring_within_90d": 5,
                    "expiring_arr": None,
                    "is_multi_currency": True,
                    "expiring_arr_by_currency": {"USD": 500000.0, "EUR": 250000.0},
                }
            }
        }
        out = g._r113_renewal_headline_fields(bundle)
        # Must NOT fabricate a single misleading ARR number.
        assert "expiring_arr" not in out
        assert "expiring_arr_by_currency" in out
        assert "USD 500,000" in out["expiring_arr_by_currency"]
        assert "EUR 250,000" in out["expiring_arr_by_currency"]

    def test_malformed_bundle_returns_empty(self):
        import ask_ai_grounded as g
        assert g._r113_renewal_headline_fields(None) == {}
        assert g._r113_renewal_headline_fields({}) == {}
        assert g._r113_renewal_headline_fields({"enhanced_account_insights": "nope"}) == {}

    def test_bool_is_not_treated_as_number(self):
        import ask_ai_grounded as g
        bundle = {
            "enhanced_account_insights": {
                "contracts": {"expiring_within_90d": True},
                "renewals": {"at_risk_total": False},
            }
        }
        out = g._r113_renewal_headline_fields(bundle)
        assert "contracts_expiring_90d" not in out
        assert "renewals_at_risk" not in out

    def test_headline_merge_wired_in_composer(self):
        src = _read(PROJECT_ROOT / "ask_ai_grounded.py")
        assert "_r113_renewal_headline_fields(bundle)" in src
        # Must only fill gaps -- never clobber SSoT portfolio metrics.
        assert "if _r113_k not in canonical_headline" in src


# ---------------------------------------------------------------------------
# Phase B2 -- live suggestion chips (bounded in-memory per-scope cache).
# ---------------------------------------------------------------------------
class TestB2TopRiskCache:
    def _clear(self):
        import ask_ai_grounded as g
        with g._R113_TOP_RISK_CACHE_LOCK:
            g._R113_TOP_RISK_CACHE.clear()

    def test_stamp_and_read_roundtrip(self):
        import ask_ai_grounded as g
        self._clear()
        profiles = {
            "Acme": {"risk_score_0_100": 90.0},
            "Beta": {"risk_score_0_100": 30.0},
            "Gamma": {"risk_score_0_100": 60.0},
        }
        g._r113_stamp_top_risk_customers("Mgr", "All", 90, profiles)
        top = g.get_top_risk_customers_for_scope("Mgr", "All", 90, top_n=2)
        assert top == ["Acme", "Gamma"]  # sorted by score DESC

    def test_cold_cache_returns_empty(self):
        import ask_ai_grounded as g
        self._clear()
        assert g.get_top_risk_customers_for_scope("Nobody", "x", 90) == []

    def test_scope_key_normalisation(self):
        import ask_ai_grounded as g
        self._clear()
        g._r113_stamp_top_risk_customers("Mgr", "All", 90, {"X": {"risk_score_0_100": 5}})
        # Case / whitespace insensitive on read.
        assert g.get_top_risk_customers_for_scope(" mgr ", "ALL", 90, top_n=1) == ["X"]

    def test_empty_profiles_no_stamp(self):
        import ask_ai_grounded as g
        self._clear()
        g._r113_stamp_top_risk_customers("Mgr", "All", 90, {})
        g._r113_stamp_top_risk_customers("Mgr", "All", 90, None)
        assert g.get_top_risk_customers_for_scope("Mgr", "All", 90) == []

    def test_cache_is_bounded_fifo(self):
        import ask_ai_grounded as g
        self._clear()
        cap = g._R113_TOP_RISK_CACHE_MAX
        for i in range(cap + 10):
            g._r113_stamp_top_risk_customers(f"M{i}", "All", 90, {"C": {"risk_score_0_100": 1}})
        with g._R113_TOP_RISK_CACHE_LOCK:
            assert len(g._R113_TOP_RISK_CACHE) <= cap
        # Earliest scopes evicted.
        assert g.get_top_risk_customers_for_scope("M0", "All", 90) == []

    def test_composite_risk_fallback(self):
        import ask_ai_grounded as g
        self._clear()
        profiles = {
            "Lo": {"composite_risk": 10.0},
            "Hi": {"composite_risk": 80.0},
        }
        g._r113_stamp_top_risk_customers("M", "All", 90, profiles)
        assert g.get_top_risk_customers_for_scope("M", "All", 90, top_n=1) == ["Hi"]

    def test_suggestions_endpoint_warm_names_real_customer(self, client):
        import ask_ai_grounded as g
        self._clear()
        g._r113_stamp_top_risk_customers("Brian Frazier", "All", 90,
                                         {"WORLD BANK GROUP": {"risk_score_0_100": 99}})
        resp = client.get(
            "/api/ask-ai/suggestions?manager=Brian+Frazier&technology=All&days=90"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        blob = json.dumps(data["suggestions"])
        assert "WORLD BANK GROUP" in blob

    def test_suggestions_endpoint_cold_still_returns_chips(self, client):
        self._clear()
        resp = client.get(
            "/api/ask-ai/suggestions?manager=Nobody&technology=All&days=90"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["suggestions"], list)
        assert len(data["suggestions"]) >= 1  # template fallback never blank


# ---------------------------------------------------------------------------
# Phase B3 -- customer drill-through (XSS-safe).
# ---------------------------------------------------------------------------
class TestB3CustomerDrillthrough:
    def test_js_links_customer_to_customer360_route(self):
        js = _read(ASK_AI_JS)
        assert "'/customer/' + encodeURIComponent" in js
        assert "Round 113 / B3" in js

    def test_js_uses_textcontent_not_innerhtml_for_customer(self):
        js = _read(ASK_AI_JS)
        # The customer pill/badge must be set via textContent (XSS-safe).
        assert "custPill.textContent" in js
        assert "custBadge.textContent" in js

    def test_customer360_route_exists(self, client):
        # The /customer/<name> route must be registered (drill-through target).
        import app_simple
        rules = [r.rule for r in app_simple.app.url_map.iter_rules()]
        assert any(r.startswith("/customer/") for r in rules)


# ---------------------------------------------------------------------------
# Phase C1 -- CSOne folder card display bug.
# ---------------------------------------------------------------------------
class TestC1CsoneFolderCard:
    def test_js_reads_folder_path(self):
        js = _read(CSONE_JS)
        assert "payload.folder_path" in js
        assert "Round 113 / C1" in js

    def test_get_endpoint_returns_folder_path(self, client):
        resp = client.get("/api/settings/csone-onedrive-folder")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        # The card depends on this exact key.
        assert "folder_path" in data


# ---------------------------------------------------------------------------
# Phase C2 -- Intelligence-toggle feedback on Preferences.
# ---------------------------------------------------------------------------
class TestC2IntelFeedback:
    def test_prefs_has_standalone_feedback_target(self):
        html = _read(PREFS_HTML)
        assert "data-intel-banner-summary" in html

    def test_intel_js_targets_standalone_summary(self):
        js = _read(INTEL_JS)
        # When the full banner is absent, fall back to a standalone summary.
        assert "document.querySelector('[data-intel-banner-summary]')" in js
        assert "Round 113 / C2" in js


# ---------------------------------------------------------------------------
# Phase C3 -- persisted default scope.
# ---------------------------------------------------------------------------
class TestC3DefaultScopeValidators:
    def test_default_days_validator(self):
        import adoptiq_settings as s
        assert s.is_valid_default_days(0) is True       # unset sentinel
        assert s.is_valid_default_days(90) is True
        assert s.is_valid_default_days(1) is True
        assert s.is_valid_default_days(365) is True
        assert s.is_valid_default_days(366) is False
        assert s.is_valid_default_days(-5) is False
        assert s.is_valid_default_days("abc") is False

    def test_default_scope_str_validator(self):
        import adoptiq_settings as s
        assert s.is_valid_default_scope_str("") is True            # unset
        assert s.is_valid_default_scope_str("Brian Frazier") is True
        assert s.is_valid_default_scope_str("All") is True
        # Shell-meta / control chars rejected.
        assert s.is_valid_default_scope_str("foo; rm -rf /") is False
        assert s.is_valid_default_scope_str("a" * 201) is False
        assert s.is_valid_default_scope_str("x`whoami`") is False

    def test_schema_carries_default_keys(self):
        import adoptiq_settings as s
        for key in ("default_days", "default_manager", "default_technology"):
            assert key in s._SCHEMA


class TestC3DefaultScopeResolver:
    def test_resolver_drops_stale_manager(self, monkeypatch):
        import app_simple
        import adoptiq_settings as s
        monkeypatch.setattr(
            s, "load_settings",
            lambda: {
                "default_days": 180,
                "default_manager": "Ghost Manager Not On Roster",
                "default_technology": "All",
            },
        )
        out = app_simple._r113_resolve_report_defaults()
        assert out["default_days"] == 180
        # Stale manager dropped to "" on read.
        assert out["default_manager"] == ""
        # Raw persisted value preserved for the UI hint.
        assert out["persisted"]["default_manager"] == "Ghost Manager Not On Roster"

    def test_resolver_keeps_valid_manager(self, monkeypatch):
        import app_simple
        import adoptiq_settings as s
        valid_mgr = list(app_simple.MANAGERS)[0]
        monkeypatch.setattr(
            s, "load_settings",
            lambda: {"default_days": 0, "default_manager": valid_mgr,
                     "default_technology": ""},
        )
        out = app_simple._r113_resolve_report_defaults()
        assert out["default_manager"] == valid_mgr
        assert out["default_days"] == 0

    def test_resolver_never_raises(self, monkeypatch):
        import app_simple
        import adoptiq_settings as s

        def _boom():
            raise RuntimeError("settings unreadable")

        monkeypatch.setattr(s, "load_settings", _boom)
        out = app_simple._r113_resolve_report_defaults()
        assert out["default_manager"] == ""
        assert out["default_technology"] == ""
        assert out["default_days"] == 0


class TestC3DefaultScopeEndpoint:
    def test_get_returns_managers_and_technologies(self, client, monkeypatch):
        import adoptiq_settings as s
        monkeypatch.setattr(s, "load_settings", lambda: {})
        resp = client.get("/api/settings/report-defaults")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["managers"], list)
        assert isinstance(data["technologies"], list)
        assert "persisted" in data

    def test_post_rejects_bad_days(self, client, monkeypatch):
        import adoptiq_settings as s
        monkeypatch.setattr(s, "load_settings", lambda: {})
        resp = client.post(
            "/api/settings/report-defaults",
            json={"default_days": 9999},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_default_days"

    def test_post_rejects_injection_in_manager(self, client, monkeypatch):
        import adoptiq_settings as s
        monkeypatch.setattr(s, "load_settings", lambda: {})
        resp = client.post(
            "/api/settings/report-defaults",
            json={"default_manager": "foo; rm -rf /"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_default_manager"

    def test_post_persists_valid_scope(self, client, monkeypatch):
        import adoptiq_settings as s
        store = {}
        monkeypatch.setattr(s, "load_settings", lambda: dict(store))

        def _save(d):
            store.clear()
            store.update(d)

        monkeypatch.setattr(s, "save_settings", _save)
        resp = client.post(
            "/api/settings/report-defaults",
            json={"default_days": 180, "default_manager": "", "default_technology": ""},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert store["default_days"] == 180

    def test_report_defaults_card_js_exists(self):
        assert REPORT_DEFAULTS_JS.exists()
        js = _read(REPORT_DEFAULTS_JS)
        assert "/api/settings/report-defaults" in js
        # CSRF token on the mutating request.
        assert "X-CSRFToken" in js

    def test_prefs_template_has_defaults_card(self):
        html = _read(PREFS_HTML)
        assert "data-report-defaults-card" in html
        assert "r113_report_defaults.js" in html


# ---------------------------------------------------------------------------
# Phase C4 -- guard intel polling to banner/panel pages.
# ---------------------------------------------------------------------------
class TestC4PollGuard:
    def test_poll_surface_guard_present(self):
        js = _read(INTEL_JS)
        assert "_intelPollSurfacePresent" in js
        assert "Round 113 / C4" in js

    def test_guard_checks_banner_and_panel(self):
        js = _read(INTEL_JS)
        # The guard must look for the banner OR the sharepoint panel.
        block = js[js.index("_intelPollSurfacePresent"):]
        assert "[data-intel-banner]" in block
        assert "[data-sharepoint-panel]" in block
