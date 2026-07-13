"""Round 95 / Phase B - Ask AI canonical metric self-check tests."""

from __future__ import annotations

from unittest.mock import patch


def test_round95_canonical_self_check_flags_total_customer_drift():
    from ask_ai_grounded import _r95_cross_check_answer_against_canonical

    result = _r95_cross_check_answer_against_canonical(
        "Total Customers: 47 across the selected portfolio.",
        {"manager": "All Managers"},
        {"total_customers": 39},
    )
    assert len(result.corrections) == 1
    assert result.corrections[0]["kpi"] == "total_customers"
    assert result.corrections[0]["llm_value"] == 47
    assert result.corrections[0]["canonical_value"] == 39


def test_round95_canonical_self_check_respects_arr_tolerance():
    from ask_ai_grounded import _r95_cross_check_answer_against_canonical

    result = _r95_cross_check_answer_against_canonical(
        "Total ARR: $1.01M for this scope.",
        {"manager": "All Managers"},
        {"total_arr": 1_000_000},
    )
    assert result.corrections == []
    assert "total_arr" in result.verified


def test_round95_arr_billion_suffix_is_verified_and_corrected_without_suffix_leak():
    from ask_ai_grounded import (
        _r95_apply_canonical_corrections,
        _r95_cross_check_answer_against_canonical,
    )

    verified = _r95_cross_check_answer_against_canonical(
        "Total ARR: $2.5B.", {}, {"total_arr": 2_500_000_000}
    )
    conflict = _r95_cross_check_answer_against_canonical(
        "Total ARR: $2.4bn.", {}, {"total_arr": 2_500_000_000}
    )
    corrected = _r95_apply_canonical_corrections(
        "Total ARR: $2.4bn.", conflict.corrections
    )

    assert verified.verified == ["total_arr"]
    assert conflict.corrections[0]["matched_value_text"].casefold() == "2.4bn"
    assert "Total ARR: $2,500,000,000." in corrected
    assert "2,500,000,000bn" not in corrected.casefold()


def test_round95_canonical_self_check_ignores_unchecked_numbers():
    from ask_ai_grounded import _r95_cross_check_answer_against_canonical

    result = _r95_cross_check_answer_against_canonical(
        "The answer references 47 escalations in a free-form note.",
        {"manager": "All Managers"},
        {"total_customers": 39},
    )
    assert result.corrections == []
    assert result.verified == []


def test_round95_canonical_self_check_does_not_treat_proximity_as_kpi_value():
    from ask_ai_grounded import _r95_cross_check_answer_against_canonical

    ranking = _r95_cross_check_answer_against_canonical(
        "Top 5 customers account for most activity.",
        {},
        {"total_customers": 53},
    )
    window = _r95_cross_check_answer_against_canonical(
        "Total customers increased over 90 days to 12.",
        {},
        {"total_customers": 53},
    )

    assert ranking.corrections == []
    assert window.corrections == []


def test_round95_canonical_corrections_append_callout():
    from ask_ai_grounded import _r95_apply_canonical_corrections

    answer = _r95_apply_canonical_corrections(
        "Total Customers: 47.",
        [{"kpi": "total_customers", "llm_value": 47, "canonical_value": 39, "delta_pct": 20.5}],
    )
    assert "Canonical Corrections" in answer
    assert "canonical value is 39" in answer
    assert "Total Customers: 39." in answer


def test_round95_canonical_correction_never_rewrites_ranking_or_window_number():
    from ask_ai_grounded import _r95_apply_canonical_corrections

    answer = _r95_apply_canonical_corrections(
        "Top 5 customers were reviewed over 90 days.",
        [{"kpi": "total_customers", "llm_value": 12, "canonical_value": 53}],
    )

    assert "Top 53" not in answer
    assert "over 53 days" not in answer
    assert "canonical value is 53" in answer


def test_round95_ask_ai_endpoint_threads_canonical_corrections(client):
    import app_simple

    grounded = {
        "ok": True,
        "answer": "Total Customers: 47.\n\n### Canonical Corrections\n- total_customers: answer stated 47; canonical value is 39.",
        "context_summary": "Data: test",
        "retrieval_diag": {"method": "hybrid", "rerank": "not_applied"},
        "canonical_headline": {"total_customers": 39},
        "canonical_corrections": [
            {"kpi": "total_customers", "llm_value": 47, "canonical_value": 39, "delta_pct": 20.5}
        ],
        "canonical_verified": [],
    }
    with patch.object(app_simple, "run_portfolio_grounded_ask_ai", return_value=grounded), \
         patch.object(app_simple, "_r74_generate_follow_up_suggestions", return_value=[]):
        resp = client.post(
            "/api/ask-ai-portfolio",
            json={"question": "How many customers?", "manager": "All Managers", "technology": "All", "days": 90},
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["canonical_corrections"][0]["kpi"] == "total_customers"


def test_round95_subset_metrics_are_not_rewritten_as_portfolio_totals():
    from ask_ai_grounded import _r95_cross_check_answer_against_canonical

    answer = (
        "High-risk customers: 5. Critical barriers: 2. "
        "Closed action plans: 8. Top 5 customers: 90 cases."
    )
    result = _r95_cross_check_answer_against_canonical(
        answer,
        {},
        {"total_customers": 53, "total_barriers": 20, "open_action_plans": 3},
    )

    assert result.corrections == []
    assert result.verified == []


def test_round95_checks_every_occurrence_and_unicode_accounting_signs():
    from ask_ai_grounded import (
        _r95_apply_canonical_corrections,
        _r95_cross_check_answer_against_canonical,
    )

    repeated = _r95_cross_check_answer_against_canonical(
        "Total customers: 53. Total customers: 999.",
        {},
        {"total_customers": 53},
    )
    unicode_minus = _r95_cross_check_answer_against_canonical(
        "Total ARR: −$2.4B. Total ARR: ($2.4B).",
        {},
        {"total_arr": 2_500_000_000},
    )

    assert len(repeated.corrections) == 1
    assert len(unicode_minus.corrections) == 2
    corrected = _r95_apply_canonical_corrections(
        "Total customers: 53. Total customers: 999.", repeated.corrections
    )
    assert corrected.count("Total customers: 53") == 2
