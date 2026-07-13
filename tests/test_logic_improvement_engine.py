"""Adversarial synthetic tests for the evidence-grounded logic improvements."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import canonical_metrics as cm
import data_normalization as dn
from ai_narrative_validator import validate_grounded_numbers
from data_normalization import (
    build_customer_lookup,
    partition_customer_frame,
    resolve_customer_name,
    slice_customer_frame,
)
from risk_scoring import compute_customer_risk_profile


ROOT = Path(__file__).resolve().parent.parent


def test_tac_fanout_is_one_logical_case_and_null_ids_remain_independent() -> None:
    cases = pd.DataFrame(
        [
            {
                "Case #": "CSC-1",
                "Status": "Open",
                "Severity": "P1",
                "LAST_MODIFIED_DATE": "2025-01-01T00:00:00Z",
                "Transaction ID": "BEMS-100",
            },
            {
                "Case #": "CSC-1",
                "Status": "Closed",
                "Severity": "P3",
                "LAST_MODIFIED_DATE": "2026-01-01T00:00:00Z",
                "Transaction ID": "",
            },
            {"Case #": None, "Status": "Open", "Severity": "P4"},
            {"Case #": "", "Status": "Open", "Severity": "P4"},
        ]
    )

    logical = cm.deduplicate_tac_cases(cases)

    assert len(logical) == 3
    assert cm.count_total_tac(cases) == 3
    assert cm.count_p1(cases) == 0
    assert cm.count_p3(cases) == 1
    assert cm.count_open_tac(cases) == 2
    assert cm.count_bems(cases) == 0
    assert logical.attrs["tac_dedup"]["duplicates_removed"] == 1
    assert logical.attrs["tac_dedup"]["conflicting_case_ids"] == ["CSC-1"]


def test_action_plan_negated_completion_is_open_and_duplicate_state_is_current() -> None:
    plans = pd.DataFrame(
        [
            {"ID": "AP-1", "STATUS_C": "Completed", "LAST_MODIFIED_DATE": "2025-01-01"},
            {"ID": "AP-1", "STATUS_C": "Incomplete", "LAST_MODIFIED_DATE": "2026-01-01"},
            {"ID": "AP-2", "STATUS_C": "Not done", "LAST_MODIFIED_DATE": "2026-01-02"},
        ]
    )

    profile = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=plans,
        customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
        ext_incidents=[],
    )
    details = profile["components"]["action_plans"]["details"]

    assert details["count"] == 2
    assert details["unresolved_count"] == 2
    assert details["duplicates_removed"] == 1
    assert any("Assign owners and due dates" in rec for rec in profile["recommendations"])
    action = next(
        item
        for item in profile["next_best_actions"]
        if "Assign owners and due dates" in item["action"]
    )
    assert action["likely_owner"]
    assert action["urgency"] == "This week"
    assert action["expected_outcome"]
    assert action["confidence"] == profile["evidence_quality"]["confidence_band"]


def test_pulse_uses_row_level_text_fallback_and_shared_backfill_flags() -> None:
    pulse = pd.DataFrame(
        [
            {"ID": "P-1", "SCORE__C": 9, "PULSE_RATING__C": "Good"},
            {"ID": "P-2", "SCORE__C": None, "PULSE_RATING__C": "Poor"},
            {
                "ID": "P-3",
                "SCORE__C": 1,
                "PULSE_RATING__C": "Poor",
                "is_backfilled": True,
            },
        ]
    )
    profile = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        customer_pulse=pulse,
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
        ext_incidents=[],
    )
    details = profile["components"]["customer_pulse"]["details"]

    assert details["count"] == 2
    assert details["poor_bad_count"] == 1
    assert details["backfill_excluded_count"] == 1


def test_customer_pulse_latest_state_is_reorder_invariant_and_keeps_blank_ids() -> None:
    rows = [
        {
            "ID": "P-1",
            "SCORE__C": 2,
            "PULSE_RATING__C": "Poor",
            "LAST_MODIFIED_DATE": "2026-07-01T00:00:00Z",
        },
        {
            "ID": "P-1",
            "SCORE__C": 9,
            "PULSE_RATING__C": "Good",
            "LAST_MODIFIED_DATE": "2025-07-01T00:00:00Z",
        },
        {"ID": None, "SCORE__C": 8, "PULSE_RATING__C": "Good"},
        {"ID": "", "SCORE__C": 6, "PULSE_RATING__C": "Neutral"},
    ]

    forward = cm.deduplicate_customer_pulse(pd.DataFrame(rows))
    reversed_order = cm.deduplicate_customer_pulse(
        pd.DataFrame(list(reversed(rows)))
    )

    for logical in (forward, reversed_order):
        assert len(logical) == 3
        current = logical[logical["ID"].fillna("").eq("P-1")].iloc[0]
        assert current["SCORE__C"] == 2
        assert current["PULSE_RATING__C"] == "Poor"
        assert logical["ID"].fillna("").eq("").sum() == 2
        assert logical.attrs["customer_pulse_dedup"]["duplicates_removed"] == 1


def test_pulse_count_sentiment_and_risk_share_latest_logical_records() -> None:
    rows = [
        {
            "ID": "P-1",
            "SCORE__C": 2,
            "LAST_MODIFIED_DATE": "2026-07-01T00:00:00Z",
        },
        {
            "ID": "P-1",
            "SCORE__C": 9,
            "LAST_MODIFIED_DATE": "2025-07-01T00:00:00Z",
        },
        {"ID": None, "SCORE__C": 8},
        {"ID": "", "SCORE__C": 6},
    ]
    variants = [pd.DataFrame(rows), pd.DataFrame(list(reversed(rows)))]
    component_results = []

    for pulse in variants:
        assert cm.count_total_customer_pulse(pulse) == 3
        sentiment = cm.pulse_sentiment(pulse)
        assert sentiment == {
            "count": 3,
            "mean_0_to_10": 5.33,
            "positive": 1,
            "neutral": 1,
            "negative": 1,
            "sentiment": "Neutral",
            "has_backfill_flag": False,
        }
        profile = compute_customer_risk_profile(
            "Acme",
            customer_ab=pd.DataFrame(),
            customer_csone=pd.DataFrame(),
            customer_pulse=pulse,
            customer_action_plans=pd.DataFrame(),
            customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
            ext_incidents=[],
        )
        component_results.append(profile["components"]["customer_pulse"])

    assert component_results[0] == component_results[1]
    assert round(component_results[0]["score"], 6) == round(130 / 3, 6)
    assert component_results[0]["details"]["duplicates_removed"] == 1


def test_latest_backfill_pulse_does_not_restore_a_stale_observed_duplicate() -> None:
    pulse = pd.DataFrame(
        [
            {
                "ID": "P-1",
                "SCORE__C": 2,
                "LAST_MODIFIED_DATE": "2026-07-01T00:00:00Z",
                "is_backfilled": True,
            },
            {
                "ID": "P-1",
                "SCORE__C": 9,
                "LAST_MODIFIED_DATE": "2025-07-01T00:00:00Z",
                "is_backfilled": False,
            },
        ]
    )

    sentiment = cm.pulse_sentiment(pulse)
    assert sentiment["customer_observed"]["count"] == 0
    assert sentiment["backfilled_excluded_count"] == 1

    profile = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        customer_pulse=pulse,
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
        ext_incidents=[],
    )
    pulse_details = profile["components"]["customer_pulse"]["details"]
    assert pulse_details["data_state"] == "backfill_only"
    assert pulse_details["duplicates_removed"] == 1
    assert profile["components"]["engagement"]["details"]["total_activity"] == 0


def test_unusable_pulse_is_excluded_instead_of_diluting_support_risk() -> None:
    case = pd.DataFrame(
        [
            {
                "Case #": "CSC-1",
                "Status": "Open",
                "Severity": "P2",
                "Date/Time Opened": datetime.now(timezone.utc).isoformat(),
            }
        ]
    )
    common = {
        "customer_ab": pd.DataFrame(),
        "customer_csone": case,
        "customer_action_plans": pd.DataFrame(),
        "customer_subs": pd.DataFrame([{"STATUS_C": "Active"}]),
        "ext_incidents": [],
    }
    absent = compute_customer_risk_profile("Acme", customer_pulse=None, **common)
    unusable = compute_customer_risk_profile(
        "Acme", customer_pulse=pd.DataFrame([{"COMMENTS__C": "No rating"}]), **common
    )

    assert unusable["components"]["customer_pulse"]["score"] is None
    assert unusable["risk_score_0_100"] == absent["risk_score_0_100"]


def test_active_p1_bems_cannot_be_healthy_and_actions_are_evidence_specific() -> None:
    case = pd.DataFrame(
        [
            {
                "Case #": "CSC-1",
                "Status": "Open",
                "Severity": "P1",
                "Date/Time Opened": datetime.now(timezone.utc).isoformat(),
                "Transaction ID": "BEMS-42",
            }
        ]
    )
    profile = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame(),
        customer_csone=case,
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
        ext_incidents=[],
    )

    assert profile["risk_score_0_100"] >= 55
    assert profile["risk_band"] in {"HIGH", "CRITICAL"}
    assert set(profile["guardrail_reasons"]) == {
        "active P1 support case",
        "active BEMS escalation",
    }
    assert any("active P1" in rec for rec in profile["recommendations"])
    assert any("active BEMS" in rec for rec in profile["recommendations"])


def test_combined_negative_pulse_and_escalated_case_produce_joined_recovery_action() -> None:
    case = pd.DataFrame(
        [
            {
                "Case #": "CSC-1",
                "Status": "Open",
                "Severity": "P2",
                "Date/Time Opened": datetime.now(timezone.utc).isoformat(),
            }
        ]
    )
    pulse = pd.DataFrame([{"ID": "P-1", "PULSE_RATING__C": "Poor"}])

    profile = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame(),
        customer_csone=case,
        customer_pulse=pulse,
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
        ext_incidents=[],
    )
    joined = [
        item
        for item in profile["next_best_actions"]
        if "joins active P1/P2" in item["action"]
    ]

    assert len(joined) == 1
    assert joined[0]["evidence_sources"] == [
        "Customer Pulse",
        "Support Cases (TAC)",
    ]
    assert joined[0]["likely_owner"] == "Customer Success Manager and TAC case owner"


def test_old_closed_p1_bems_is_history_not_current_urgency() -> None:
    case = pd.DataFrame(
        [
            {
                "Case #": "CSC-OLD",
                "Status": "Closed",
                "Severity": "P1",
                "Date/Time Opened": "2020-01-01T00:00:00Z",
                "Date/Time Closed": "2020-01-02T00:00:00Z",
                "Transaction ID": "BEMS-OLD",
            }
        ]
    )
    profile = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame(),
        customer_csone=case,
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
        ext_incidents=[],
    )

    support = profile["components"]["support_cases"]["details"]
    assert support["bems_count"] == 1
    assert support["active_bems_count"] == 0
    assert support["active_p1_count"] == 0
    assert profile["guardrail_floor"] == 0
    assert not any("BEMS" in rec for rec in profile["recommendations"])


def test_barrier_latest_state_wins_over_old_open_critical_history() -> None:
    barriers = pd.DataFrame(
        [
            {
                "ID": "AB-1",
                "SEVERITY_C": "Critical",
                "AB_STATUS_C": "Open",
                "LAST_MODIFIED_DATE": "2025-01-01T00:00:00Z",
            },
            {
                "ID": "AB-1",
                "SEVERITY_C": "Low",
                "AB_STATUS_C": "Closed",
                "LAST_MODIFIED_DATE": "2026-01-01T00:00:00Z",
            },
        ]
    )
    profile = compute_customer_risk_profile(
        "Acme",
        customer_ab=barriers,
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
        ext_incidents=[],
    )
    details = profile["components"]["adoption_barriers"]["details"]

    assert details["count"] == 1
    assert details["open_count"] == 0
    assert details["critical_count"] == 0
    assert details["history_conflict_ids"] == ["AB-1"]
    assert profile["guardrail_floor"] == 0


def test_ambiguous_account_only_identity_fails_closed() -> None:
    subs = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Alpha Corp"},
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Zeta Corp"},
        ]
    )
    lookup = build_customer_lookup(subs)
    account_only = pd.Series({"ACCOUNT_ID_C": "A-1"})
    explicit = pd.Series({"ACCOUNT_ID_C": "A-1", "BU_NAME": "Zeta Corp"})

    assert resolve_customer_name(account_only, lookup) == "Unknown"
    assert resolve_customer_name(explicit, lookup) == "Zeta Corp"
    sliced = slice_customer_frame(
        pd.DataFrame([account_only]), "Alpha Corp", customer_lookup=lookup
    )
    assert sliced.empty


def test_customer_frames_are_partitioned_with_one_identity_pass(monkeypatch) -> None:
    subs = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Acme Corp"},
            {"ACCOUNT_ID_C": "A-2", "BU_NAME": "Beta LLC"},
        ]
    )
    cases = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "A-1", "Case #": "C-1"},
            {"ACCOUNT_ID_C": "A-1", "Case #": "C-2"},
            {"ACCOUNT_ID_C": "A-2", "Case #": "C-3"},
        ]
    )
    lookup = build_customer_lookup(subs)
    original = dn.resolve_customer_name
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dn, "resolve_customer_name", counted)
    partitions = partition_customer_frame(cases, customer_lookup=lookup)

    assert calls == len(cases)
    assert partitions["acme"]["Case #"].tolist() == ["C-1", "C-2"]
    assert partitions["beta"]["Case #"].tolist() == ["C-3"]


def test_cross_customer_duplicate_ids_are_quarantined_before_assignment() -> None:
    cases = pd.DataFrame(
        [
            {"Case #": "CSC-SHARED", "customer_name": "Alpha", "Status": "Open", "Severity": "P1"},
            {"Case #": "CSC-SHARED", "customer_name": "Beta", "Status": "Closed", "Severity": "P3"},
        ]
    )
    pulse = pd.DataFrame(
        [
            {"ID": "P-SHARED", "BU_NAME": "Alpha", "SCORE__C": 2},
            {"ID": "P-SHARED", "BU_NAME": "Beta", "SCORE__C": 9},
        ]
    )

    logical_cases = cm.deduplicate_tac_cases(cases)
    logical_pulse = cm.deduplicate_customer_pulse(pulse)

    assert logical_cases.empty
    assert logical_cases.attrs["tac_dedup"]["cross_customer_conflict_ids"] == [
        "CSC-SHARED"
    ]
    assert logical_cases.attrs["tac_dedup"]["cross_customer_rows_quarantined"] == 2
    assert logical_pulse.empty
    assert logical_pulse.attrs["customer_pulse_dedup"][
        "cross_customer_conflict_ids"
    ] == ["P-SHARED"]
    assert partition_customer_frame(cases) == {}
    assert partition_customer_frame(pulse) == {}


def test_numeric_grounding_rejects_common_but_unproven_claims() -> None:
    briefing = "Total customers: 7. Support cases: 2."
    result = validate_grounded_numbers(
        "99 customers are at risk; 500 cases are critical; churn is 99.9%.",
        briefing,
    )

    assert not result.is_valid
    offending = result.sample_offending["ungrounded_number"]
    assert "99" in offending
    assert "500" in offending
    assert "99.9%" in offending


def test_numeric_grounding_accepts_literal_and_provable_ratio() -> None:
    literal = validate_grounded_numbers(
        "Renewal exposure is 47.3%.", "Canonical renewal exposure: 47.3%."
    )
    ratio = validate_grounded_numbers(
        "2 of 7 customers represent 28.6%.",
        "Affected customers: 2. Total customers: 7.",
    )

    assert literal.is_valid
    assert ratio.is_valid


def test_numeric_grounding_inherits_compact_kpi_context_without_crossing_units() -> None:
    result = validate_grounded_numbers(
        "24 adoption barriers (7 critical, 18 still open); $500,000 is 40% of ARR at risk.",
        "Adoption barriers total: 24; critical: 7; open: 18. ARR at risk: $500,000 (40% of at-risk total).",
    )

    assert result.is_valid


def test_numeric_grounding_handles_lowercase_money_suffixes_without_prefix_escape() -> None:
    result_m = validate_grounded_numbers(
        "ARR exposure is $5.7m.",
        "Five segments were reviewed: 5.",
    )
    result_bn = validate_grounded_numbers(
        "ARR exposure is $2.4bn.",
        "Two segments were reviewed: 2.",
    )

    assert not result_m.is_valid
    assert "5.7m" in result_m.sample_offending["ungrounded_number"]
    assert not result_bn.is_valid
    assert "2.4bn" in result_bn.sample_offending["ungrounded_number"]


def test_duration_claim_is_not_mistaken_for_a_structural_window() -> None:
    duration = validate_grounded_numbers(
        "The outage lasted 999 days.",
        "No outage duration was supplied.",
    )
    window = validate_grounded_numbers(
        "Review the next 90 days.",
        "No analysis-window number was supplied.",
    )

    assert not duration.is_valid
    assert window.is_valid


def test_large_top_n_is_not_a_free_numeric_grounding_bypass() -> None:
    result = validate_grounded_numbers(
        "Review the top 999 customers.",
        "No portfolio size was supplied.",
    )

    assert not result.is_valid
    assert "999" in result.sample_offending["ungrounded_number"]


def test_numeric_grounding_parses_scientific_mm_and_word_money_units() -> None:
    for claim in ("$2.5e9 ARR", "$1.2MM ARR", "$5.7 billion ARR", "$5.7 million ARR"):
        result = validate_grounded_numbers(claim, "Canonical ARR: $1,000,000.")
        assert not result.is_valid, claim
        assert result.sample_offending["ungrounded_number"], claim


def test_action_plan_unknown_status_is_not_scored_as_healthy_or_open() -> None:
    for plans in (
        pd.DataFrame([{"ID": "AP-1"}]),
        pd.DataFrame([{"ID": "AP-1", "STATUS_C": ""}]),
        pd.DataFrame([{"ID": "AP-1", "STATUS_C": "garbage"}]),
    ):
        profile = compute_customer_risk_profile(
            "Acme",
            customer_ab=pd.DataFrame(),
            customer_csone=pd.DataFrame(),
            customer_pulse=pd.DataFrame(),
            customer_action_plans=plans,
            customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
            ext_incidents=[],
        )
        details = profile["components"]["action_plans"]["details"]

        assert profile["components"]["action_plans"]["score"] is None
        assert details["unknown_status_count"] == 1
        assert details["excluded_from_score"] is True
        assert cm.count_open_action_plans(None, ap_df=plans) == 0


def test_fetch_failed_empty_frames_are_unavailable_not_healthy_zeroes() -> None:
    failed_frames = []
    for _ in range(4):
        frame = pd.DataFrame()
        frame.attrs["fetch_error"] = "upstream timeout"
        frame.attrs["fetch_error_kind"] = "timeout"
        failed_frames.append(frame)

    failed = compute_customer_risk_profile(
        "Acme",
        customer_ab=failed_frames[0],
        customer_csone=failed_frames[1],
        customer_pulse=failed_frames[2],
        customer_action_plans=failed_frames[3],
        customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
        ext_incidents=[],
    )
    observed_empty = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame([{"STATUS_C": "Active"}]),
        ext_incidents=[],
    )

    assert failed["risk_score_0_100"] is None
    assert failed["risk_band"] == "UNKNOWN"
    assert failed["risk_assessment_state"] == "INSUFFICIENT_EVIDENCE"
    assert failed["evidence_quality"]["confidence_band"] == "LOW"
    assert failed["components"]["support_cases"]["details"]["data_state"] == "missing"
    assert observed_empty["risk_score_0_100"] == 0
    assert observed_empty["risk_band"] == "HEALTHY"

    from compact_report_formatter import _compact_portfolio_band
    from risk_scoring import (
        compute_portfolio_risk_summary,
        health_grade_for_profile,
        portfolio_health_grade,
    )

    portfolio = compute_portfolio_risk_summary({"Acme": failed})
    assert health_grade_for_profile(failed) == "N/A"
    assert portfolio["scored_customers"] == 0
    assert portfolio["unknown_risk_customers"] == 1
    assert portfolio["average_risk_score_0_100"] is None
    assert portfolio["highest_risk_score_0_100"] is None
    assert portfolio_health_grade(portfolio) == "N/A"
    assert _compact_portfolio_band(None)["tier"] == "UNKNOWN"


def test_cross_customer_scoring_and_single_renewal_wiring_are_source_guarded() -> None:
    ask_source = (ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    app_source = (ROOT / "app_simple.py").read_text(encoding="utf-8")

    assert "cm.list_customers(" in ask_source
    assert "_partition_customer_frame(" in ask_source
    assert ask_source.index("_customer_frame_indexes =") < ask_source.index(
        "for _cust in sorted(_customer_universe)"
    )
    assert "customer_pulse=_cust_pulse" in ask_source
    assert "customer_action_plans=_cust_ap" in ask_source
    assert "customer_subs=_cust_subs" in ask_source
    single_start = app_source.index("def run_subscription_analysis")
    single_end = app_source.index("\n@app.route", single_start)
    single_window = app_source[single_start:single_end]
    assert "_decision_intelligence_v2_prepare(" in single_window
    assert "customer_pulse=cp_df" in single_window
    assert "action_plans=ap_df" in single_window
    assert "success_priorities=sp_df" in single_window
    assert "if _subscription_v2.get('bundle') is not None:" in single_window
    assert single_window.count(
        "renewal_analysis = get_subscription_renewal_risk(subscription_id, days)"
    ) == 1


def test_subscription_query_carries_scope_and_contract_fields() -> None:
    source = (ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    function_start = source.index("def get_subscriptions_for_team")
    function_window = source[function_start:function_start + 15000]

    for column in (
        "TECHNOLOGY_C",
        "SUB_TECHNOLOGY_C",
        "STATUS_C",
        "RENEWAL_RISK_CATEGORY",
    ):
        assert f'available_columns, "{column}"' in function_window
    assert "def filter_team_subscriptions_by_technology" in source


def test_citation_and_canonical_correction_guards_are_fail_closed() -> None:
    source = (ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    citation_start = source.index("def _validate_claim_citations")
    citation_window = source[citation_start:citation_start + 1800]
    correction_start = source.index("def _r95_apply_canonical_corrections")
    correction_window = source[correction_start:correction_start + 6000]

    assert "unknown_citations" in citation_window
    assert "unknown_statement_ids" in citation_window
    assert "quantities_ok" in citation_window
    assert "_r95_extract_answer_match(" in correction_window
    assert 'answer_match["value_start"]' in correction_window
    assert "conflicting answer text was replaced" in correction_window
    assert "answer stated" not in correction_window


def test_tac_equal_rank_ties_are_reorder_invariant():
    rows = [
        {
            "Case #": "CSC-TIE",
            "Status": "Open",
            "Severity": "P2",
            "LAST_MODIFIED_DATE": "2026-07-01T00:00:00Z",
            "Title": "outage investigation",
        },
        {
            "Case #": "CSC-TIE",
            "Status": "Open",
            "Severity": "P2",
            "LAST_MODIFIED_DATE": "2026-07-01T00:00:00Z",
            "Title": "provisioning request",
        },
    ]

    forward = cm.deduplicate_tac_cases(pd.DataFrame(rows))
    reversed_order = cm.deduplicate_tac_cases(pd.DataFrame(list(reversed(rows))))

    assert forward.iloc[0]["Title"] == reversed_order.iloc[0]["Title"]
    assert dn.add_case_lifecycle_fields(forward)["case_type_class"].tolist() == (
        dn.add_case_lifecycle_fields(reversed_order)["case_type_class"].tolist()
    )


@pytest.mark.parametrize(
    "status",
    [
        "Blocked",
        "Waiting",
        "Closed - Reopened",
        "Not Started",
        "Draft",
        "Planned",
        "Scheduled",
        "To Do",
    ],
)
def test_nonterminal_action_plan_statuses_are_open(status: str):
    assert dn.normalize_status_label(status) == "Open"


def test_low_completeness_sources_withhold_false_health():
    profile = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(
            [{"SCORE__C": 9}] + [{"SCORE__C": None}] * 99
        ),
        customer_action_plans=pd.DataFrame(
            [{"STATUS_C": "Closed"}] + [{"STATUS_C": "???"}] * 99
        ),
        customer_subs=pd.DataFrame(
            [{"STATUS_C": "Active", "RENEWAL_RISK_CATEGORY": "Low"}]
        ),
        ext_incidents=[],
    )

    assert profile["risk_band"] == "UNKNOWN"
    assert profile["risk_score_0_100"] is None
    assert profile["evidence_quality"]["confidence_band"] == "LOW"
    assert any("Validate the missing evidence" in item for item in profile["recommendations"])


@pytest.mark.parametrize(
    ("subscription", "incidents", "expected_band"),
    [
        (
            {"STATUS_C": "Active", "RENEWAL_RISK_CATEGORY": "Critical"},
            [],
            "HIGH",
        ),
        (
            {"STATUS_C": "Active", "RENEWAL_RISK_CATEGORY": "Low"},
            [
                {
                    "status": "major_outage",
                    "impact_level": "critical",
                    "customer_name": "Acme",
                }
            ],
            "HIGH",
        ),
    ],
)
def test_direct_renewal_and_incident_evidence_trigger_risk_guardrails(
    subscription: dict,
    incidents: list,
    expected_band: str,
):
    profile = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame([subscription]),
        ext_incidents=incidents,
    )

    assert profile["risk_band"] == expected_band
    assert profile["risk_score_0_100"] >= 55
    assert not any("Maintain the normal" in item for item in profile["recommendations"])


def test_global_untagged_incident_does_not_fan_out_customer_guardrail():
    global_incident = [{"status": "major_outage", "impact_level": "critical"}]
    common = {
        "customer_ab": pd.DataFrame(),
        "customer_csone": pd.DataFrame(),
        "customer_pulse": pd.DataFrame(),
        "customer_action_plans": pd.DataFrame(),
        "customer_subs": pd.DataFrame(
            [{"STATUS_C": "Active", "RENEWAL_RISK_CATEGORY": "Low"}]
        ),
        "ext_incidents": global_incident,
    }

    alpha = compute_customer_risk_profile("Alpha", **common)
    beta = compute_customer_risk_profile("Beta", **common)

    for profile in (alpha, beta):
        details = profile["components"]["incidents"]["details"]
        assert details["active_critical_impact_count"] == 1
        assert details["attributed_active_critical_impact_count"] == 0
        assert details["untagged_count"] == 1
        assert profile["risk_score_0_100"] < 55
        assert "active critical external incident" not in " ".join(
            profile["guardrail_reasons"]
        )
        assert not any(
            "critical-incident customer impact review" in item["action"]
            for item in profile["next_best_actions"]
        )


def test_positive_incident_evidence_cannot_lower_existing_customer_risk():
    common = {
        "customer_ab": pd.DataFrame(
            [{"AB_STATUS_C": "Open", "SEVERITY_C": "Critical"}]
        ),
        "customer_csone": pd.DataFrame(
            [{"Title": "Login", "Severity": "P1"}]
        ),
        "customer_pulse": None,
        "customer_action_plans": None,
        "customer_subs": None,
    }
    without_incidents = compute_customer_risk_profile(
        "Acme", ext_incidents=None, **common
    )
    with_incidents = compute_customer_risk_profile(
        "Acme",
        ext_incidents=[
            {
                "customer_name": "Acme",
                "title": f"Webex outage {index}",
                "status": "investigating",
            }
            for index in range(3)
        ],
        **common,
    )

    assert with_incidents["risk_score_0_100"] >= without_incidents["risk_score_0_100"]
    assert with_incidents["incident_risk_uplift"] > 0
