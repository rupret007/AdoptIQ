"""Focused regressions for strict report scope and delivery gates."""

from __future__ import annotations

import ast
import inspect

import pandas as pd
import pytest

import app_simple
import canonical_metrics as cm
from data_source_validator import validate_data_sources_for_report


def _function_calls(function, called_name: str) -> list[ast.Call]:
    tree = ast.parse(inspect.getsource(function))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == called_name
    ]


def test_compact_csone_scope_keeps_honest_empty_instead_of_widening() -> None:
    prepared = pd.DataFrame(
        {
            "customer_name": ["Outside Customer"],
            "Technology": ["Webex Contact Center"],
            "Sub Technology": ["WxCC"],
        }
    )

    scoped = app_simple._r162_apply_strict_csone_report_scope(
        prepared,
        "Webex Contact Center",
        90,
        [],
        ["In Scope Customer"],
        include_all_cases=True,
    )

    assert scoped.empty
    assert scoped.attrs["scope_validation_empty"] is True
    assert "customer/member and technology criteria" in scoped.attrs[
        "scope_validation_detail"
    ]
    # This pins the concrete pre-fix reproducer: the technology-only fallback
    # would have admitted the same outside-customer row.
    widened = app_simple._apply_scope_filter_csone_inclusive(
        prepared,
        "Webex Contact Center",
        90,
        include_all_cases=True,
    )
    assert widened["customer_name"].tolist() == ["Outside Customer"]


def test_compact_worker_never_calls_technology_only_csone_fallback() -> None:
    source = inspect.getsource(app_simple.run_compact_analysis)

    assert "_r162_apply_strict_csone_report_scope(" in source
    assert "_apply_scope_filter_csone_inclusive(" not in source


def test_comprehensive_csconsole_scope_uses_scoped_customer_boundary() -> None:
    scoped = app_simple._r162_scope_comprehensive_csconsole_sources(
        action_plans=pd.DataFrame(
            {
                "ID": ["AP-IN", "AP-OUT"],
                "ACCOUNT_ID_C": ["A-WXCC", "A-MEETINGS"],
                "BU_NAME": ["Contact Customer", "Meetings Customer"],
            }
        ),
        customer_pulse=pd.DataFrame(
            {
                "ID": ["PULSE-DIRECT"],
                "ACCOUNT_ID_C": ["A-WXCC"],
                "BU_NAME": ["Contact Customer"],
                "TECHNOLOGY_C": ["Webex Contact Center"],
                "SUB_TECHNOLOGY_C": ["WxCC"],
            }
        ),
        success_priorities=pd.DataFrame(
            {
                "ID": ["SP-UNKNOWN"],
                "ACCOUNT_ID_C": ["A-WXCC"],
                "BU_NAME": ["Contact Customer"],
            }
        ),
        adoption_barriers=pd.DataFrame(),
        technology="Webex Contact Center",
        customer_names=["Contact Customer"],
        account_ids=["A-WXCC"],
    )

    # An AP without technology metadata is retained only through the already
    # technology-scoped subscription customer/account universe.
    assert scoped["action_plans"]["ID"].tolist() == ["AP-IN"]
    # A different physical source with its own authoritative technology field
    # remains applicable inside the selected criteria.
    assert scoped["customer_pulse"]["ID"].tolist() == ["PULSE-DIRECT"]
    # Missing technology plus no scoped customer/account match is disclosed,
    # never widened into the report.
    assert scoped["success_priorities"].empty
    assert cm.source_data_state(scoped["success_priorities"])["state"] in {
        "partial",
        "unavailable",
    }


def test_comprehensive_direct_technology_evidence_survives_empty_sub_scope() -> None:
    scoped = app_simple._r162_scope_comprehensive_csconsole_sources(
        action_plans=pd.DataFrame(),
        customer_pulse=pd.DataFrame(
            {
                "ID": ["PULSE-DIRECT"],
                "BU_NAME": ["Manager Prefetched Customer"],
                "TECHNOLOGY_C": ["Webex Contact Center"],
                "SUB_TECHNOLOGY_C": ["WxCC"],
            }
        ),
        success_priorities=pd.DataFrame(),
        adoption_barriers=pd.DataFrame(),
        technology="Webex Contact Center",
        customer_names=[],
        account_ids=[],
    )

    assert scoped["customer_pulse"]["ID"].tolist() == ["PULSE-DIRECT"]


def test_comprehensive_final_consumers_never_reintroduce_raw_csconsole() -> None:
    customer_calls = _function_calls(
        app_simple.run_comprehensive_analysis,
        "_get_all_customers_from_all_sources",
    )
    assert len(customer_calls) >= 2
    forbidden = {
        "csconsole_action_plans",
        "csconsole_customer_pulse",
        "csconsole_success_priorities",
        "csconsole_adoption_barriers",
    }
    for call in customer_calls:
        values = {
            ast.unparse(keyword.value)
            for keyword in call.keywords
            if keyword.arg and keyword.arg.startswith("csconsole_")
        }
        assert not values & forbidden

    validator_calls = _function_calls(
        app_simple.run_comprehensive_analysis,
        "raise_validation_error_if_invalid",
    )
    assert len(validator_calls) >= 2
    for call in validator_calls:
        kwargs = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in call.keywords
            if keyword.arg
        }
        assert kwargs["team_subs_df"] == "team_subs_for_customer_counting"
        assert kwargs["csconsole_action_plans"] == "filtered_action_plans"
        assert kwargs["csconsole_customer_pulse"] == "filtered_customer_pulse"
        assert kwargs["csconsole_success_priorities"] == (
            "filtered_success_priorities"
        )

    ap_fetch_calls = _function_calls(
        app_simple.run_comprehensive_analysis,
        "_r65_fetch_aps_snowflake",
    )
    assert ap_fetch_calls
    assert {
        ast.unparse(call.args[1]) for call in ap_fetch_calls if len(call.args) > 1
    } == {"_comprehensive_scoped_account_ids"}

    source = inspect.getsource(app_simple.run_comprehensive_analysis)
    assert source.rfind("Final criteria-scoped Comprehensive source frames validated") > source.rfind(
        "_r65_fetch_aps_snowflake("
    )


def test_validator_allows_only_explicit_honest_scoped_subscription_zero() -> None:
    valid, missing, details = validate_data_sources_for_report(
        report_type="comprehensive",
        snowflake_ctx=object(),
        team_subs_df=pd.DataFrame(),
        ab_data=pd.DataFrame(),
        csone_data=pd.DataFrame(),
        required_sources=["team_subscriptions"],
        allow_empty_required_sources=["team_subscriptions"],
    )

    assert valid is True
    assert missing == []
    assert details == {}

    unavailable = pd.DataFrame()
    unavailable.attrs["fetch_error"] = "subscription query timed out"
    valid, missing, details = validate_data_sources_for_report(
        report_type="comprehensive",
        snowflake_ctx=object(),
        team_subs_df=unavailable,
        ab_data=pd.DataFrame(),
        csone_data=pd.DataFrame(),
        required_sources=["team_subscriptions"],
        allow_empty_required_sources=["team_subscriptions"],
    )

    assert valid is False
    assert missing == ["team_subscriptions"]
    assert "UNAVAILABLE" in details["team_subscriptions"]


def test_subscription_gate_validates_exact_rendered_sourced_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = (
        "The selected subscription has one verified risk signal. "
        "[Source: grounded subscription briefing book]"
    )
    captured = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return {"is_valid": True, "errors": [], "warnings": []}

    monkeypatch.setattr(app_simple, "validate_report_consistency", _capture)
    result = app_simple._r162_validate_subscription_report_consistency(
        ab_df=pd.DataFrame(),
        tac_df=pd.DataFrame(),
        customer_pulse_df=pd.DataFrame(),
        rendered_ai_response=rendered,
        strict_mode=False,
    )

    assert result["is_valid"] is True
    assert captured["factual_claims"] == [rendered]


def test_subscription_gate_rejects_unsourced_or_broken_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="missing inline source attribution"):
        app_simple._r162_validate_subscription_report_consistency(
            ab_df=pd.DataFrame(),
            tac_df=pd.DataFrame(),
            customer_pulse_df=pd.DataFrame(),
            rendered_ai_response="One factual claim without a citation",
            strict_mode=False,
        )

    def _raise(*args, **kwargs):
        raise RuntimeError("validator unavailable")

    monkeypatch.setattr(app_simple, "validate_report_consistency", _raise)
    with pytest.raises(RuntimeError, match="validator unavailable"):
        app_simple._r162_validate_subscription_report_consistency(
            ab_df=pd.DataFrame(),
            tac_df=pd.DataFrame(),
            customer_pulse_df=pd.DataFrame(),
            rendered_ai_response="Claim [Source: source data]",
            strict_mode=False,
        )


def test_subscription_worker_has_no_consistency_fail_open() -> None:
    source = inspect.getsource(app_simple.run_subscription_analysis)

    assert "_r162_validate_subscription_report_consistency(" in source
    assert "rendered_ai_response=_r71_rendered_ai_claim" in source
    assert "Subscription consistency check skipped" not in source
    assert "report delivery blocked" in source
