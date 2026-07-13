"""Round 9 / Phase 2.1: total_arr NaN-safe in adoptiq_backend.py."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_total_arr_isfinite() -> None:
    src = REPO_ROOT.joinpath('adoptiq_backend.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 2.1' in src, 'Round 9 / Phase 2.1 marker missing in adoptiq_backend.py'
    assert 'math.isfinite(total_arr)' in src, (
        'adoptiq_backend.py: NaN guard math.isfinite(total_arr) missing'
    )
    assert 'fillna(0)' in src, (
        'adoptiq_backend.py: fillna(0) on the ARR column missing'
    )
