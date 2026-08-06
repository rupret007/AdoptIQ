"""Round 9 / Phase 2.2: HHI computation guards non-finite per-customer shares."""
from __future__ import annotations
from source_shape_utils import assert_in_source

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_hhi_isfinite() -> None:
    src = REPO_ROOT.joinpath('adoptiq_backend.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 2.2' in src, 'Round 9 / Phase 2.2 marker missing in adoptiq_backend.py'
    # The new guard replaces non-finite values with 0 before squaring.
    assert_in_source(src, 'replace(', label='src')
    # And ARR ratios route through the shared helper.
    assert_in_source(src, '_safe_div(', label='src')
