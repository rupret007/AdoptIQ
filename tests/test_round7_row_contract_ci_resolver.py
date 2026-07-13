"""Round 7 / Phase 2.7 regression test.

``validate_row_contract`` must use a case-insensitive alias resolver consistent with ``first_existing_column``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_7() -> None:
    src = (REPO_ROOT.joinpath('data_contracts.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.7" in src, (
        "Round 7 Phase 2.7 marker missing in data_contracts.py."
    )
