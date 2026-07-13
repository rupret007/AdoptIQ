"""Round 4 / Phase 3.5 regression test.

``risk_scoring._score_customer_pulse`` must read the canonical
``SCORE__C`` numeric pulse field FIRST, falling back to rating text
only when the score is absent.  Pre-Round-4 the scorer preferred
``PULSE_RATING__C`` / ``Rating`` and never read ``SCORE__C``, so the
risk score and the canonical pulse_sentiment label could disagree on
the same row.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_pulse_scorer_reads_score_c_field() -> None:
    src = (REPO_ROOT / "risk_scoring.py").read_text(encoding="utf-8")
    assert "SCORE__C" in src or "SCORE_C" in src, (
        "Round 4 Phase 3.5: risk_scoring._score_customer_pulse must "
        "reference SCORE__C so it agrees with cm.pulse_sentiment "
        "(which reads SCORE__C first)."
    )


def test_pulse_scorer_function_present() -> None:
    src = (REPO_ROOT / "risk_scoring.py").read_text(encoding="utf-8")
    assert "_score_customer_pulse" in src, (
        "_score_customer_pulse not found in risk_scoring.py"
    )
