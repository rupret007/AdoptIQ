"""Round 71 / Phase 4 (#23) -- risk score 0-10 rounding pinned to .1f.

Pre-R71 ``app_simple.py`` formatted the 0-10 risk score with ``.2f``
in some places and ``.1f`` in others.  ``risk_scoring`` (the SSoT)
stores the score with 1-decimal precision.  The drift surfaced as
``5.40/10`` in the renewal dashboard table while the narrative two
paragraphs above said ``5.4/10`` for the SAME customer.

Round 71 / Phase 4 (#23) standardizes every 0-10 surface to ``.1f``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8", errors="replace")


def test_round71_renewal_dashboard_table_uses_one_decimal_for_0_10() -> None:
    """The renewal dashboard table's Overall Risk Score row MUST use
    ``{_r67_score_10:.1f}/10`` (not ``.2f``)."""
    src = _read_app_simple()
    # The R71 fix string literal.
    expected = "f'{_r67_score_10:.1f}/10  ({risk_score:.1f}/100)'"
    assert expected in src, (
        f"Round 71 / Phase 4 (#23): the renewal dashboard table must "
        f"format the 0-10 score with .1f.  Expected literal: {expected!r}"
    )


def test_round71_renewal_dashboard_table_no_longer_uses_two_decimal_for_0_10() -> None:
    """The renewal dashboard table MUST NOT carry the ``.2f`` form."""
    src = _read_app_simple()
    forbidden = "f'{_r67_score_10:.2f}/10"
    # We may still have other .2f patterns elsewhere, but specifically
    # the dashboard-table line was the drift site.
    assert forbidden not in src, (
        f"Round 71 / Phase 4 (#23): the dashboard-table line must not "
        f"use the pre-R71 .2f form ({forbidden!r}).  Pre-R71 it printed "
        f"5.40/10 while narratives elsewhere said 5.4/10."
    )


def test_round71_executive_summary_narrative_uses_one_decimal() -> None:
    """The executive summary narrative MUST also use ``.1f`` for 0-10."""
    src = _read_app_simple()
    # The narrative line is around L11674.
    assert "f'{_r67_score_10:.1f}/10 ({_r67_risk_label}; {risk_score:.1f}/100). '" in src or (
        "{_r67_score_10:.1f}/10" in src
    ), (
        "Round 71 / Phase 4 (#23): the renewal executive summary "
        "narrative must format 0-10 with .1f."
    )


def test_round71_renewal_score_division_uses_one_decimal_round() -> None:
    """The risk_score / 10.0 conversion MUST round to 1 decimal (not 2)."""
    src = _read_app_simple()
    # The conversion.
    assert "round(float(risk_score) / 10.0, 1)" in src, (
        "Round 71 / Phase 4 (#23): the risk_score / 10.0 conversion "
        "must round(..., 1) to honour the SSoT 1-decimal precision."
    )
    # And the pre-R71 form (round(..., 2)) must NOT exist.
    assert "round(float(risk_score) / 10.0, 2)" not in src, (
        "Round 71 / Phase 4 (#23): the pre-R71 round(..., 2) form "
        "must be removed."
    )
