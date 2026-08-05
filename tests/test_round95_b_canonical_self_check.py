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


def test_round95_canonical_self_check_ignores_unchecked_numbers():
    from ask_ai_grounded import _r95_cross_check_answer_against_canonical

    result = _r95_cross_check_answer_against_canonical(
        "The answer references 47 escalations in a free-form note.",
        {"manager": "All Managers"},
        {"total_customers": 39},
    )
    assert result.corrections == []
    assert result.verified == []


def test_round95_canonical_corrections_append_callout():
    from ask_ai_grounded import _r95_apply_canonical_corrections

    answer = _r95_apply_canonical_corrections(
        "Total Customers: 47.",
        [{"kpi": "total_customers", "llm_value": 47, "canonical_value": 39, "delta_pct": 20.5}],
    )
    assert "Total Customers: 47" not in answer
    assert "### Canonical Metrics" in answer
    assert "total_customers: 39" in answer
    assert "[Sources: METRIC-TOTAL-CUSTOMERS-" in answer


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
