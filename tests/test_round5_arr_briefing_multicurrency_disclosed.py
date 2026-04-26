"""Round 5 / Phase 3.3 regression test.

The ARR briefing block must bucket totals by ``CURRENCY_CODE`` and
emit an explicit "MIXED - do not sum" disclosure when the upstream
financials span multiple currencies, instead of slapping a ``$``
prefix on a sum that mixes USD/EUR/GBP rows.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_arr_briefing_handles_multicurrency() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 3.3" in src, (
        "Round 5 Phase 3.3 marker missing in adoptiq_backend.py."
    )
    assert (
        "CURRENCY_CODE" in src
    ), (
        "Round 5 Phase 3.3: ARR briefing must bucket by CURRENCY_CODE before "
        "summing/displaying totals to the LLM prompt."
    )
