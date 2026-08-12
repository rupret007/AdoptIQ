"""Round 162 — Comprehensive integrity gate degrades instead of hard-aborting."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app_simple
from data_source_validator import DataSourceValidationError, raise_validation_error_if_invalid


def test_r162_both_empty_with_team_subs_does_not_abort() -> None:
    team_subs = pd.DataFrame({"BU_NAME": ["ACME"], "SUBSCRIPTION_ID": ["SUB1"]})
    should_abort, warnings = app_simple._r162_comprehensive_integrity_should_abort(
        pd.DataFrame(),
        pd.DataFrame(),
        team_subs_df=team_subs,
        csconsole_action_plans=pd.DataFrame(),
        integrity_reason=app_simple._R162_INTEGRITY_BOTH_EMPTY_REASON,
    )
    assert should_abort is False
    kinds = {w.get("kind") for w in warnings}
    assert "scoped_ab_empty" in kinds
    assert "scoped_csone_empty" in kinds


def test_r162_both_empty_without_team_or_csconsole_aborts() -> None:
    should_abort, warnings = app_simple._r162_comprehensive_integrity_should_abort(
        pd.DataFrame(),
        pd.DataFrame(),
        team_subs_df=pd.DataFrame(),
        csconsole_action_plans=pd.DataFrame(),
        integrity_reason=app_simple._R162_INTEGRITY_BOTH_EMPTY_REASON,
    )
    assert should_abort is True
    assert warnings == []


def test_r162_csconsole_only_continues_without_team_subs() -> None:
    action_plans = pd.DataFrame({"ID": ["AP1"], "BU_NAME": ["ACME"]})
    should_abort, warnings = app_simple._r162_comprehensive_integrity_should_abort(
        pd.DataFrame(),
        pd.DataFrame(),
        team_subs_df=pd.DataFrame(),
        csconsole_action_plans=action_plans,
        integrity_reason=app_simple._R162_INTEGRITY_BOTH_EMPTY_REASON,
    )
    assert should_abort is False
    assert any(w.get("kind") == "scoped_csone_empty" for w in warnings)


def test_r162_quality_integrity_reason_still_aborts() -> None:
    should_abort, _warnings = app_simple._r162_comprehensive_integrity_should_abort(
        pd.DataFrame({"title": ["x"], "description": ["y"]}),
        pd.DataFrame(),
        team_subs_df=pd.DataFrame({"BU_NAME": ["ACME"]}),
        integrity_reason="Detected 3 future-dated AB rows.",
    )
    assert should_abort is True


def test_r162_helper_present_in_app_simple_source() -> None:
    src = Path(app_simple.__file__).read_text(encoding="utf-8")
    assert "def _r162_comprehensive_integrity_should_abort" in src
    assert "Partial Data — Continuing" in src


def test_r162_1_honest_upstream_empty_ab_reaches_degraded_continue_gate() -> None:
    """The prerequisite validator must not pre-empt the Round 162 policy."""
    team_subs = pd.DataFrame({"BU_NAME": ["ACME"], "SUBSCRIPTION_ID": ["SUB1"]})
    empty_ab = pd.DataFrame()
    empty_csone = pd.DataFrame()

    # This is the exact validation shape used by the Comprehensive worker.
    # Pre-R162.1 it raised here and the integrity helper was unreachable.
    raise_validation_error_if_invalid(
        report_type="comprehensive",
        snowflake_ctx=object(),
        team_subs_df=team_subs,
        ab_data=empty_ab,
        csone_data=empty_csone,
        required_sources=["snowflake", "team_subscriptions", "adoption_barriers"],
        allow_empty_required_sources=["adoption_barriers"],
    )

    should_abort, warnings = app_simple._r162_comprehensive_integrity_should_abort(
        empty_ab,
        empty_csone,
        team_subs_df=team_subs,
        integrity_reason=app_simple._R162_INTEGRITY_BOTH_EMPTY_REASON,
    )
    assert should_abort is False
    assert {warning.get("kind") for warning in warnings} == {
        "scoped_ab_empty",
        "scoped_csone_empty",
    }


@pytest.mark.parametrize(
    ("ab_data", "expected_detail"),
    [
        pytest.param(
            None,
            "No adoption barrier data found",
            id="missing-frame",
        ),
        pytest.param(
            pd.DataFrame({"UNRELATED_COLUMN": ["value"]}),
            "missing the customer-identifier slot",
            id="malformed-frame",
        ),
    ],
)
def test_r162_1_allow_empty_does_not_allow_missing_or_malformed_ab(
    ab_data, expected_detail: str
) -> None:
    team_subs = pd.DataFrame({"BU_NAME": ["ACME"]})
    with pytest.raises(DataSourceValidationError) as exc_info:
        raise_validation_error_if_invalid(
            report_type="comprehensive",
            snowflake_ctx=object(),
            team_subs_df=team_subs,
            ab_data=ab_data,
            csone_data=pd.DataFrame(),
            required_sources=["snowflake", "team_subscriptions", "adoption_barriers"],
            allow_empty_required_sources=["adoption_barriers"],
        )
    assert expected_detail in exc_info.value.details["adoption_barriers"]


def test_r162_1_allow_empty_does_not_hide_ab_fetch_failure() -> None:
    failed_ab = pd.DataFrame()
    failed_ab.attrs["fetch_error"] = "upstream timeout"
    with pytest.raises(DataSourceValidationError) as exc_info:
        raise_validation_error_if_invalid(
            report_type="comprehensive",
            snowflake_ctx=object(),
            team_subs_df=pd.DataFrame({"BU_NAME": ["ACME"]}),
            ab_data=failed_ab,
            csone_data=pd.DataFrame(),
            required_sources=["snowflake", "team_subscriptions", "adoption_barriers"],
            allow_empty_required_sources=["adoption_barriers"],
        )
    assert "fetch FAILED" in exc_info.value.details["adoption_barriers"]


def test_r162_1_worker_threads_allow_empty_policy_to_all_validation_passes() -> None:
    src = Path(app_simple.__file__).read_text(encoding="utf-8")
    assert src.count(
        "allow_empty_required_sources=validation_allow_empty_required_sources"
    ) == 3
