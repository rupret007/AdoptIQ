"""Round 2 / Phase 1.6 conditional-format threshold pin.

The Excel exec-dashboard band labels in ``app_simple.py`` previously
used hardcoded ``>= 7`` / ``>= 4`` cuts on a 0-10 risk score, which
disagreed with the canonical ``RISK_BAND_THRESHOLDS`` (HIGH=55,
MEDIUM=35 on a 0-100 scale, ie 5.5 / 3.5 on the 0-10 scale).  This
caused the same numeric score to land in different bands in
different surfaces.

After Phase 1.6 the band label MUST be derived from
``risk_scoring.RISK_BAND_THRESHOLDS`` so the headline tile cannot
drift from the rest of the report.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_legacy_seven_or_four_band_cuts_in_app_simple_status_block() -> None:
    """The legacy ``>=7`` / ``>=4`` cuts (with ``[HIGH] High Risk``)
    must not reappear in the Excel exec-dashboard Status block.
    """
    src = _read(REPO_ROOT / "app_simple.py")
    bad = re.search(
        r"\[HIGH\][^\n]*overall_risk_score\s*>=\s*7\b",
        src,
    )
    assert bad is None, (
        "Round 2 Phase 1.6: the Excel executive-dashboard Status row "
        "must not use the legacy >=7 / >=4 band cuts; it must derive "
        "from RISK_BAND_THRESHOLDS so it agrees with the rest of the "
        "report."
    )


def test_app_simple_imports_canonical_thresholds() -> None:
    src = _read(REPO_ROOT / "app_simple.py")
    assert "RISK_BAND_THRESHOLDS" in src, (
        "Round 2 Phase 1.6: app_simple.py must import "
        "RISK_BAND_THRESHOLDS from risk_scoring so it can derive "
        "band cuts from the canonical source."
    )
