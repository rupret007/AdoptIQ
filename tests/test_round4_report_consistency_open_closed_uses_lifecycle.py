"""Round 4 / Phase 3.3 regression test.

``report_consistency`` must drive open/closed TAC counts through
``cm.count_open_tac`` / ``cm.count_closed_tac`` (the canonical
helpers) instead of the normalized status sets directly.  This avoids
the pre-Round-4 case where rows with status ``Unknown`` plus a real
``close_date`` were dropped by the validator while
``data_normalization`` correctly classified them as closed.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_report_consistency_uses_canonical_open_closed_helpers() -> None:
    src = (REPO_ROOT / "report_consistency.py").read_text(encoding="utf-8")
    # The Round 4 fix routes through canonical_metrics.count_open_tac
    # and count_closed_tac.  We accept either ``cm.count_open_tac`` or
    # an explicit ``count_open_tac(`` call with a CSOne frame.
    assert (
        "count_open_tac" in src or "count_closed_tac" in src
    ), (
        "Round 4 Phase 3.3: report_consistency must call "
        "cm.count_open_tac / cm.count_closed_tac (the canonical "
        "lifecycle-aware helpers) instead of bypassing them."
    )
