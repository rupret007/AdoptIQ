"""Round 9 / Phase 1.2: app_simple.py traceback demoted to DEBUG.

Marker + pattern regression test.  The previous handler logged
``traceback.format_exc()`` at ERROR/INFO on every analysis failure,
inflating central log volume and exposing local stack frames.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_app_simple_traceback_debug() -> None:
    src = REPO_ROOT.joinpath('app_simple.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 1.2' in src, 'Round 9 / Phase 1.2 marker missing in app_simple.py'
    # The replacement uses logger.debug + exc_info=True for the verbatim trace
    assert 'logger.debug("[[LIST]] Full traceback for analysis failure", exc_info=True)' in src, (
        'app_simple.py: full traceback should be logged at DEBUG via exc_info=True'
    )
    # And a stable kind tag at ERROR
    assert 'kind={_kind_for_log}' in src or 'kind=' in src, (
        'app_simple.py: condensed error_kind tag missing at ERROR level'
    )
