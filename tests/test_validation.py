"""
Tests for input validation functions in app_simple.py.
Covers validate_manager_input, validate_technology_input, validate_days_input,
validate_file_input, and validate_customer_name_input.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app_simple import (
    validate_manager_input,
    validate_technology_input,
    validate_days_input,
    validate_file_input,
    validate_customer_name_input,
)


# ── validate_manager_input ──────────────────────────────────────────────

class TestValidateManagerInput:
    def test_valid_name(self):
        ok, msg = validate_manager_input("Jane Doe")
        assert ok is True
        assert msg == "Valid"

    def test_valid_hyphenated(self):
        ok, _ = validate_manager_input("Mary-Jane Watson")
        assert ok is True

    def test_empty_string(self):
        ok, msg = validate_manager_input("")
        assert ok is False
        assert "required" in msg.lower()

    def test_none(self):
        ok, msg = validate_manager_input(None)
        assert ok is False

    def test_not_a_string(self):
        ok, _ = validate_manager_input(12345)
        assert ok is False

    def test_too_long(self):
        ok, msg = validate_manager_input("A" * 101)
        assert ok is False
        assert "long" in msg.lower()

    def test_exactly_100_chars(self):
        ok, _ = validate_manager_input("A" * 100)
        assert ok is True

    def test_sql_injection_single_quote(self):
        ok, msg = validate_manager_input("John'; DROP TABLE--")
        assert ok is False
        assert "Invalid" in msg or "character" in msg.lower()

    def test_sql_injection_double_dash(self):
        ok, _ = validate_manager_input("admin--")
        assert ok is False

    def test_sql_injection_comment(self):
        ok, _ = validate_manager_input("user /* comment */")
        assert ok is False

    def test_sql_injection_xp(self):
        ok, _ = validate_manager_input("xp_cmdshell")
        assert ok is False

    def test_semicolon_blocked(self):
        ok, _ = validate_manager_input("user;")
        assert ok is False


# ── validate_technology_input ────────────────────────────────────────────

class TestValidateTechnologyInput:
    def test_valid_tech(self):
        ok, msg = validate_technology_input("Webex Calling")
        assert ok is True
        assert msg == "Valid"

    def test_all_tech(self):
        ok, _ = validate_technology_input("All")
        assert ok is True

    def test_empty(self):
        ok, msg = validate_technology_input("")
        assert ok is False
        assert "required" in msg.lower()

    def test_none(self):
        ok, _ = validate_technology_input(None)
        assert ok is False

    def test_not_a_string(self):
        ok, _ = validate_technology_input(42)
        assert ok is False

    def test_too_long(self):
        ok, msg = validate_technology_input("T" * 101)
        assert ok is False
        assert "long" in msg.lower()

    def test_sql_injection(self):
        ok, _ = validate_technology_input("Webex'; DROP TABLE--")
        assert ok is False

    def test_valid_long_name(self):
        ok, _ = validate_technology_input("Webex Contact Center Enterprise")
        assert ok is True


# ── validate_days_input ──────────────────────────────────────────────────

class TestValidateDaysInput:
    def test_valid_30(self):
        ok, msg = validate_days_input(30)
        assert ok is True
        assert msg == "Valid"

    def test_valid_90(self):
        ok, _ = validate_days_input(90)
        assert ok is True

    def test_valid_365(self):
        ok, _ = validate_days_input(365)
        assert ok is True

    def test_valid_1(self):
        ok, _ = validate_days_input(1)
        assert ok is True

    def test_zero(self):
        ok, msg = validate_days_input(0)
        assert ok is False
        assert "between" in msg.lower()

    def test_negative(self):
        ok, _ = validate_days_input(-10)
        assert ok is False

    def test_too_large(self):
        ok, _ = validate_days_input(366)
        assert ok is False

    def test_string_input(self):
        ok, msg = validate_days_input("30")
        assert ok is False
        assert "number" in msg.lower()

    def test_float_input(self):
        ok, _ = validate_days_input(30.5)
        assert ok is False

    def test_none(self):
        ok, _ = validate_days_input(None)
        assert ok is False


# ── validate_file_input ──────────────────────────────────────────────────

class TestValidateFileInput:
    def test_valid_xlsx(self):
        ok, msg = validate_file_input("report.xlsx")
        assert ok is True
        assert msg == "Valid"

    def test_valid_xls(self):
        ok, _ = validate_file_input("data.xls")
        assert ok is True

    def test_empty_is_optional(self):
        ok, _ = validate_file_input("")
        assert ok is True

    def test_none_is_optional(self):
        ok, _ = validate_file_input(None)
        assert ok is True

    def test_wrong_extension_csv(self):
        ok, msg = validate_file_input("data.csv")
        assert ok is False
        assert "Excel" in msg

    def test_wrong_extension_pdf(self):
        ok, _ = validate_file_input("report.pdf")
        assert ok is False

    def test_no_extension(self):
        ok, _ = validate_file_input("report")
        assert ok is False

    def test_not_a_string(self):
        ok, msg = validate_file_input(123)
        assert ok is False
        assert "text" in msg.lower()

    def test_case_insensitive_extension(self):
        ok, _ = validate_file_input("Report.XLSX")
        assert ok is True


# ── validate_customer_name_input ─────────────────────────────────────────

class TestValidateCustomerNameInput:
    def test_valid_name(self):
        ok, msg = validate_customer_name_input("Acme Corp")
        assert ok is True
        assert msg == "Valid"

    def test_empty_is_optional(self):
        ok, _ = validate_customer_name_input("")
        assert ok is True

    def test_none_is_optional(self):
        ok, _ = validate_customer_name_input(None)
        assert ok is True

    def test_too_long(self):
        ok, msg = validate_customer_name_input("A" * 201)
        assert ok is False
        assert "long" in msg.lower()

    def test_not_a_string(self):
        ok, msg = validate_customer_name_input(123)
        assert ok is False
        assert "text" in msg.lower()

    def test_sql_injection(self):
        ok, _ = validate_customer_name_input("Acme'; DROP TABLE--")
        assert ok is False

    def test_xss_attack(self):
        ok, _ = validate_customer_name_input("Test<script>alert(1)")
        assert ok is False

    def test_dropbox_allowed(self):
        ok, _ = validate_customer_name_input("Dropbox")
        assert ok is True

    def test_select_inc_allowed(self):
        ok, _ = validate_customer_name_input("Select Inc")
        assert ok is True

    def test_semicolon_blocked(self):
        ok, _ = validate_customer_name_input("Company;Name")
        assert ok is False

    def test_exactly_200_chars(self):
        ok, _ = validate_customer_name_input("A" * 200)
        assert ok is True

    def test_unicode_name(self):
        ok, _ = validate_customer_name_input("Unternehmen GmbH")
        assert ok is True
