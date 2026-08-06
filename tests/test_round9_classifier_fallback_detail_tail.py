"""Round 9 / Phase 6.6: classifier-fallback path always runs _detail_tail scrubber."""
from __future__ import annotations
from source_shape_utils import assert_in_source

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_classifier_fallback_detail_tail() -> None:
    src = REPO_ROOT.joinpath('app_simple.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 6.6' in src, (
        'Round 9 / Phase 6.6 marker missing in app_simple.py'
    )
    # The fallback now imports _detail_tail from error_classifier.
    assert 'from error_classifier import _detail_tail' in src, (
        'app_simple.py: classifier-fallback should import _detail_tail scrubber'
    )
    assert_in_source(src, '_classifier_detail_tail', label='src')
