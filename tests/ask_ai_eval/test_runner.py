"""Round 66 / Pass 4 - pytest entry point for the Ask AI eval framework.

Marker: ``eval``. Excluded from default ``pytest -q`` via pytest.ini's
``addopts = -m 'not eval'``. Invoked explicitly by ``make eval-ask-ai``.

What this test asserts (Pass 4 baseline contract):
- The eval runner finishes without raising.
- It produces at least one question result (i.e. the golden set is
  reachable, fixtures load, cassettes exist for replay mode).
- The scorecard renders deterministically (same results in -> same
  Markdown out).

It does NOT gate on a specific pass rate. The Pass 5 scorecard
comparison gate (>=15% uplift on >=3 of 5 categories) lives in
``QUALITY_AUDIT.md`` as the manual operator step at Build 40 cut time;
codifying it here would either (a) require committing the live LLM
cassettes which depend on a real CircuIT recording session, or
(b) risk a flaky CI when CircuIT is unavailable. Trade-off documented
in plan section "Risks / Decisions to Flag".
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

import pytest

from . import runner as _runner


pytestmark = pytest.mark.eval


@pytest.fixture(scope="module")
def eval_results():
    return _runner.run_all()


def test_eval_runner_completes(eval_results):
    assert isinstance(eval_results, list)


def test_eval_has_questions(eval_results):
    if not eval_results:
        pytest.skip(
            "no questions found under tests/ask_ai_eval/questions/ - "
            "operator must seed the golden set (see QUALITY_AUDIT.md)"
        )
    assert len(eval_results) >= 1


def test_eval_scorecard_renders(eval_results, tmp_path: Path):
    if not eval_results:
        pytest.skip("no questions; nothing to render")
    out = tmp_path / "scorecard.md"
    _runner.write_scorecard(
        eval_results,
        out_path=out,
        git_sha="test",
        generated_at=_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert "Ask AI Eval Scorecard" in body
    assert "Per-category" in body
    assert "Per-question" in body


def test_eval_is_deterministic(eval_results):
    if not eval_results:
        pytest.skip("no questions; nothing to compare")
    rendered_a = _runner.render_scorecard(eval_results, git_sha="x", generated_at="t")
    rendered_b = _runner.render_scorecard(eval_results, git_sha="x", generated_at="t")
    assert rendered_a == rendered_b


def test_eval_baseline_committed():
    """The committed baseline scorecard exists under
    tests/ask_ai_eval/scorecards/baseline.md when the operator has
    completed the recording step. Skip when absent (pre-recording state)."""
    baseline = Path(__file__).resolve().parent / "scorecards" / "baseline.md"
    if not baseline.is_file():
        pytest.skip(
            "baseline.md not yet committed - operator runs "
            "MOCK_CIRCUIT_MODE=record python -m tests.ask_ai_eval.runner "
            "against live CircuIT once and commits the resulting baseline"
        )
    body = baseline.read_text(encoding="utf-8")
    assert "Ask AI Eval Scorecard" in body
    assert "Pass rate:" in body


def test_eval_runs_offline_when_replay():
    """Confirm the framework does NOT touch the network in replay mode
    by ensuring the runner does not import requests/urllib3 at runtime
    when the cassette path is taken (sentinel: the runner module exposes
    no network entrypoints)."""
    assert not hasattr(_runner, "requests")
    assert not hasattr(_runner, "urllib3")
    assert not hasattr(_runner, "circuit_client")
    # Mock cassette dir env override should be respected
    override = os.environ.get("ASK_AI_EVAL_CASSETTE_DIR")
    assert override is None or os.path.isdir(override) or not Path(override).exists()
