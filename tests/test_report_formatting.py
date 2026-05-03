"""
Tests for report formatting utilities.
Extends coverage of report_utils.py and compact_report_formatter.py.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest
from report_utils import (
    format_date,
    format_number,
    format_currency,
    get_risk_scoring_explanation,
    get_report_metadata_footer,
    get_data_sources_paragraph_text,
    get_data_sources_list,
)
from compact_report_formatter import calculate_renewal_risk_scores


# ── format_date ──────────────────────────────────────────────────────────

class TestFormatDate:
    def test_long_style(self):
        d = format_date(datetime(2025, 2, 2), "long")
        assert "February" in d
        assert "2025" in d

    def test_short_style(self):
        d = format_date(datetime(2025, 2, 2), "short")
        assert d == "2025-02-02"

    def test_datetime_style(self):
        d = format_date(datetime(2025, 2, 2, 14, 30), "datetime")
        assert "February" in d
        assert "14:30" in d

    def test_iso_style(self):
        d = format_date(datetime(2025, 2, 2, 14, 30, 0), "iso")
        assert d == "2025-02-02 14:30:00"

    def test_none_returns_na(self):
        assert format_date(None) == "N/A"

    def test_string_input(self):
        d = format_date("2025-06-15", "short")
        assert "2025-06-15" in d

    def test_invalid_string(self):
        d = format_date("not a date")
        assert isinstance(d, str)

    def test_default_style_is_long(self):
        d = format_date(datetime(2025, 12, 25))
        assert "December" in d

    def test_unknown_style_uses_long(self):
        d = format_date(datetime(2025, 1, 1), "unknown_style")
        assert "January" in d


# ── format_number ────────────────────────────────────────────────────────

class TestFormatNumber:
    def test_integer(self):
        assert format_number(1234) == "1,234"

    def test_zero(self):
        assert format_number(0) == "0"

    def test_large_number(self):
        assert format_number(1234567) == "1,234,567"

    def test_decimal(self):
        assert format_number(12.567, decimals=2) == "12.57"

    def test_one_decimal(self):
        assert format_number(12.5, decimals=1) == "12.5"

    def test_as_percent(self):
        assert format_number(0.5, as_percent=True) == "0.5%"

    def test_none_returns_na(self):
        assert format_number(None) == "N/A"

    def test_negative_number(self):
        assert format_number(-42) == "-42"

    def test_string_fallback(self):
        # Round 6 / Phase 1.20: an unparseable string returns ``"N/A"`` so
        # callers cannot accidentally surface raw object reprs in
        # user-facing Word/Excel cells when an upstream type sneaks
        # through.  Previously this returned ``str(value)``.
        assert format_number("abc") == "N/A"


# ── format_currency ──────────────────────────────────────────────────────

class TestFormatCurrency:
    def test_normal(self):
        assert format_currency(1000.50) == "$1,000.50"

    def test_zero(self):
        assert format_currency(0) == "$0.00"

    def test_large_amount(self):
        assert format_currency(1234567.89) == "$1,234,567.89"

    def test_none_returns_na(self):
        assert format_currency(None) == "N/A"

    def test_custom_decimals(self):
        assert format_currency(99.999, decimals=0) == "$100"

    def test_integer_input(self):
        assert format_currency(500) == "$500.00"

    def test_string_fallback(self):
        # Round 7 / Phase 3.10: an unparseable string returns ``"N/A"``
        # to match ``format_number``.  Previously this returned
        # ``str(value)`` which leaked unparseable strings (e.g.
        # ``"USD 2.5MM"``) into financial Word cells as literal text.
        assert format_currency("abc") == "N/A"


# ── get_risk_scoring_explanation ─────────────────────────────────────────

class TestRiskScoringExplanation:
    def test_contains_key_terms(self):
        expl = get_risk_scoring_explanation()
        assert "Adoption Barriers" in expl
        assert "BEMS" in expl
        assert "CRITICAL" in expl
        assert "HIGH" in expl
        # Round 73 / Phase 3 (F9): the rendered band label is now
        # ``MODERATE`` (was ``MEDIUM`` pre-R73) so the methodology
        # paragraph agrees byte-for-byte with the user-facing
        # vocabulary on every other Renewal surface (R67/B1, R67/B6,
        # R70/Phase 3 #11). The internal RISK_BAND_THRESHOLDS dict
        # KEY stays ``MEDIUM`` for cross-sheet parity (Comprehensive
        # ``Risk_Components.risk_band``) -- only the rendered string
        # was remapped.
        assert "MODERATE" in expl
        assert "LOW" in expl

    def test_not_empty(self):
        assert len(get_risk_scoring_explanation()) > 100


# ── get_report_metadata_footer ───────────────────────────────────────────

class TestReportMetadataFooter:
    def test_basic(self):
        footer = get_report_metadata_footer(report_type="Test")
        assert "Test" in footer
        assert "Generated" in footer

    def test_with_all_fields(self):
        footer = get_report_metadata_footer(
            report_type="Compact",
            analysis_id="ABC-123",
            manager="Jane",
            customer_name="Acme",
            technology="Webex",
            days=90,
        )
        assert "Compact" in footer
        assert "ABC-123" in footer
        assert "Jane" in footer
        assert "Acme" in footer
        assert "Webex" in footer
        assert "90" in footer

    def test_empty_optional_fields_omitted(self):
        footer = get_report_metadata_footer(report_type="Simple")
        assert "Manager" not in footer
        assert "Customer" not in footer

    def test_data_sources_mentioned(self):
        footer = get_report_metadata_footer(report_type="X")
        assert "AdoptIQ" in footer


# ── get_data_sources ─────────────────────────────────────────────────────

class TestDataSources:
    def test_paragraph_text_not_empty(self):
        text = get_data_sources_paragraph_text()
        assert len(text) > 50
        assert "Adoption Barriers" in text

    def test_data_sources_list_structure(self):
        sources = get_data_sources_list()
        assert isinstance(sources, list)
        assert len(sources) >= 5
        for item in sources:
            assert len(item) == 3


# ── calculate_renewal_risk_scores (extended) ─────────────────────────────

class TestRenewalRiskScores:
    def test_empty_both(self):
        assert calculate_renewal_risk_scores(pd.DataFrame(), pd.DataFrame()) == {}

    def test_single_customer_ab(self):
        ab = pd.DataFrame([{"customer_name": "X", "SUBJECT_C": "Issue", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open"}])
        result = calculate_renewal_risk_scores(ab, pd.DataFrame())
        assert "X" in result
        assert result["X"]["score"] > 0

    def test_single_customer_csone(self):
        csone = pd.DataFrame([{
            "customer_name": "Y",
            "Transaction ID": "",
            "Severity": "P3",
            "Date/Time Opened": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
        }])
        result = calculate_renewal_risk_scores(pd.DataFrame(), csone)
        assert "Y" in result
        assert result["Y"]["score"] >= 0

    def test_bems_escalation_adds_risk(self):
        csone = pd.DataFrame([{
            "customer_name": "Z",
            "Transaction ID": "BEMS01234567",
            "Severity": "P1",
            "Date/Time Opened": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
        }])
        result = calculate_renewal_risk_scores(pd.DataFrame(), csone)
        assert "Z" in result
        factors = result["Z"]["risk_factors"]
        assert any("BEMS" in f.upper() or "escalation" in f.lower() for f in factors)

    def test_multiple_customers(self):
        ab = pd.DataFrame([
            {"customer_name": "A", "SUBJECT_C": "X", "SEVERITY_C": "High", "AB_STATUS_C": "Open"},
            {"customer_name": "B", "SUBJECT_C": "Y", "SEVERITY_C": "Low", "AB_STATUS_C": "Closed"},
        ])
        result = calculate_renewal_risk_scores(ab, pd.DataFrame())
        assert "A" in result
        assert "B" in result

    def test_result_structure(self):
        ab = pd.DataFrame([{"customer_name": "Test", "SUBJECT_C": "Issue", "SEVERITY_C": "Medium", "AB_STATUS_C": "Open"}])
        result = calculate_renewal_risk_scores(ab, pd.DataFrame())
        info = result["Test"]
        assert "score" in info
        assert "color" in info
        assert "category" in info
        assert "risk_factors" in info
        assert isinstance(info["risk_factors"], list)
