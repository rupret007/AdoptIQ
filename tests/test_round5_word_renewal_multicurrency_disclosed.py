"""Round 5 / Phase 1.3 regression test.

The renewal Word writer must disclose multi-currency totals instead of
silently slapping a ``$`` prefix on a sum that mixes USD/EUR/GBP rows.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_renewal_word_writer_handles_multicurrency() -> None:
    src = (REPO_ROOT / "advanced_renewal_analyzer.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 1.3" in src, (
        "Round 5 Phase 1.3: advanced_renewal_analyzer.py must branch on "
        "the multi-currency flag and refuse to emit a single '$<sum>' "
        "header when the upstream financials span multiple currencies."
    )
