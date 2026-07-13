"""Round 5 / Phase 1.2 regression test.

The renewal Excel writer must NOT fabricate a synthetic
"Review customer engagement and schedule account review" recommendation
when the underlying analyzer returned no recommendations.  That row
was indistinguishable from a real recommendation and inflated the
"recommendations rendered" count seen by the customer.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_synthetic_default_recommendation_emitted() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 1.2" in src, (
        "Round 5 Phase 1.2 marker missing in app_simple.py."
    )
    # The synthetic recommendation must not appear as a *string literal*
    # written into the workbook; we tolerate the line inside a code
    # comment that documents what was removed.
    needle = "Review customer engagement and schedule account review"
    no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert needle not in no_comments, (
        "Round 5 Phase 1.2: do NOT keep the legacy synthetic recommendation "
        "string as an executable literal - the analyzer either returns a "
        "recommendation list or we render an explicit 'no recommendations "
        "available' note."
    )
