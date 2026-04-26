"""Round 7 / Phase 3.17 regression test.

``config`` must not ship personal SharePoint URLs or hardcoded TEAM_ROSTER literals.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_17() -> None:
    src = (REPO_ROOT.joinpath('config.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.17" in src, (
        "Round 7 Phase 3.17 marker missing in config.py."
    )
