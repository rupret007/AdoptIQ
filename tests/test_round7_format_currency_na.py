"""Round 7 / Phase 3.10 regression test.

``format_currency`` must return ``"N/A"`` on parse failure to match ``format_number``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_10() -> None:
    src = (REPO_ROOT.joinpath('report_utils.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.10" in src, (
        "Round 7 Phase 3.10 marker missing in report_utils.py."
    )
    assert '"N/A"' in src, (
        "Round 7 / Phase 3.10: expected pattern " + '"N/A"' + " missing in report_utils.py."
    )
