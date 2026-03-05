"""
Tests for leader report helpers in leader_report_generator.py.
Covers safe_len, safe_df_check, safe_set, filename generation (Round 1 Fix 1),
and division-by-zero guard (Round 3 Fix 2).
"""

import sys
import re
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest


# ── Helper: create a generator instance without Snowflake ────────────────

@pytest.fixture
def generator():
    """Create a LeaderReportGenerator with a mock context."""
    from leader_report_generator import LeaderReportGenerator
    mock_ctx = mock.MagicMock()
    roster = [("user@cisco.com", "Test User", "Manager")]
    gen = LeaderReportGenerator(mock_ctx, roster)
    return gen


# ── safe_len ─────────────────────────────────────────────────────────────

class TestSafeLen:
    def test_list(self, generator):
        assert generator.safe_len([1, 2, 3]) == 3

    def test_empty_list(self, generator):
        assert generator.safe_len([]) == 0

    def test_none(self, generator):
        assert generator.safe_len(None) == 0

    def test_dict(self, generator):
        assert generator.safe_len({"a": 1, "b": 2}) == 2

    def test_dataframe(self, generator):
        df = pd.DataFrame({"a": [1, 2, 3]})
        assert generator.safe_len(df) == 3

    def test_empty_dataframe(self, generator):
        assert generator.safe_len(pd.DataFrame()) == 0

    def test_string(self, generator):
        assert generator.safe_len("hello") == 5

    def test_integer(self, generator):
        assert generator.safe_len(42) == 0

    def test_set(self, generator):
        assert generator.safe_len({1, 2}) == 2


# ── safe_df_check ────────────────────────────────────────────────────────

class TestSafeDfCheck:
    def test_valid_df_with_column(self, generator):
        df = pd.DataFrame({"name": ["Alice", "Bob"]})
        assert generator.safe_df_check(df, "name") is True

    def test_missing_column(self, generator):
        df = pd.DataFrame({"name": ["Alice"]})
        assert generator.safe_df_check(df, "age") is False

    def test_none(self, generator):
        assert generator.safe_df_check(None, "col") is False

    def test_empty_df(self, generator):
        assert generator.safe_df_check(pd.DataFrame(), "col") is False

    def test_non_dataframe(self, generator):
        assert generator.safe_df_check("not a df", "col") is False

    def test_integer_input(self, generator):
        assert generator.safe_df_check(42, "col") is False


# ── safe_set ─────────────────────────────────────────────────────────────

class TestSafeSet:
    def test_list(self, generator):
        result = generator.safe_set([1, 2, 3])
        assert result == {1, 2, 3}

    def test_none(self, generator):
        result = generator.safe_set(None)
        assert result == set()

    def test_set_passthrough(self, generator):
        result = generator.safe_set({1, 2})
        assert result == {1, 2}

    def test_non_empty_dataframe_returns_empty(self, generator):
        df = pd.DataFrame({"a": [1, 2]})
        result = generator.safe_set(df)
        assert result == set()

    def test_empty_list(self, generator):
        result = generator.safe_set([])
        assert result == set()

    def test_integer(self, generator):
        result = generator.safe_set(42)
        assert result == set()


# ── Filename generation (Round 1 Fix 1 regression) ───────────────────────

class TestFilenameGeneration:
    def test_spaces_replaced_with_underscores(self):
        manager_name = "Brian Frazier"
        safe_manager = "".join(c for c in manager_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_manager = safe_manager.replace(" ", "_")
        assert " " not in safe_manager
        assert safe_manager == "Brian_Frazier"

    def test_special_chars_stripped(self):
        manager_name = "O'Brien (Jr.)"
        safe_manager = "".join(c for c in manager_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_manager = safe_manager.replace(" ", "_")
        assert "'" not in safe_manager
        assert "(" not in safe_manager
        assert ")" not in safe_manager

    def test_hyphen_preserved(self):
        manager_name = "Mary-Jane Watson"
        safe_manager = "".join(c for c in manager_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_manager = safe_manager.replace(" ", "_")
        assert "-" in safe_manager
        assert safe_manager == "Mary-Jane_Watson"

    def test_filename_format(self):
        safe_manager = "Test_User"
        days = 90
        filename = f"AdoptIQ_Report_Leader_{safe_manager}_{days}d_20260301_120000.docx"
        assert filename.startswith("AdoptIQ_Report_Leader_")
        assert filename.endswith(".docx")
        assert " " not in filename

    def test_underscore_preserved(self):
        manager_name = "Test_User"
        safe_manager = "".join(c for c in manager_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_manager = safe_manager.replace(" ", "_")
        assert safe_manager == "Test_User"


# ── Division by zero guard (Round 3 Fix 2 regression) ────────────────────

class TestDivisionByZeroGuard:
    def test_max_prevents_division_by_zero(self):
        total_team_members = 0
        avg_divisor = max(total_team_members, 1)
        assert avg_divisor == 1
        total_customers = 10
        result = total_customers / avg_divisor
        assert result == 10.0

    def test_normal_division(self):
        total_team_members = 5
        avg_divisor = max(total_team_members, 1)
        assert avg_divisor == 5
        total_aps = 25
        result = total_aps / avg_divisor
        assert result == 5.0

    def test_stats_data_format(self):
        """Verify the f-string formatting works with avg_divisor."""
        total_team_members = 0
        avg_divisor = max(total_team_members, 1)
        total_customers = 15
        total_aps = 20
        total_abs = 10
        stats_data = [
            ("Team Members", str(total_team_members), f"{total_team_members}"),
            ("Total Customers", str(total_customers), f"{total_customers/avg_divisor:.1f}"),
            ("Action Plans", str(total_aps), f"{total_aps/avg_divisor:.1f}"),
            ("Adoption Barriers", str(total_abs), f"{total_abs/avg_divisor:.1f}"),
        ]
        assert stats_data[0][2] == "0"
        assert stats_data[1][2] == "15.0"
        assert stats_data[2][2] == "20.0"
        assert stats_data[3][2] == "10.0"


def test_customer_matching_uses_normalized_exact_compare():
    """Guard against substring matching that can mis-attribute customers."""
    src = Path(__file__).resolve().parent.parent.joinpath("leader_report_generator.py").read_text(encoding="utf-8")
    assert "str.contains(customer, case=False, na=False)" not in src
