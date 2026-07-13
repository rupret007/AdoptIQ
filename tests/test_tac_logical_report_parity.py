"""Cross-report guards for raw-row versus logical TAC-case parity."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

import adoptiq_backend as backend
from leader_report_generator import LeaderReportGenerator


def _lifecycle_fanout_cases() -> pd.DataFrame:
    """Four source rows: one duplicated ID plus two independent null IDs."""
    return pd.DataFrame(
        {
            "Case #": ["C-1", "C-1", None, None],
            "CASE_ID": ["C-1", "C-1", None, None],
            "Customer Name": ["Acme", "Acme", "Acme", "Acme"],
            "customer_name": ["Acme", "Acme", "Acme", "Acme"],
            "SUBSCRIPTION_ID": ["SUB-1", "SUB-1", "SUB-1", "SUB-1"],
            "Title": ["Old open snapshot", "New closed snapshot", "Null one", "Null two"],
            "STATUS": ["Open", "Closed", "Open", "Closed"],
            "Priority": ["P1", "P3", "P1", "P3"],
            "SEVERITY": ["P1", "P3", "P1", "P3"],
            "CREATED_DATE": ["2026-01-01"] * 4,
            "CLOSED_DATE": [None, "2026-02-01", None, "2026-02-02"],
            "LAST_MODIFIED_DATE": [
                "2026-01-02",
                "2026-02-01",
                "2026-01-03",
                "2026-01-04",
            ],
        }
    )


def test_leader_logical_frame_selects_newest_lifecycle_and_keeps_null_ids() -> None:
    logical = LeaderReportGenerator._logical_tac_cases(_lifecycle_fanout_cases())

    assert len(logical) == 3
    selected = logical.loc[logical["Case #"] == "C-1"].iloc[0]
    assert selected["Title"] == "New closed snapshot"
    assert selected["STATUS"] == "Closed"
    assert logical["Case #"].isna().sum() == 2


def test_leader_attribution_reports_logical_counts_and_diagnostics() -> None:
    generator = object.__new__(LeaderReportGenerator)
    team = {
        "Owner One": {
            "subscriptions": pd.DataFrame(
                {
                    "SUBSCRIPTION_ID": ["SUB-1"],
                    "ACCOUNT_ID_C": ["A-1"],
                    "BU_NAME": ["Acme"],
                }
            ),
            "customers": ["Acme"],
        }
    }

    generator.add_tac_cases_from_csone(team, _lifecycle_fanout_cases(), days=90)

    attributed = team["Owner One"]["tac_cases"]
    assert len(attributed) == 3
    assert attributed.loc[attributed["Case #"] == "C-1", "Title"].item() == "New closed snapshot"
    assert attributed["Case #"].isna().sum() == 2
    assert generator._tac_match_summary["raw_rows_input"] == 4
    assert generator._tac_match_summary["logical_cases_input"] == 3
    assert generator._tac_match_summary["duplicate_rows_removed"] == 1
    assert generator._tac_match_summary["matched_total"] == 3


def test_report_rate_uses_logical_denominator_and_current_severity() -> None:
    escalation_rate, chronic_rate = backend._calc_rates(_lifecycle_fanout_cases())

    # Current C-1 is P3; only the first null-ID row is P1 => 1 / 3.
    assert escalation_rate == 33.3
    assert chronic_rate == 0.0


def test_compact_briefing_uses_logical_total_and_newest_case_detail() -> None:
    briefing = backend._create_executive_briefing_book_with_csone(
        "Test Manager",
        pd.DataFrame(),
        _lifecycle_fanout_cases(),
        pd.DataFrame(),
        "All",
    )

    assert "**Total Support Cases:** 3" in briefing
    assert "New closed snapshot" in briefing
    assert "Old open snapshot" not in briefing


def test_renewal_fetch_returns_logical_cases_and_preserves_raw_diagnostics() -> None:
    class _Cursor:
        description = [
            ("CASE_ID",),
            ("ACCOUNT_ID",),
            ("SUBJECT",),
            ("STATUS",),
            ("CREATED_DATE",),
            ("CLOSED_DATE",),
            ("SEVERITY",),
            ("DESCRIPTION",),
            ("DESCRIPTION_C",),
        ]

        def execute(self, *_args, **_kwargs):
            return self

        def fetchall(self):
            return [
                ("C-1", "A-1", "Old open snapshot", "Open", "2026-01-01", None, "P1", "", ""),
                ("C-1", "A-1", "New closed snapshot", "Closed", "2026-01-01", "2026-02-01", "P3", "", ""),
                (None, "A-1", "Null one", "Open", "2026-01-03", None, "P1", "", ""),
                (None, "A-1", "Null two", "Closed", "2026-01-04", "2026-02-02", "P3", "", ""),
            ]

        def close(self):
            return None

    class _Context:
        def cursor(self):
            return _Cursor()

    with (
        patch.object(backend, "is_table_blocked", return_value=False),
        patch.object(backend, "_get_table_columns", return_value={"CLOSED_DATE"}),
    ):
        result = backend.fetch_support_cases_snowflake(
            _Context(), ["A-1"], days=90, limit=100
        )

    assert len(result) == 3
    current = result.loc[result["CASE_ID"] == "C-1"].iloc[0]
    assert current["SUBJECT"] == "New closed snapshot"
    assert current["case_status_norm"] == "Closed"
    assert result["CASE_ID"].isna().sum() == 2
    assert result.attrs["raw_rows_returned"] == 4
    assert result.attrs["logical_cases_returned"] == 3
    assert result.attrs["duplicates_removed"] == 1


def test_renewal_last_modified_is_freshness_not_a_false_close_date() -> None:
    class _Cursor:
        description = [
            ("CASE_ID",),
            ("ACCOUNT_ID",),
            ("SUBJECT",),
            ("STATUS",),
            ("CREATED_DATE",),
            ("CLOSED_DATE",),
            ("LAST_MODIFIED_DATE",),
            ("SEVERITY",),
            ("DESCRIPTION",),
            ("DESCRIPTION_C",),
        ]

        def execute(self, *_args, **_kwargs):
            return self

        def fetchall(self):
            return [
                (
                    "C-OPEN",
                    "A-1",
                    "Stale open snapshot",
                    "Open",
                    "2026-01-01",
                    None,
                    "2026-01-02",
                    "P1",
                    "",
                    "",
                ),
                (
                    "C-OPEN",
                    "A-1",
                    "Current open snapshot",
                    "Open",
                    "2026-01-01",
                    None,
                    "2026-02-01",
                    "P3",
                    "",
                    "",
                ),
            ]

        def close(self):
            return None

    class _Context:
        def cursor(self):
            return _Cursor()

    with (
        patch.object(backend, "is_table_blocked", return_value=False),
        patch.object(
            backend,
            "_get_table_columns",
            return_value={"LAST_MODIFIED_DATE"},
        ),
    ):
        result = backend.fetch_support_cases_snowflake(
            _Context(), ["A-1"], days=90, limit=100
        )

    assert len(result) == 1
    assert result.iloc[0]["SUBJECT"] == "Current open snapshot"
    assert result.iloc[0]["case_status_norm"] == "Open"
    assert bool(result.iloc[0]["is_open"]) is True
    assert pd.isna(result.iloc[0]["closed_date"])
