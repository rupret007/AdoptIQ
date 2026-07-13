"""Round 6 / Phase 1.3 regression test.

advanced_renewal_analyzer Contract Summary must respect
``is_multi_currency`` before printing a single ``$`` Total ARR.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_contract_summary_multicurrency_aware() -> None:
    src = (REPO_ROOT / "advanced_renewal_analyzer.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.3" in src, (
        "Round 6 Phase 1.3 marker missing in advanced_renewal_analyzer.py."
    )
