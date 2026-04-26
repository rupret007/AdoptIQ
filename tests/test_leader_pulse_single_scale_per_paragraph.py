"""Round 2 / Phase 1.9 + Phase 3.1 regression test.

The leader-report pulse paragraph and the sentiment headline must be
derived from a single ``cm.pulse_sentiment`` call on the canonical
0-10 scale.  Mixing scales (or recomputing sentiment from raw column
math) leads to a paragraph that says one thing and a headline that
says another.

This test pins:
  * ``leader_report_generator._derive_sentiment_summary`` calls
    ``cm.pulse_sentiment`` on ``cm.PULSE_SCALE_0_TO_10``, not a
    bare numeric mean.
  * The function does not import any ad-hoc ``Positive`` / ``Negative``
    threshold literals from outside the canonical helper.
  * Phase 3.1: when ``customer_pulse.attrs['fetch_error']`` is set,
    leader skips computing a misleading sentiment.
"""
from __future__ import annotations

import inspect
import re

from leader_report_generator import LeaderReportGenerator


def test_derive_sentiment_summary_uses_canonical_pulse_helper() -> None:
    src = inspect.getsource(LeaderReportGenerator._derive_sentiment_summary)
    assert re.search(
        r"cm\.pulse_sentiment\([^)]*scale\s*=\s*cm\.PULSE_SCALE_0_TO_10",
        src,
        re.DOTALL,
    ), (
        "Round 2 Phase 1.9: _derive_sentiment_summary must call "
        "cm.pulse_sentiment(scale=cm.PULSE_SCALE_0_TO_10) so the "
        "sentiment is on the canonical 0-10 scale."
    )


def test_derive_sentiment_summary_no_inline_threshold_code() -> None:
    """No ad-hoc ``>= 7.5`` / ``<= 5.0`` numeric thresholds in the
    helper's *executable* code — those thresholds live in
    canonical_metrics so a threshold change updates everywhere at
    once.  Doc-comments referencing the canonical cutoffs are fine
    and even helpful; we only want to catch the case where someone
    wrote a real ``if score >= 7.5:`` branch back into the helper.
    """
    src = inspect.getsource(LeaderReportGenerator._derive_sentiment_summary)
    # Strip comment-only lines so doc references to >=7.5 / <=5.0
    # do not falsely trip this regression check.
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert ">= 7.5" not in code and ">=7.5" not in code, (
        "Round 2 Phase 1.9: do not write a >= 7.5 Positive branch "
        "in executable code; route through cm.pulse_sentiment so "
        "the threshold is centralized."
    )
    assert "<= 5.0" not in code and "<=5.0" not in code, (
        "Round 2 Phase 1.9: do not write a <= 5.0 Negative branch "
        "in executable code; route through cm.pulse_sentiment so "
        "the threshold is centralized."
    )


def test_leader_handles_pulse_fetch_error_attr() -> None:
    """Round 2 Phase 3.1: leader should detect a pulse fetch failure
    via ``customer_pulse.attrs['fetch_error']`` and avoid emitting a
    sentiment as if the silence were good news.
    """
    import pathlib
    src = pathlib.Path("leader_report_generator.py").read_text(encoding="utf-8")
    assert (
        "customer_pulse.attrs.get('fetch_error')" in src
        or 'customer_pulse.attrs.get("fetch_error")' in src
        or "pulse.attrs.get('fetch_error')" in src
        or 'pulse.attrs.get("fetch_error")' in src
    ), (
        "Round 2 Phase 3.1: leader_report_generator must inspect "
        "customer_pulse.attrs['fetch_error'] so a failed pulse "
        "fetch surfaces as 'unavailable' rather than a misleading "
        "neutral sentiment."
    )
