"""Round 3 / Phase 3.3 regression test.

When ``get_subscriptions_for_team`` returns an empty DataFrame whose
``attrs`` carry a ``fetch_error`` (Snowflake timeout / driver error),
the validator must surface "subscription data unavailable", not
"no subscriptions assigned to team members".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_source_validator import validate_data_sources_for_report


class _Ctx:
    pass


def test_empty_team_subs_with_fetch_error_marked_unavailable():
    df = pd.DataFrame()
    df.attrs["fetch_error"] = "snowflake.connector.errors.OperationalError: 250003"
    df.attrs["fetch_error_kind"] = "snowflake_timeout"

    is_valid, missing, details = validate_data_sources_for_report(
        report_type="comprehensive",
        snowflake_ctx=_Ctx(),
        team_subs_df=df,
        ab_data=pd.DataFrame({"customer_name": ["Acme"]}),
        csone_data=pd.DataFrame({"customer_name": ["Acme"]}),
        required_sources=["team_subscriptions"],
    )

    assert is_valid is False
    assert "team_subscriptions" in missing
    msg = details.get("team_subscriptions", "")
    assert "UNAVAILABLE" in msg
    assert "fetch failed" in msg
    assert "250003" in msg
    assert "no subscriptions assigned" not in msg.lower()


def test_empty_team_subs_without_fetch_error_keeps_no_data_message():
    df = pd.DataFrame()

    is_valid, missing, details = validate_data_sources_for_report(
        report_type="comprehensive",
        snowflake_ctx=_Ctx(),
        team_subs_df=df,
        ab_data=pd.DataFrame({"customer_name": ["Acme"]}),
        csone_data=pd.DataFrame({"customer_name": ["Acme"]}),
        required_sources=["team_subscriptions"],
    )

    assert is_valid is False
    msg = details.get("team_subscriptions", "")
    assert "UNAVAILABLE" not in msg
    assert "No team subscription data found" in msg
