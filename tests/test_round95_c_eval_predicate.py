"""Round 95 / Phase C - canonical-metric eval predicate tests."""

from __future__ import annotations
from source_shape_utils import assert_in_source


def test_round95_predicate_registry_contains_canonical_metric_handler():
    from tests.ask_ai_eval.predicates import PREDICATE_REGISTRY

    assert "must_match_canonical_metric" in PREDICATE_REGISTRY


def test_round95_eval_runner_picks_up_25_new_canonical_questions():
    from tests.ask_ai_eval.runner import load_questions

    questions = load_questions()
    r95 = [q for q in questions if q.category == "canonical_metric"]
    assert len(r95) == 25
    assert all(any(p.get("type") == "must_match_canonical_metric" for p in q.predicates) for q in r95)


def test_round95_canonical_metric_predicate_runs_against_fixture_bundle():
    from tests.ask_ai_eval.predicates import must_match_canonical_metric
    from tests.ask_ai_eval.runner import load_portfolio

    bundle = load_portfolio("p01_high_renewal_risk")
    passed, reason = must_match_canonical_metric(
        "Open adoption barriers: 12.",
        metric="open_adoption_barriers",
        tolerance=0,
        portfolio_bundle=bundle,
    )
    assert passed is True
    assert "canonical value 12" in reason


def test_round95_evaluate_question_passes_new_predicate():
    from tests.ask_ai_eval.mock_circuit import MockCircuitClient
    from tests.ask_ai_eval.runner import evaluate_question, load_portfolio, load_questions

    q = next(question for question in load_questions() if question.id == "p01_q11")
    result = evaluate_question(q, load_portfolio(q.portfolio), MockCircuitClient(mode="replay"))
    assert result.passed is True
    assert result.predicate_results[0]["type"] == "must_match_canonical_metric"


def test_round95_scorecard_renders_canonical_metric_summary():
    from tests.ask_ai_eval.runner import QuestionResult, render_scorecard

    body = render_scorecard(
        [
            QuestionResult(
                question_id="p01_q11",
                portfolio="p01_high_renewal_risk",
                category="canonical_metric",
                passed=True,
                predicate_results=[
                    {"type": "must_match_canonical_metric", "passed": True, "reason": "matched"}
                ],
            )
        ],
        git_sha="round95",
        generated_at="2026-05-08T00:00:00Z",
    )
    assert_in_source(body, "Canonical metric predicates: 1/1", label='body')
    assert_in_source(body, "| p01_q11 |", label='body')
