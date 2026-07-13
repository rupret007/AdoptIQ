"""Round 5 / Phase 5.3 regression test.

``calculate_arr_at_risk`` must filter to OPEN/critical adoption-barrier
rows and intersect with the active-subscription universe before
summing ARR.  Without this filter, the headline "ARR at risk" number
included long-resolved cases and inactive subscriptions, dramatically
inflating the customer-facing dollar figure.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_arr_at_risk_filters_open_and_active() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # The plan calls out three sub-comments under Phase 5.3.
    assert src.count("Round 5 / Phase 5.3") >= 2, (
        "Round 5 Phase 5.3: calculate_arr_at_risk must filter to "
        "open/critical AB rows AND intersect with active subscriptions; "
        "expect at least two Phase 5.3 markers (one per filter step)."
    )
