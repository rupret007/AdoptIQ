"""Round 2 / Phase 1.5 regression test.

The Excel high-risk customer sheet must derive its rows from the same
``cm.is_high_risk_profile`` predicate as the executive dashboard's
``high_risk_count`` cell.  Previously the sheet used an ad-hoc
``score >= 6`` filter while the dashboard used the band/colour
predicate, so the two surfaces could disagree by 1+ customers.

This is a source-level pin: assert that ``app_simple.py`` references
``cm.is_high_risk_profile`` (or the canonical ``compute_high_risk_count``)
when building both the high-risk Excel sheet and the dashboard tile,
rather than re-implementing a numeric comparison locally.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_app_simple_high_risk_sheet_uses_canonical_predicate() -> None:
    src = _read(REPO_ROOT / "app_simple.py")
    assert "cm.is_high_risk_profile" in src or "is_high_risk_profile(" in src, (
        "Round 2 Phase 1.5: the Excel high-risk customer sheet and "
        "the dashboard tile must both go through "
        "cm.is_high_risk_profile so the row count and the headline "
        "tile cannot disagree."
    )


def test_app_simple_high_risk_predicate_used_in_multiple_sites() -> None:
    """The same predicate must back BOTH the Excel sheet filter and
    the executive-dashboard tile.  Asserting the predicate is called
    in at least two distinct locations gives us a regression signal
    if a future refactor reverts one site to ``score >= 6``.
    """
    src = _read(REPO_ROOT / "app_simple.py")
    matches = re.findall(r"is_high_risk_profile\s*\(", src)
    assert len(matches) >= 2, (
        "Round 2 Phase 1.5: cm.is_high_risk_profile must be used at "
        "two or more sites in app_simple.py (Excel high-risk sheet + "
        "executive dashboard tile) so they cannot drift. "
        f"Found {len(matches)} call sites."
    )
