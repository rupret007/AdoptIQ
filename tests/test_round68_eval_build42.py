"""Round 68 / Build 42 (C10): pin the Build 42 Ask AI eval scorecard.

The plan calls for the 50-question synthetic suite to re-run at
100% under the C1-C9 changes.  The scorecard is checked into
``tests/ask_ai_eval/scorecards/build42.md`` so the floor is
auditable in source control alongside ``build40.md``.

This test:
  * Loads the checked-in build42.md scorecard and pins the floor
    (100% total, 100% per category).
  * Re-runs the eval live and asserts the live result still hits
    the same floor (so a regression in C1-C9 surfaces here, not
    only in a manual operator step).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCORECARDS_DIR = _REPO_ROOT / "tests" / "ask_ai_eval" / "scorecards"
_BUILD42_SCORECARD = _SCORECARDS_DIR / "build42.md"


# --- Checked-in scorecard pin ------------------------------------------------


def test_round68_build42_scorecard_exists() -> None:
    """The Build 42 scorecard must be checked in so the floor is
    auditable from git history."""
    assert _BUILD42_SCORECARD.is_file(), (
        "tests/ask_ai_eval/scorecards/build42.md is missing -- C10 not "
        "executed.  Run: GIT_SHA=build42 python -m tests.ask_ai_eval.runner"
    )


def test_round68_build42_scorecard_header_pins_build_label() -> None:
    """The scorecard header must literally read 'Build 42' (or
    'build42') so a future operator grepping for the build label
    finds it."""
    body = _BUILD42_SCORECARD.read_text(encoding="utf-8")
    assert "build42" in body.lower(), "scorecard header doesn't carry the build label"


def test_round68_build42_scorecard_total_pass_rate_is_100() -> None:
    """The plan's gate: total pass rate MUST be 100%."""
    body = _BUILD42_SCORECARD.read_text(encoding="utf-8")
    m = re.search(r"Pass rate:\s*(\d+)/(\d+)\s*\(([\d.]+)%\)", body)
    assert m is not None, "scorecard header missing 'Pass rate:' line"
    passed = int(m.group(1))
    total = int(m.group(2))
    pct = float(m.group(3))
    assert total == 50, f"expected 50 questions, got {total}"
    assert passed == 50, (
        f"Build 42 eval pass rate is {passed}/{total} ({pct}%).  "
        f"C1-C9 broke the eval floor; see build42.md for failing rows."
    )
    assert pct >= 100.0, f"pct {pct} < 100"


def test_round68_build42_per_category_floors_pinned() -> None:
    """Every per-category row MUST be 100% so a partial regression
    (e.g. citation_correctness drops to 80%) surfaces here."""
    body = _BUILD42_SCORECARD.read_text(encoding="utf-8")
    cat_section = body.split("## Per-category", 1)
    assert len(cat_section) == 2, "scorecard missing 'Per-category' section"
    rows = re.findall(r"\|\s*([a-z_]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)%\s*\|", cat_section[1])
    assert rows, "no per-category rows parsed"
    failures: list[str] = []
    for cat, passed, total, pct in rows:
        if int(passed) != int(total):
            failures.append(f"{cat}: {passed}/{total} ({pct}%)")
    assert not failures, (
        "Per-category floor regression: " + "; ".join(failures)
    )


# --- Live re-run -------------------------------------------------------------


def test_round68_build42_live_eval_matches_scorecard_floor() -> None:
    """Re-run the eval live (offline cassettes) and confirm the
    live result still hits 100%.  Catches the case where the
    checked-in scorecard is stale relative to the current source."""
    from tests.ask_ai_eval import runner

    results = runner.run_all()
    assert results, "runner returned no results -- eval suite is empty"
    rate = runner.total_pass_rate(results)
    assert rate >= 100.0, (
        f"Live eval pass rate dropped to {rate:.1f}% -- C1-C9 introduced a "
        "regression that the scorecard floor would also catch on the next "
        "scorecard re-generation."
    )


def test_round68_build42_live_eval_per_category_holds() -> None:
    from tests.ask_ai_eval import runner

    results = runner.run_all()
    by_cat = runner.category_pass_rates(results)
    bad = [(cat, rate) for cat, rate in by_cat.items() if rate < 100.0]
    assert not bad, (
        "Live eval per-category regression: "
        + "; ".join(f"{cat}={rate:.1f}%" for cat, rate in bad)
    )


# --- Source-shape pin --------------------------------------------------------


def test_round68_build42_scorecard_has_per_question_table() -> None:
    """The scorecard must contain the per-question table so an
    auditor can see which Q failed when the floor breaks."""
    body = _BUILD42_SCORECARD.read_text(encoding="utf-8")
    assert "## Per-question" in body, "scorecard missing 'Per-question' section"
    assert "| Question | Portfolio | Category | Passed | Failure reason |" in body, (
        "scorecard per-question table header drifted"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
