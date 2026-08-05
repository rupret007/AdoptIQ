"""Round 147 truth gates for the deterministic Ask AI replay corpus."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from . import _generate_fixtures as _generator
from . import runner as _runner
from .mock_circuit import MockCircuitClient
from .proofs import build_question_proof, metric_source_id


pytestmark = pytest.mark.eval


@pytest.fixture(scope="module")
def replay_results():
    return _runner.run_all(mode="replay")


def _question(question_id: str) -> _runner.Question:
    return next(question for question in _runner.load_questions() if question.id == question_id)


def _write_cassette(root: Path, question_id: str, response: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{question_id}.json").write_text(
        json.dumps(
            {
                "question_id": question_id,
                "recorded_at": "tamper-control",
                "prompt_hash": "",
                "response": response,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_replay_is_75_of_75_with_25_canonical_checks(replay_results):
    assert len(replay_results) == 75
    assert sum(result.passed for result in replay_results) == 75
    assert sum(result.rejected_count for result in replay_results) == 0
    canonical = [
        predicate
        for result in replay_results
        for predicate in result.predicate_results
        if predicate.get("type") == "must_match_canonical_metric"
    ]
    assert len(canonical) == 25
    assert all(predicate.get("passed") for predicate in canonical)


def test_every_canonical_answer_cites_its_exact_metric_source(replay_results):
    canonical_results = [
        result for result in replay_results if result.category == "canonical_metric"
    ]
    assert len(canonical_results) == 25
    for result in canonical_results:
        expected = metric_source_id(result.question_id)
        assert f"[Sources: {expected}]" in result.answer_excerpt


def test_every_derived_answer_renders_each_verifier_metric_source(replay_results):
    by_id = {result.question_id: result for result in replay_results}
    derived_count = 0
    for question in _runner.load_questions():
        bundle = _runner.load_portfolio(question.portfolio)
        records, _ = _runner.build_evidence_records(bundle)
        proof = build_question_proof(question, bundle, records)
        for record in proof.derived_records:
            derived_count += 1
            assert record.source_id in by_id[question.id].answer_excerpt
    assert derived_count >= 35


def test_every_negative_control_is_explicitly_insufficient(replay_results):
    negative_controls = [
        result for result in replay_results if result.category == "negative_control"
    ]
    assert len(negative_controls) == 9
    for result in negative_controls:
        assert "Insufficient grounded evidence" in result.answer_excerpt
        assert "[Sources:" not in result.answer_excerpt


def test_tampered_expected_value_fails_instead_of_teaching_cassette():
    original = _question("p01_q01")
    predicates = [dict(predicate) for predicate in original.predicates]
    predicates[0]["value"] = 999
    tampered = replace(original, predicates=predicates)
    bundle = _runner.load_portfolio(tampered.portfolio)
    result = _runner.evaluate_question(
        tampered,
        bundle,
        MockCircuitClient(mode="replay"),
    )
    assert result.passed is False
    assert any(
        predicate.get("type") == "must_render_number_within_tolerance"
        and predicate.get("passed") is False
        for predicate in result.predicate_results
    )
    assert "999" not in result.answer_excerpt


def test_tampered_metric_claim_is_rejected_by_exact_derived_evidence(tmp_path: Path):
    question = _question("p01_q01")
    _write_cassette(
        tmp_path,
        question.id,
        {
            "executive_summary": "",
            "claims": [
                {
                    "statement": "Open adoption barriers: 999.",
                    "citations": [metric_source_id(question.id)],
                }
            ],
            "actions": [],
            "unknowns": [],
        },
    )
    result = _runner.evaluate_question(
        question,
        _runner.load_portfolio(question.portfolio),
        MockCircuitClient(mode="replay", cassette_dir=tmp_path),
    )
    assert result.passed is False
    assert result.rejected_count >= 1
    assert "999" not in result.answer_excerpt
    assert "cited records do not support" in result.answer_excerpt


def test_incomplete_row_citation_cannot_repeat_winner_claim(tmp_path: Path):
    question = _question("p01_q02")
    _write_cassette(
        tmp_path,
        question.id,
        {
            "executive_summary": "",
            "claims": [
                {
                    "statement": "EpsilonEvalGroup has the minimum pulse score of 1.",
                    "citations": ["PULSE-EVAL-001"],
                }
            ],
            "actions": [],
            "unknowns": [],
        },
    )
    result = _runner.evaluate_question(
        question,
        _runner.load_portfolio(question.portfolio),
        MockCircuitClient(mode="replay", cassette_dir=tmp_path),
    )
    assert result.passed is False
    assert result.rejected_count >= 1
    assert "EpsilonEvalGroup" not in result.answer_excerpt
    assert "PULSE-EVAL-005" not in result.answer_excerpt


def test_fixture_generator_is_byte_deterministic(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert _generator.write_all(first) == {
        "portfolios": 5,
        "questions": 75,
        "cassettes": 75,
    }
    assert _generator.write_all(second) == {
        "portfolios": 5,
        "questions": 75,
        "cassettes": 75,
    }
    assert _tree_digest(first) == _tree_digest(second)
