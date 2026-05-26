"""Round 98 accuracy/stability scrub regression coverage."""

from __future__ import annotations

import pandas as pd


def test_round98_renewal_scoring_threads_pulse_ap_and_normalized_subs() -> None:
    from app_simple import _calculate_simple_renewal_risk

    result = _calculate_simple_renewal_risk(
        customer_name="Acme Inc",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        team_subs_df=pd.DataFrame([
            {"BU_NAME": "ACME, INC.", "ANNUAL_CONTRACT_VALUE": 1_000_000},
        ]),
        days=90,
        customer_pulse=pd.DataFrame([
            {"BU_NAME": "ACME, INC.", "SCORE__C": 2},
        ]),
        customer_action_plans=pd.DataFrame([
            {"BU_NAME": "ACME, INC.", "STATUS_C": "Open"},
        ]),
    )

    components = result["risk_components"]
    assert components["customer_pulse"]["score"] > 0
    assert components["action_plans"]["score"] > 0


def test_round98_customer_slice_normalizes_punctuation_case_suffixes() -> None:
    from app_simple import _r98_slice_customer_frame

    frame = pd.DataFrame([
        {"customer_name": "ACME, INC.", "value": 1},
        {"customer_name": "Other LLC", "value": 2},
    ])

    sliced = _r98_slice_customer_frame(frame, "Acme Inc", ("customer_name",))

    assert sliced["value"].tolist() == [1]


def test_round98_csone_scope_fallback_uses_normalized_customer_names() -> None:
    from adoptiq_backend import _apply_scope_filter_csone

    df = pd.DataFrame([
        {
            "Subscription ID": "N/A",
            "customer_name": "ACME, INC.",
            "Technology": "Contact Center",
            "Sub Technology": "Webex Contact Center",
        },
        {
            "Subscription ID": "N/A",
            "customer_name": "Other",
            "Technology": "Contact Center",
            "Sub Technology": "Webex Contact Center",
        },
    ])

    scoped = _apply_scope_filter_csone(
        df,
        "All Contact Center",
        90,
        sub_ids=[],
        team_customer_names=["Acme Inc"],
    )

    assert scoped["customer_name"].tolist() == ["ACME, INC."]


def test_round98_named_tech_ab_filter_does_not_widen_on_zero_match() -> None:
    from adoptiq_backend import _apply_scope_filter_ab
    from app_simple import _r93_ab_scope_warning_entries

    df = pd.DataFrame([
        {"PRODUCT_C": "Webex Meetings", "SUBJECT_C": "Meetings issue"},
    ])

    scoped = _apply_scope_filter_ab(df, "Webex Calling", 90)

    assert scoped.empty
    assert scoped.attrs.get("tech_filter_empty_after_scope") is True
    warnings = _r93_ab_scope_warning_entries(scoped)
    assert warnings
    assert warnings[0]["kind"] == "tech_filter_empty_after_scope"
