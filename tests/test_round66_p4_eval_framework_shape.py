"""Round 66 / Pass 4 - Source-shape pins for the Ask AI eval framework.

These tests live OUTSIDE the eval marker so they run as part of
``make verify``. The eval suite itself (under ``tests/ask_ai_eval/``)
carries the ``eval`` marker and runs via ``make eval-ask-ai``.

Coverage:
- MockCircuitClient cassette miss / hash drift
- Predicate evaluators (4 types + unknown-type fallback)
- Runner load_questions / load_portfolio
- Scorecard rendering + determinism
- compose_grounded_answer eval-seam contract is intact
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import json
import re
from pathlib import Path

import pytest

from tests.ask_ai_eval import predicates as _predicates
from tests.ask_ai_eval import runner as _runner
from tests.ask_ai_eval.mock_circuit import (
    CassetteMissError,
    MockCircuitClient,
    _cassette_path,
    _hash_prompt,
)


# ---------------------------------------------------------------------------
# Mock CircuIT client
# ---------------------------------------------------------------------------


def test_mock_replay_raises_on_missing_cassette(tmp_path: Path):
    client = MockCircuitClient(mode="replay", cassette_dir=tmp_path)
    with pytest.raises(CassetteMissError) as excinfo:
        client.call("p99_q99_does_not_exist", "anything")
    assert "no cassette" in str(excinfo.value).lower()


def test_mock_replay_strict_hash_default_passes_when_recorded_hash_blank(tmp_path: Path):
    """Generator stamps an empty prompt_hash for synthetic cassettes; the
    client must allow that (otherwise every synthetic cassette fails)."""
    cassette = {
        "question_id": "demo",
        "recorded_at": "synthetic",
        "prompt_hash": "",
        "response": {"executive_summary": "ok", "claims": []},
    }
    (tmp_path / "demo.json").write_text(json.dumps(cassette), encoding="utf-8")
    client = MockCircuitClient(mode="replay", cassette_dir=tmp_path)
    out = client.call("demo", "any-prompt")
    assert out == {"executive_summary": "ok", "claims": []}


def test_mock_replay_strict_hash_raises_on_drift(tmp_path: Path):
    cassette = {
        "question_id": "demo",
        "recorded_at": "synthetic",
        "prompt_hash": _hash_prompt("ORIGINAL_PROMPT"),
        "response": {"executive_summary": "ok"},
    }
    (tmp_path / "demo.json").write_text(json.dumps(cassette), encoding="utf-8")
    client = MockCircuitClient(mode="replay", cassette_dir=tmp_path)
    with pytest.raises(CassetteMissError) as excinfo:
        client.call("demo", "DIFFERENT_PROMPT")
    assert "drift" in str(excinfo.value).lower()


def test_mock_record_requires_live_client():
    with pytest.raises(ValueError):
        MockCircuitClient(mode="record", live_client=None)


def test_mock_unknown_mode_raises():
    with pytest.raises(ValueError):
        MockCircuitClient(mode="bogus")


def test_cassette_path_rejects_empty_id(tmp_path: Path):
    """All-special-char ids reduce to empty string after sanitization
    and must raise rather than silently writing to the cassette dir."""
    with pytest.raises(ValueError):
        _cassette_path("///", tmp_path)


def test_cassette_path_sanitizes_path_separators(tmp_path: Path):
    """Defense-in-depth: '/' in question_id must NOT escape the
    cassette dir. The sanitizer strips path separators rather than
    raising so a benign id with a slash gets written to a safe name."""
    out = _cassette_path("p01/q01", tmp_path)
    assert out.parent == tmp_path
    assert "/" not in out.name[: len(out.name) - len(".json")]


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def test_must_contain_phrase_case_insensitive():
    passed, _ = _predicates.must_contain_phrase("Hello WORLD", "world")
    assert passed
    passed, _ = _predicates.must_contain_phrase("Hello", "missing")
    assert not passed


def test_must_contain_phrase_empty_inputs_fail():
    passed, _ = _predicates.must_contain_phrase("anything", "")
    assert not passed
    passed, _ = _predicates.must_contain_phrase("", "needle")
    assert not passed


def test_must_cite_source_id_any_token():
    passed, _ = _predicates.must_cite_source_id(
        "Some answer [Sources: AB-1, AB-2]", "any"
    )
    assert passed
    passed, _ = _predicates.must_cite_source_id("No citations here.", "any")
    assert not passed


def test_must_cite_source_id_requires_rendered_sources_marker():
    passed, _ = _predicates.must_cite_source_id(
        "Supported [Sources: AB-EVAL-007]", "AB-EVAL-007"
    )
    assert passed
    passed, _ = _predicates.must_cite_source_id(
        "no inline mention", "AB-EVAL-007", sources_seen={"AB-EVAL-007"}
    )
    assert not passed
    passed, _ = _predicates.must_cite_source_id(
        "no inline", "AB-EVAL-007", sources_seen={"AB-EVAL-008"}
    )
    assert not passed


def test_must_not_render_pii_blocks_match():
    passed, _ = _predicates.must_not_render_pii(
        "Contact alice@cisco.com for details", pii_patterns=["@cisco.com"]
    )
    assert not passed
    passed, _ = _predicates.must_not_render_pii(
        "no email here", pii_patterns=["@cisco.com"]
    )
    assert passed


def test_must_render_number_within_tolerance_passes_close_value():
    passed, _ = _predicates.must_render_number_within_tolerance(
        "Total: 102 records", 100, tolerance_pct=5
    )
    assert passed
    passed, _ = _predicates.must_render_number_within_tolerance(
        "Total: 200 records", 100, tolerance_pct=5
    )
    assert not passed


def test_must_render_number_within_tolerance_handles_zero_baseline():
    passed, _ = _predicates.must_render_number_within_tolerance(
        "Found 0 matching records", 0
    )
    assert passed
    passed, _ = _predicates.must_render_number_within_tolerance(
        "Found 50 matching records", 0
    )
    assert not passed


def test_must_render_number_handles_commas():
    passed, _ = _predicates.must_render_number_within_tolerance(
        "Total ARR is $4,250,000 across the cohort", 4_250_000, tolerance_pct=2
    )
    assert passed


def test_evaluate_unknown_predicate_fails_gracefully():
    results = _predicates.evaluate(
        "any answer", [{"type": "does_not_exist", "phrase": "x"}]
    )
    assert len(results) == 1
    assert results[0]["passed"] is False
    assert "unknown predicate" in results[0]["reason"]


def test_evaluate_predicate_raise_does_not_poison():
    """A predicate kwarg mismatch is wrapped, not raised."""
    results = _predicates.evaluate(
        "any answer",
        [{"type": "must_render_number_within_tolerance", "value": "not a number"}],
    )
    assert len(results) == 1
    assert results[0]["passed"] is False


# ---------------------------------------------------------------------------
# Runner: load + render
# ---------------------------------------------------------------------------


def test_load_questions_returns_75_for_committed_golden_set_after_round95():
    questions = _runner.load_questions()
    assert len(questions) == 75
    portfolios = {q.portfolio for q in questions}
    assert portfolios == {
        "p01_high_renewal_risk",
        "p02_heavy_psirt",
        "p03_adoption_barriers",
        "p04_quiet_portfolio",
        "p05_cross_compare",
    }


def test_load_questions_categories_cover_eight_buckets():
    questions = _runner.load_questions()
    categories = {q.category for q in questions}
    expected = {
        "kpi_extraction",
        "customer_lookup",
        "cross_compare",
        "psirt_exposure",
        "negative_control",
        "multi_step",
        "citation_correctness",
        "time_bounded",
    }
    assert expected.issubset(categories)


def test_load_portfolio_returns_dataframes():
    bundle = _runner.load_portfolio("p01_high_renewal_risk")
    assert not bundle.adoption_barriers.empty
    assert not bundle.support_cases.empty
    assert not bundle.customer_pulse.empty


def test_render_scorecard_is_deterministic():
    results = _runner.run_all()
    a = _runner.render_scorecard(results, git_sha="x", generated_at="t")
    b = _runner.render_scorecard(results, git_sha="x", generated_at="t")
    assert a == b
    # And contains the per-category + per-question tables
    assert "Per-category" in a
    assert "Per-question" in a


def test_run_all_baseline_matches_committed_scorecard():
    """The committed baseline scorecard's pass rate must match the
    runner's current output. Drift here means either:
      (a) an upstream change altered compose_grounded_answer behavior
          and the cassettes/baseline need re-recording, OR
      (b) the cassettes drifted from the fixtures.
    Either way the operator must intervene."""
    baseline = Path(__file__).resolve().parent / "ask_ai_eval" / "scorecards" / "baseline.md"
    if not baseline.is_file():
        pytest.skip("baseline.md not committed yet")
    body = baseline.read_text(encoding="utf-8")
    m = re.search(r"Pass rate: (\d+)/(\d+)", body)
    assert m, "baseline.md missing pass rate line"
    baseline_passed = int(m.group(1))
    baseline_total = int(m.group(2))
    results = _runner.run_all()
    current_passed = sum(1 for r in results if r.passed)
    current_total = len(results)
    assert current_total == baseline_total, (
        f"question count drift: baseline={baseline_total} current={current_total}"
    )
    assert current_passed == baseline_passed, (
        f"pass rate drift vs baseline.md: baseline={baseline_passed} current={current_passed}"
    )


# ---------------------------------------------------------------------------
# compose_grounded_answer eval-seam contract
# ---------------------------------------------------------------------------


def test_compose_grounded_answer_eval_seam_marker_present():
    """If anyone removes the Pass 4 contract block from
    compose_grounded_answer, this fails loud so the change goes through
    a deliberate decision."""
    src = Path(__file__).resolve().parents[1] / "ask_ai_grounded.py"
    body = src.read_text(encoding="utf-8")
    assert_in_source(body, "Round 66 / Pass 4 - ASK AI EVAL SEAM", label='body')


def test_compose_grounded_answer_signature_unchanged():
    """The runner depends on this exact signature; a drift here means
    every cassette needs re-recording."""
    import inspect

    import ask_ai_grounded as _g

    sig = inspect.signature(_g.compose_grounded_answer)
    params = list(sig.parameters)
    assert params == [
        "payload",
        "allowed_ids",
        "canonical_numbers",
        "evidence_records",
        "evidence_bootstrap",
    ]
