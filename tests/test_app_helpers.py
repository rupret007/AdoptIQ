"""
Tests for app-level helper functions in app_simple.py.
Covers _categorize_technology, _build_insights_payload,
_has_customer_activity_for_deep_dive, filter_subscriptions_by_criteria,
_clean_datetime_columns_for_excel (Round 2 Fix 2 regression).
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np
import pytest
from app_simple import (
    _categorize_technology,
    _build_insights_payload,
    _has_customer_activity_for_deep_dive,
    _is_valid_analysis_id,
    filter_subscriptions_by_criteria,
    _clean_datetime_columns_for_excel,
    app,
    INSIGHT_SUMMARY_MAX_CHARS,
)


# ── _categorize_technology ───────────────────────────────────────────────

class TestCategorizeTechnology:
    def test_webex_meetings(self):
        assert _categorize_technology("Webex Meetings") == "Webex Meetings"

    def test_meetings_keyword(self):
        assert _categorize_technology("Enterprise Meetings Package") == "Webex Meetings"

    def test_webex_calling(self):
        assert _categorize_technology("Webex Calling") == "Webex Calling"

    def test_calling_keyword(self):
        assert _categorize_technology("Cloud Calling Service") == "Webex Calling"

    def test_wxcc(self):
        assert _categorize_technology("Webex Contact Center") == "Webex Contact Center"

    def test_wxcce(self):
        assert _categorize_technology("Webex Contact Center Enterprise") == "Webex Contact Center Enterprise"

    def test_ucce(self):
        assert _categorize_technology("Cisco UCCE") == "Cisco UCCE"

    def test_uccx(self):
        assert _categorize_technology("Cisco UCCX") == "Cisco UCCX"

    def test_contact_center_generic(self):
        assert _categorize_technology("Contact Center") == "Contact Center"

    def test_messaging(self):
        assert _categorize_technology("Messaging Platform") == "Messaging"

    def test_devices(self):
        assert _categorize_technology("Desk Pro Device") == "Devices"

    def test_video(self):
        assert _categorize_technology("Room Kit Video") == "Video"

    def test_other(self):
        assert _categorize_technology("Something Unknown") == "Other"

    def test_case_insensitive(self):
        assert _categorize_technology("WEBEX MEETINGS") == "Webex Meetings"

    def test_wxcc_vs_enterprise_disambiguation(self):
        assert _categorize_technology("Webex Contact Center Enterprise") == "Webex Contact Center Enterprise"
        assert _categorize_technology("Webex Contact Center") == "Webex Contact Center"


class TestAnalysisIdValidation:
    def test_valid_analysis_id(self):
        assert _is_valid_analysis_id("analysis_123-ABC.def") is True

    def test_invalid_analysis_id(self):
        assert _is_valid_analysis_id("../etc/passwd") is False
        assert _is_valid_analysis_id("bad id with space") is False
        assert _is_valid_analysis_id("") is False


class TestSecretKeyDefaults:
    def test_app_secret_key_is_not_static_dev_literal(self):
        assert app.config["SECRET_KEY"] != "adoptiq-secret-key-2024-dev-change-in-production"


# ── _build_insights_payload ──────────────────────────────────────────────

class TestBuildInsightsPayload:
    def test_message_from_status(self):
        status = {"message": "Report completed successfully"}
        payload = _build_insights_payload(status, "fallback")
        assert payload["summary_line"] == "Report completed successfully"

    def test_fallback_when_no_message(self):
        payload = _build_insights_payload({}, "fallback text")
        assert payload["summary_line"] == "fallback text"

    def test_truncation(self):
        long_msg = "X" * (INSIGHT_SUMMARY_MAX_CHARS + 50)
        status = {"message": long_msg}
        payload = _build_insights_payload(status, "fallback")
        assert len(payload["summary_line"]) == INSIGHT_SUMMARY_MAX_CHARS

    def test_extra_fields_merged(self):
        payload = _build_insights_payload({}, "fb", {"risk_theme": "HIGH", "count": 5})
        assert payload["risk_theme"] == "HIGH"
        assert payload["count"] == 5

    def test_no_extra_fields(self):
        payload = _build_insights_payload({}, "fb")
        assert "summary_line" in payload
        assert len(payload) == 1


# ── _has_customer_activity_for_deep_dive ─────────────────────────────────

class TestHasCustomerActivity:
    def test_all_empty(self):
        e = pd.DataFrame()
        assert _has_customer_activity_for_deep_dive(e, e, e, e, e, e) is False

    def test_only_ab_data(self):
        e = pd.DataFrame()
        ab = pd.DataFrame([{"ID": "AB001"}])
        assert _has_customer_activity_for_deep_dive(ab, e, e, e, e, e) is True

    def test_only_csone_data(self):
        e = pd.DataFrame()
        csone = pd.DataFrame([{"SR Number": "TAC001"}])
        assert _has_customer_activity_for_deep_dive(e, csone, e, e, e, e) is True

    def test_only_csconsole_barriers(self):
        e = pd.DataFrame()
        csconsole_ab = pd.DataFrame([{"ACCOUNT_ID_C": "001"}])
        assert _has_customer_activity_for_deep_dive(e, e, e, e, e, csconsole_ab) is True

    def test_none_inputs(self):
        assert _has_customer_activity_for_deep_dive(None, None, None, None, None, None) is False

    def test_action_plans_only(self):
        e = pd.DataFrame()
        ap = pd.DataFrame([{"plan": "Plan A"}])
        assert _has_customer_activity_for_deep_dive(e, e, ap, e, e, e) is True


# ── filter_subscriptions_by_criteria ─────────────────────────────────────

class TestFilterSubscriptions:
    @pytest.fixture
    def team_df(self):
        return pd.DataFrame({
            "BU_NAME": ["Acme Corp", "Acme Corp UK", "Beta Inc"],
            "ACCOUNT_ID_C": ["A1", "A2", "B1"],
            "SUBSCRIPTION_ID": ["Sub123", "Sub456", "Sub789"],
        })

    def test_no_filter(self, team_df):
        out, err = filter_subscriptions_by_criteria(team_df, None, None)
        assert err is None
        assert len(out) == 3

    def test_filter_by_customer_name(self, team_df):
        out, err = filter_subscriptions_by_criteria(team_df, "acme", None)
        assert err is None
        assert len(out) == 2
        assert set(out["BU_NAME"]) == {"Acme Corp", "Acme Corp UK"}

    def test_filter_by_subscription_id(self, team_df):
        out, err = filter_subscriptions_by_criteria(team_df, None, "Sub456")
        assert err is None
        assert len(out) == 1
        assert out["SUBSCRIPTION_ID"].iloc[0] == "Sub456"

    def test_customer_name_no_match(self, team_df):
        out, err = filter_subscriptions_by_criteria(team_df, "NoMatch", None)
        assert err is not None
        assert "No subscriptions found" in err
        assert out.empty

    def test_subscription_id_no_match(self, team_df):
        out, err = filter_subscriptions_by_criteria(team_df, None, "Sub999")
        assert err is not None
        assert out.empty

    def test_invalid_subscription_id_format(self, team_df):
        out, err = filter_subscriptions_by_criteria(team_df, None, "Sub123;DROP")
        assert err is not None
        assert "Invalid subscription ID format" in err
        assert out.empty

    def test_empty_team_subs(self):
        out, err = filter_subscriptions_by_criteria(pd.DataFrame(), None, None)
        assert err is not None
        assert "No subscriptions available" in err

    def test_customer_takes_precedence(self, team_df):
        out, err = filter_subscriptions_by_criteria(team_df, "Beta", "Sub123")
        assert err is None
        assert len(out) == 1
        assert out["BU_NAME"].iloc[0] == "Beta Inc"


# ── _clean_datetime_columns_for_excel (Round 2 Fix 2 regression) ─────────

class TestCleanDatetimeColumnsForExcel:
    def test_tz_aware_stripped(self):
        df = pd.DataFrame({
            "created": pd.to_datetime(["2026-01-01", "2026-02-01"]).tz_localize("UTC"),
            "name": ["A", "B"],
        })
        result = _clean_datetime_columns_for_excel(df)
        assert result["created"].dt.tz is None

    def test_tz_naive_unchanged(self):
        df = pd.DataFrame({
            "created": pd.to_datetime(["2026-01-01", "2026-02-01"]),
            "name": ["A", "B"],
        })
        result = _clean_datetime_columns_for_excel(df)
        assert result["created"].dt.tz is None
        assert len(result) == 2

    def test_mixed_columns(self):
        df = pd.DataFrame({
            "tz_col": pd.to_datetime(["2026-01-01"]).tz_localize("US/Eastern"),
            "naive_col": pd.to_datetime(["2026-01-01"]),
            "text_col": ["hello"],
        })
        result = _clean_datetime_columns_for_excel(df)
        assert result["tz_col"].dt.tz is None
        assert result["naive_col"].dt.tz is None
        assert result["text_col"].iloc[0] == "hello"

    def test_none_returns_empty_df(self):
        result = _clean_datetime_columns_for_excel(None)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_empty_returns_empty(self):
        result = _clean_datetime_columns_for_excel(pd.DataFrame())
        assert result.empty

    def test_original_not_mutated(self):
        df = pd.DataFrame({
            "created": pd.to_datetime(["2026-01-01"]).tz_localize("UTC"),
        })
        result = _clean_datetime_columns_for_excel(df)
        assert df["created"].dt.tz is not None
        assert result["created"].dt.tz is None
