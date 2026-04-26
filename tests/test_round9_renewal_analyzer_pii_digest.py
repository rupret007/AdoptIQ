"""Round 9 / Phase 6.3: advanced_renewal_analyzer.py customer_name digested at INFO."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_renewal_analyzer_pii_digest() -> None:
    src = REPO_ROOT.joinpath('advanced_renewal_analyzer.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 6.3' in src, (
        'Round 9 / Phase 6.3 marker missing in advanced_renewal_analyzer.py'
    )
    assert '_cust_digest' in src, (
        'advanced_renewal_analyzer.py: _cust_digest helper missing'
    )
    assert 'customer_digest=' in src, (
        'advanced_renewal_analyzer.py: customer_digest INFO log marker missing'
    )
