"""Round 16 / Phase 4 -- AI insights eval harness.

Lightweight, recorded-response eval of ``ai_narrative_validator``.
Exercises the validator against a fixed briefing and four fixture
narratives:

- ``grounded.txt``           — every quoted number and entity is in the
                               briefing; must pass.
- ``hallucinated_number.txt``— quotes 47 customers / $9,876,543 (not in
                               the briefing); must fail with
                               ``ungrounded_number``.
- ``invented_entity.txt``    — names "Phantom Holdings Corp" (not in
                               the allowed-entities list); must fail
                               with ``invented_entity``.
- ``html_injection.txt``     — embeds ``<script>``; must fail with
                               ``html_injection:script_tag``.

How to extend
-------------

1. Add a new ``tests/fixtures/round16/insights/<scenario>.txt`` file
   containing the recorded model output.
2. Append an ``EvalCase`` row to the ``_EVAL_CASES`` table below with
   the expected outcome.
3. Re-run ``python3 -m pytest tests/test_round16_ai_insights_eval.py``.
4. Document the new scenario in
   ``QUALITY_AUDIT.md`` §Round 16 / Phase 4 with a one-line rationale.

The recorded responses are *fixtures*, not live LLM calls.  This is a
deterministic regression harness, not a model benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

import ai_narrative_validator as anv

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "round16" / "insights"


def _read(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


# Allow-list of customer names the manager owns for the test portfolio.
# This is what app_simple.py would derive from the briefing's customer
# frames; pinned here so the eval is fully deterministic.
_ALLOWED_ENTITIES = ("Acme Corp", "Beta Inc", "Gamma Ltd", "Delta Co")


@dataclass(frozen=True)
class EvalCase:
    name: str
    fixture: str
    should_pass: bool
    expected_failure_substring: str = ""


_EVAL_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="grounded narrative passes",
        fixture="grounded.txt",
        should_pass=True,
    ),
    EvalCase(
        name="hallucinated number fails on ungrounded_number",
        fixture="hallucinated_number.txt",
        should_pass=False,
        expected_failure_substring="ungrounded_number",
    ),
    EvalCase(
        name="invented entity fails on invented_entity",
        fixture="invented_entity.txt",
        should_pass=False,
        expected_failure_substring="invented_entity",
    ),
    EvalCase(
        name="html injection fails on html_injection",
        fixture="html_injection.txt",
        should_pass=False,
        expected_failure_substring="html_injection",
    ),
)


@pytest.fixture(scope="module")
def briefing_text() -> str:
    return _read("briefing.txt")


@pytest.mark.parametrize("case", _EVAL_CASES, ids=[c.name for c in _EVAL_CASES])
def test_phase_4_eval_case(case: EvalCase, briefing_text: str):
    """Round 16 / Phase 4 -- run each fixture through the validator and
    assert the recorded expectation."""
    narrative = _read(case.fixture)
    result = anv.validate_narrative(
        narrative,
        briefing_text,
        allowed_entities=_ALLOWED_ENTITIES,
    )
    if case.should_pass:
        assert result.is_valid, (
            f"Fixture {case.fixture} should pass validation but failed with "
            f"failures={result.failures} samples={result.sample_offending}"
        )
    else:
        assert not result.is_valid, (
            f"Fixture {case.fixture} should fail validation but passed."
        )
        joined = ",".join(result.failures)
        assert case.expected_failure_substring in joined, (
            f"Fixture {case.fixture}: expected failure substring "
            f"{case.expected_failure_substring!r} in {result.failures}"
        )


def test_phase_4_all_fixtures_present_on_disk():
    """Round 16 / Phase 4 -- every ``EvalCase`` must point at a real
    fixture file so the harness cannot silently skip a scenario."""
    for case in _EVAL_CASES:
        path = _FIXTURE_DIR / case.fixture
        assert path.exists(), f"Missing fixture: {path}"
        assert path.stat().st_size > 0, f"Empty fixture: {path}"


def test_phase_4_briefing_fixture_is_substantive(briefing_text: str):
    """Pin: the briefing fixture must carry the core KPIs the eval
    cases reference, otherwise the grounded case would pass for the
    wrong reason."""
    for marker in ("12", "24", "31", "Acme Corp", "Beta Inc", "1,250,000"):
        assert marker in briefing_text, (
            f"Briefing fixture missing canonical marker: {marker!r}"
        )


def test_phase_4_eval_case_count_matches_fixture_count():
    """Round 16 / Phase 4 -- guard against orphaned fixtures (added a
    new file but forgot to add an EvalCase row).  Counts only the
    response fixtures, not the briefing itself."""
    response_fixtures = [
        p for p in _FIXTURE_DIR.iterdir()
        if p.is_file() and p.suffix == ".txt" and p.name != "briefing.txt"
    ]
    assert len(response_fixtures) == len(_EVAL_CASES), (
        "Round 16 / Phase 4 eval harness: fixture/case count mismatch. "
        f"Files: {sorted(p.name for p in response_fixtures)}\n"
        f"Cases: {[c.fixture for c in _EVAL_CASES]}"
    )
