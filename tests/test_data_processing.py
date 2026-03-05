"""
Tests for pure data-processing functions in adoptiq_backend.py.
Covers _extract_refs, _filter_tech_text, _filter_tech_text_enhanced,
_is_wxcce_signature, _normalize_category, _normalize_subtech,
_portfolio_grade, _calc_rates, _counts_by, load_csone_excel,
_apply_scope_filter_csone, and cross_reference_refs.
"""

import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest
from unittest.mock import patch
from adoptiq_backend import (
    _extract_refs,
    _filter_tech_text,
    _filter_tech_text_enhanced,
    _is_wxcce_signature,
    _normalize_category,
    _normalize_subtech,
    _portfolio_grade,
    _calc_rates,
    _counts_by,
    load_csone_excel,
    _apply_scope_filter_csone,
    cross_reference_refs,
    fetch_support_cases_snowflake,
)


# ── _extract_refs ────────────────────────────────────────────────────────

class TestExtractRefs:
    def test_bems_id(self):
        result = _extract_refs("Escalation BEMS01916938 opened")
        assert "BEMS01916938" in result

    def test_bems_with_hyphen(self):
        result = _extract_refs("Case BEMS-123456 pending")
        assert "BEMS-123456" in result

    def test_csc_id(self):
        result = _extract_refs("Bug CSCwa12345 found")
        assert "CSCWA12345" in result

    def test_mixed_refs(self):
        result = _extract_refs("BEMS01916938 and CSCwa12345 in same case")
        assert "BEMS01916938" in result
        assert "CSCWA12345" in result

    def test_empty_string(self):
        assert _extract_refs("") == ""

    def test_none(self):
        assert _extract_refs(None) == ""

    def test_no_refs(self):
        assert _extract_refs("No references here") == ""

    def test_case_insensitive(self):
        result = _extract_refs("bems01234567")
        assert "BEMS01234567" in result

    def test_wxccsa_ref(self):
        result = _extract_refs("WXCCSA-12345 is open")
        assert "WXCCSA-12345" in result

    def test_sorted_output(self):
        result = _extract_refs("CSCzz99999 BEMS00000001")
        parts = [r.strip() for r in result.split(",")]
        assert parts == sorted(parts)


# ── _is_wxcce_signature ──────────────────────────────────────────────────

class TestIsWxcceSignature:
    def test_enterprise_marker(self):
        assert _is_wxcce_signature("Webex Contact Center Enterprise") is True

    def test_webex_cce(self):
        assert _is_wxcce_signature("Webex CCE") is True

    def test_ucce_marker(self):
        assert _is_wxcce_signature("Unified Contact Center Enterprise") is True

    def test_ucce_short(self):
        assert _is_wxcce_signature("some text UCCE issue") is True

    def test_wxcc_not_enterprise(self):
        assert _is_wxcce_signature("Webex Contact Center") is False

    def test_empty(self):
        assert _is_wxcce_signature("") is False

    def test_none(self):
        assert _is_wxcce_signature(None) is False

    def test_case_insensitive(self):
        assert _is_wxcce_signature("WEBEX CCE") is True


# ── _filter_tech_text ────────────────────────────────────────────────────

class TestFilterTechText:
    def test_all_matches_everything(self):
        assert _filter_tech_text("anything", "All") is True

    def test_empty_string_with_all(self):
        assert _filter_tech_text("", "All") is True

    def test_webex_calling_match(self):
        assert _filter_tech_text("Webex Calling", "Webex Calling") is True

    def test_no_match(self):
        assert _filter_tech_text("Something Else", "Webex Calling") is False

    def test_empty_string(self):
        assert _filter_tech_text("", "Webex Calling") is False

    def test_none_string(self):
        assert _filter_tech_text(None, "Webex Calling") is False


# ── _filter_tech_text_enhanced ───────────────────────────────────────────

class TestFilterTechTextEnhanced:
    def test_all_returns_true(self):
        assert _filter_tech_text_enhanced("x", "y", "All") is True

    def test_wxcc_excludes_enterprise(self):
        assert _filter_tech_text_enhanced(
            "Contact Center Software",
            "Webex CCE / Webex Contact Center Enterprise",
            "Webex Contact Center",
        ) is False

    def test_wxcce_matches_enterprise_row(self):
        assert _filter_tech_text_enhanced(
            "Contact Center Software",
            "Webex CCE / Webex Contact Center Enterprise",
            "Webex Contact Center Enterprise",
        ) is True

    def test_pure_wxcc_matches(self):
        assert _filter_tech_text_enhanced(
            "Webex Contact Center",
            "Webex Contact Center",
            "Webex Contact Center",
        ) is True

    def test_uccx_sub_tech(self):
        assert _filter_tech_text_enhanced(
            "Contact Center Software",
            "UCCX",
            "Cisco UCCX",
        ) is True

    def test_ucce_not_matched_as_uccx(self):
        assert _filter_tech_text_enhanced(
            "Contact Center Software",
            "UCCE",
            "Cisco UCCX",
        ) is False

    def test_all_contact_center(self):
        assert _filter_tech_text_enhanced(
            "Contact Center Software",
            "Webex Contact Center",
            "All Contact Center",
        ) is True

    def test_none_fields(self):
        assert _filter_tech_text_enhanced(None, None, "Webex Calling") is False


# ── _normalize_category ─────────────────────────────────────────────────

class TestNormalizeCategory:
    def test_empty(self):
        assert _normalize_category("") == "Uncategorized"

    def test_none(self):
        assert _normalize_category(None) == "Uncategorized"

    def test_unknown_value(self):
        assert _normalize_category("xyzzy") == "Uncategorized"


# ── _normalize_subtech ──────────────────────────────────────────────────

class TestNormalizeSubtech:
    def test_empty(self):
        assert _normalize_subtech("") == "Unknown"

    def test_none(self):
        assert _normalize_subtech(None) == "Unknown"

    def test_wxcc(self):
        result = _normalize_subtech("Webex Contact Center")
        assert result == "Webex Contact Center"

    def test_unknown(self):
        assert _normalize_subtech("xyzzy") == "Other/Unknown"


# ── _portfolio_grade ─────────────────────────────────────────────────────

class TestPortfolioGrade:
    def test_grade_d_high_ab(self):
        assert _portfolio_grade(100, 0, 0) == "D"

    def test_grade_d_high_esc(self):
        assert _portfolio_grade(0, 30, 0) == "D"

    def test_grade_d_high_chronic(self):
        assert _portfolio_grade(0, 0, 30) == "D"

    def test_grade_c_medium_ab(self):
        assert _portfolio_grade(50, 0, 0) == "C"

    def test_grade_c_medium_esc(self):
        assert _portfolio_grade(0, 20, 0) == "C"

    def test_grade_b_low(self):
        assert _portfolio_grade(5, 5, 5) == "B"

    def test_grade_b_boundary(self):
        assert _portfolio_grade(9, 19, 19) == "B"

    def test_grade_d_boundary(self):
        assert _portfolio_grade(99, 29, 29) == "C"


# ── _calc_rates ──────────────────────────────────────────────────────────

class TestCalcRates:
    def test_empty_df(self):
        esc, chronic = _calc_rates(pd.DataFrame())
        assert esc == 0.0
        assert chronic == 0.0

    def test_none_df(self):
        esc, chronic = _calc_rates(None)
        assert esc == 0.0
        assert chronic == 0.0

    def test_with_escalation(self):
        df = pd.DataFrame([
            {"Title": "escalation needed", "Severity": "P1"},
            {"Title": "normal case", "Severity": "P3"},
        ])
        esc, chronic = _calc_rates(df)
        assert esc > 0
        assert chronic == 0.0

    def test_sev1_counts(self):
        df = pd.DataFrame([
            {"Title": "sev-1 urgent issue", "Severity": "P1"},
            {"Title": "normal", "Severity": "P3"},
        ])
        esc, _ = _calc_rates(df)
        assert esc == 50.0


# ── _counts_by ───────────────────────────────────────────────────────────

class TestCountsBy:
    def test_normal_grouping(self):
        df = pd.DataFrame({"color": ["red", "red", "blue"]})
        result = _counts_by(df, "color")
        assert len(result) == 2
        assert "count" in result.columns
        assert result.iloc[0]["count"] >= result.iloc[1]["count"]

    def test_empty_df(self):
        result = _counts_by(pd.DataFrame(), "x")
        assert result.empty

    def test_none_df(self):
        result = _counts_by(None, "x")
        assert result.empty

    def test_missing_column(self):
        df = pd.DataFrame({"a": [1, 2]})
        result = _counts_by(df, "missing")
        assert result.empty

    def test_single_row(self):
        df = pd.DataFrame({"cat": ["A"]})
        result = _counts_by(df, "cat")
        assert len(result) == 1
        assert result.iloc[0]["count"] == 1


# ── load_csone_excel ─────────────────────────────────────────────────────

class TestLoadCsoneExcel:
    def test_none_path(self):
        result = load_csone_excel(None)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_missing_file(self):
        result = load_csone_excel(Path("/nonexistent/file.xlsx"))
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    @pytest.mark.slow
    def test_corrupt_file(self, tmp_path):
        corrupt = tmp_path / "corrupt.xlsx"
        corrupt.write_text("not a valid xlsx")
        result = load_csone_excel(corrupt)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    @pytest.mark.slow
    def test_valid_file(self, tmp_path):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["SR Number", "Title", "Severity"])
        ws.append(["TAC001", "Test case", "P2"])
        ws.append(["TAC002", "Another case", "P3"])
        path = tmp_path / "test.xlsx"
        wb.save(str(path))
        result = load_csone_excel(path)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert "SR Number" in result.columns


# ── _apply_scope_filter_csone (Fix 5 regression) ────────────────────────

class TestApplyScopeFilterCsone:
    def test_none_returns_empty_df(self):
        result = _apply_scope_filter_csone(None, "All", 90, [], [])
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_empty_returns_empty_df(self):
        result = _apply_scope_filter_csone(pd.DataFrame(), "All", 90, [], [])
        assert isinstance(result, pd.DataFrame)
        assert result.empty


# ── cross_reference_refs ─────────────────────────────────────────────────

class TestCrossReferenceRefs:
    def test_matching_refs(self):
        ab_df = pd.DataFrame([
            {"ID": "AB001", "SUBJECT_C": "Bug CSCwa12345 found", "customer_name": "Acme", "bemscsc_refs": "CSCWA12345"},
        ])
        csone_df = pd.DataFrame([
            {"SR Number": "TAC001", "Title": "CSCwa12345 workaround", "customer_name": "Acme", "bemscsc_refs": "CSCWA12345"},
        ])
        ext_bugs = [{"bug_id": "CSCwa12345", "title": "Known defect"}]
        matches, matched_df = cross_reference_refs(ab_df, csone_df, ext_bugs)
        assert len(matches) > 0

    def test_no_matches(self):
        ab_df = pd.DataFrame([
            {"ID": "AB001", "SUBJECT_C": "No refs here", "customer_name": "Acme", "bemscsc_refs": ""},
        ])
        csone_df = pd.DataFrame([
            {"SR Number": "TAC001", "Title": "Clean case", "customer_name": "Acme", "bemscsc_refs": ""},
        ])
        ext_bugs = [{"bug_id": "CSCzz99999", "title": "Unrelated"}]
        matches, matched_df = cross_reference_refs(ab_df, csone_df, ext_bugs)
        assert len(matches) == 0

    def test_empty_inputs(self):
        matches, matched_df = cross_reference_refs(
            pd.DataFrame(), pd.DataFrame(), []
        )
        assert len(matches) == 0


class TestFetchSupportCasesSnowflake:
    def test_case_priority_norm_uses_p_labels(self):
        class _Cursor:
            def __init__(self):
                self.description = [
                    ("CASE_ID",),
                    ("ACCOUNT_ID",),
                    ("SUBJECT",),
                    ("STATUS",),
                    ("CREATED_DATE",),
                    ("CLOSED_DATE",),
                    ("SEVERITY",),
                ]

            def execute(self, *args, **kwargs):
                return self

            def fetchall(self):
                return [
                    ("C-1", "A-1", "Issue 1", "Open", "2026-01-01T00:00:00", None, "Critical"),
                    ("C-2", "A-2", "Issue 2", "Open", "2026-01-02T00:00:00", None, "High"),
                ]

            def close(self):
                return None

        class _Ctx:
            def cursor(self):
                return _Cursor()

        # This test validates normalization behavior only, so bypass policy block.
        with patch("adoptiq_backend.is_table_blocked", return_value=False):
            df = fetch_support_cases_snowflake(_Ctx(), ["A-1", "A-2"], 90)
        assert not df.empty
        by_case = df.set_index("CASE_ID")["case_priority_norm"].to_dict()
        assert by_case["C-1"] == "P1"
        assert by_case["C-2"] == "P2"
