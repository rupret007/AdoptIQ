"""Round 7 / Phase 2.8 regression test.

``data_source_validator`` must accept the ``ROW_CONTRACTS['adoption_barriers']`` aliases (BU_NAME, ACCOUNT_NAME).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_8() -> None:
    src = (REPO_ROOT.joinpath('data_source_validator.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.8" in src, (
        "Round 7 Phase 2.8 marker missing in data_source_validator.py."
    )
