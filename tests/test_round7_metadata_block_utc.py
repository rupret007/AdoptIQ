"""Round 7 / Phase 3.11 regression test.

``format_metadata_block`` must use ``datetime.now(timezone.utc)``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_11() -> None:
    src = (REPO_ROOT.joinpath('report_utils.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.11" in src, (
        "Round 7 Phase 3.11 marker missing in report_utils.py."
    )
    assert 'datetime.now(timezone.utc)' in src, (
        "Round 7 / Phase 3.11: expected pattern " + 'datetime.now(timezone.utc)' + " missing in report_utils.py."
    )
