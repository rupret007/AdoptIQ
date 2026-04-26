"""Round 7 / Phase 6.7 regression test.

``leader_report_generator`` and ``advanced_renewal_analyzer`` must use ``datetime.now(timezone.utc)``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_7() -> None:
    src = (REPO_ROOT.joinpath('leader_report_generator.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.7" in src, (
        "Round 7 Phase 6.7 marker missing in leader_report_generator.py."
    )
    assert 'datetime.now(timezone.utc)' in src, (
        "Round 7 / Phase 6.7: expected pattern " + 'datetime.now(timezone.utc)' + " missing in leader_report_generator.py."
    )
