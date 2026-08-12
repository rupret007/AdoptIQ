"""Round 162.2 Renewal all-source and criteria-scope accuracy tests."""

from __future__ import annotations

import inspect

import pandas as pd

import app_simple
import canonical_metrics as cm


def test_subscription_scope_filters_all_contact_center_and_marks_unknown_partial() -> None:
    subscriptions = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["A-WXCC", "A-MEET", "A-UNKNOWN"],
            "BU_NAME": ["Contact Customer", "Meetings Customer", "Unknown Customer"],
            "TECHNOLOGY_C": ["Webex Contact Center", "Webex Meetings", "Unknown"],
            "SUB_TECHNOLOGY_C": ["WxCC", "Webex Meetings", ""],
        }
    )

    scoped = app_simple._r162_scope_subscription_customers(
        subscriptions,
        "All Contact Center",
    )

    assert scoped["ACCOUNT_ID_C"].tolist() == ["A-WXCC"]
    assert scoped.attrs["technology_scope_unknown_excluded"] == 1
    assert scoped.attrs["partial"] is True


def test_subscription_scope_with_missing_authoritative_fields_is_unavailable() -> None:
    subscriptions = pd.DataFrame(
        {"ACCOUNT_ID_C": ["A-1"], "BU_NAME": ["Manager Wide Customer"]}
    )

    scoped = app_simple._r162_scope_subscription_customers(
        subscriptions,
        "Webex Contact Center",
    )
    warnings = app_simple._r162_renewal_source_scope_warning_entries(
        scoped,
        dataset="team_subscriptions",
        technology="Webex Contact Center",
    )

    assert scoped.empty
    assert cm.source_data_state(scoped)["state"] == "unavailable"
    assert warnings == [
        {
            "dataset": "team_subscriptions",
            "kind": "technology_scope_unavailable",
            "error": "subscription technology fields were unavailable for this named-technology scope",
        }
    ]


def test_csconsole_scope_enforces_member_and_named_technology_boundaries() -> None:
    source = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["A-1", "A-2", "OUTSIDE"],
            "BU_NAME": ["In Scope CC", "In Scope Meetings", "Outside CC"],
            "TECHNOLOGY_C": [
                "Webex Contact Center",
                "Webex Meetings",
                "Webex Contact Center",
            ],
            "SUB_TECHNOLOGY_C": ["WxCC", "Webex Meetings", "WxCC"],
        }
    )

    scoped = app_simple._r162_scope_renewal_csconsole_source(
        source,
        "Webex Contact Center",
        customer_names=["In Scope CC", "In Scope Meetings"],
        account_ids=["A-1", "A-2"],
        dataset="customer_pulse",
    )

    assert scoped["ACCOUNT_ID_C"].tolist() == ["A-1"]
    assert scoped.attrs["technology_scope_total"] == 2
    assert scoped.attrs["technology_scope_matched"] == 1


def test_csconsole_missing_authoritative_technology_is_withheld_and_disclosed() -> None:
    source = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["A-1"],
            "BU_NAME": ["Customer One"],
            "SUBJECT_C": ["Customer success plan"],
        }
    )

    scoped = app_simple._r162_scope_renewal_csconsole_source(
        source,
        "Webex Contact Center",
        customer_names=["Customer One"],
        account_ids=["A-1"],
        dataset="success_priorities",
    )
    warnings = app_simple._r162_renewal_source_scope_warning_entries(
        scoped,
        dataset="success_priorities",
        technology="Webex Contact Center",
    )

    assert scoped.empty
    assert cm.source_data_state(scoped)["state"] == "unavailable"
    assert warnings[0]["kind"] == "technology_scope_unavailable"
    assert "authoritative technology fields were unavailable" in warnings[0]["error"]


def test_csconsole_missing_customer_boundary_fields_is_withheld() -> None:
    source = pd.DataFrame(
        {
            "TECHNOLOGY_C": ["Webex Contact Center"],
            "SUB_TECHNOLOGY_C": ["WxCC"],
            "SUBJECT_C": ["Organisation-wide row with no customer key"],
        }
    )

    scoped = app_simple._r162_scope_renewal_csconsole_source(
        source,
        "Webex Contact Center",
        customer_names=["Customer One"],
        account_ids=["A-1"],
        dataset="action_plans",
    )

    assert scoped.empty
    assert cm.source_data_state(scoped)["state"] == "unavailable"
    assert "manager/member boundary could not be validated" in scoped.attrs[
        "source_unavailable_detail"
    ]


def test_csconsole_scope_exception_fails_closed(monkeypatch) -> None:
    source = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["A-1"],
            "BU_NAME": ["Customer One"],
            "TECHNOLOGY_C": ["Webex Contact Center"],
        }
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("scope matcher unavailable")

    monkeypatch.setattr(app_simple, "_filter_csconsole_data_by_technology", _raise)
    scoped = app_simple._r162_scope_renewal_csconsole_source(
        source,
        "Webex Contact Center",
        customer_names=["Customer One"],
        account_ids=["A-1"],
        dataset="action_plans",
    )

    assert scoped.empty
    assert cm.source_data_state(scoped)["state"] == "unavailable"
    assert "customer/member scope could not be validated" in scoped.attrs[
        "source_unavailable_detail"
    ]


def test_renewal_headline_union_counts_all_six_applicable_sources() -> None:
    subscriptions = pd.DataFrame(
        {"ACCOUNT_ID_C": ["SUB-1"], "BU_NAME": ["Subscription Only"]}
    )
    action_plans = pd.DataFrame(
        {"ACCOUNT_ID_C": ["AP-1"], "BU_NAME": ["Action Plan Only"]}
    )
    barriers = pd.DataFrame(
        {"ACCOUNT_ID_C": ["AB-1"], "customer_name": ["Barrier Only"], "ID": ["AB-1"]}
    )
    pulse = pd.DataFrame(
        {"ACCOUNT_ID_C": ["PULSE-1"], "BU_NAME": ["Pulse Only"]}
    )
    tac = pd.DataFrame(
        {"ACCOUNT_ID_C": ["TAC-1"], "customer_name": ["TAC Only"], "Case #": ["SR-1"]}
    )
    success_priorities = pd.DataFrame(
        {"ACCOUNT_ID_C": ["SP-1"], "RELATED_CUSTOMER__C": ["Priority Only"]}
    )

    customers = app_simple._r162_renewal_all_source_customers(
        subscriptions=subscriptions,
        action_plans=action_plans,
        adoption_barriers=barriers,
        customer_pulse=pulse,
        tac_cases=tac,
        success_priorities=success_priorities,
    )

    assert len(customers) == 6


def test_renewal_headline_union_collapses_renamed_label_on_same_account_id() -> None:
    subscriptions = pd.DataFrame(
        {"ACCOUNT_ID_C": ["A-1"], "BU_NAME": ["Legacy Brand"]}
    )
    barriers = pd.DataFrame(
        {"ACCOUNT_ID_C": ["A-1"], "customer_name": ["New Brand"], "ID": ["AB-1"]}
    )

    customers = app_simple._r162_renewal_all_source_customers(
        subscriptions=subscriptions,
        adoption_barriers=barriers,
    )

    assert len(customers) == 1


def test_renewal_identity_slice_keeps_renamed_evidence_on_same_account_id() -> None:
    from decision_report_delivery import _customer_frame_for_identity

    subscriptions = pd.DataFrame(
        {"ACCOUNT_ID_C": ["A-1"], "BU_NAME": ["Legacy Brand"]}
    )
    barriers = pd.DataFrame(
        {"ACCOUNT_ID_C": ["A-1"], "customer_name": ["New Brand"], "ID": ["AB-1"]}
    )
    identities, frames = app_simple._r162_renewal_identity_context(
        subscriptions=subscriptions,
        adoption_barriers=barriers,
    )

    assert len(identities) == 1
    assert identities[0]["label"] == "Legacy Brand"
    sliced = _customer_frame_for_identity(
        frames["adoption_barriers"], identities[0], identities
    )
    assert sliced["ID"].tolist() == ["AB-1"]


def test_success_priorities_are_evidence_context_not_invented_score_weight(monkeypatch) -> None:
    profile = {
        "risk_score_0_100": 12.0,
        "risk_score_0_10": 1.2,
        "risk_band": "HEALTHY",
        "key_findings": [],
        "risk_factors": [],
        "recommendations": [],
        "components": {
            "adoption_barriers": {"score": 0.0, "details": {}},
            "support_cases": {
                "score": 0.0,
                "details": {"break_fix_count": 0, "provisioning_count": 0},
            },
            "customer_pulse": {"score": 0.0, "details": {}},
            "action_plans": {"score": 0.0, "details": {}},
            "incidents": {"score": 0.0, "details": {"high_impact_count": 0}},
            "contract": {"score": None, "details": {}},
            "engagement": {"score": 0.0, "details": {}},
        },
    }
    monkeypatch.setattr(app_simple, "compute_customer_risk_profile", lambda **kwargs: profile)
    priorities = pd.DataFrame(
        {"ID": ["SP-1", "SP-1", "SP-2"], "RELATED_CUSTOMER__C": ["A", "A", "A"]}
    )

    result = app_simple._calculate_simple_renewal_risk(
        customer_name="A",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        team_subs_df=pd.DataFrame(),
        days=90,
        customer_success_priorities=priorities,
    )

    assert result["renewal_risk_score"] == 12.0
    assert result["success_priorities_count"] == 2
    assert "2 Success Priorities recorded" in result["key_findings"][-1]
    assert "[Source: CSConsole Success Priorities;" in result["key_findings"][-1]


def test_renewal_worker_never_uses_technology_only_csone_widening_fallback() -> None:
    source = inspect.getsource(app_simple.run_customer_renewal_analysis)

    assert "_apply_scope_filter_csone_inclusive" not in source
    assert "keeping the empty scoped result instead of widening" in source
    assert "cm.merge_adoption_barrier_sources(" in source
