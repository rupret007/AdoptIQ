"""Round 9 / Phase 5.4: base.html annotated for nonce migration; CSP-Report-Only emitted."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_base_csp_annotation() -> None:
    src = REPO_ROOT.joinpath('templates', 'base.html').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 5.4' in src, (
        'Round 9 / Phase 5.4 marker missing in templates/base.html'
    )


def test_marker_app_simple_csp_report_only() -> None:
    src = REPO_ROOT.joinpath('app_simple.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 5.4' in src, (
        'Round 9 / Phase 5.4 marker missing in app_simple.py'
    )
    assert 'Content-Security-Policy-Report-Only' in src, (
        'app_simple.py: Content-Security-Policy-Report-Only header missing'
    )
