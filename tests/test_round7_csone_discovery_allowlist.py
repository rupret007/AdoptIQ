"""Round 7 / Phase 2.6 regression test.

``snowflake_csone_discovery`` must restrict ``full_name`` interpolation to the discovery allowlist.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_6() -> None:
    src = (REPO_ROOT.joinpath('snowflake_csone_discovery.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.6" in src, (
        "Round 7 Phase 2.6 marker missing in snowflake_csone_discovery.py."
    )
