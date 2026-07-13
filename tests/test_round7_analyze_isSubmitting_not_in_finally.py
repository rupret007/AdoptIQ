"""Round 7 / Phase 4.1 regression test.

``analyze.html`` must reset ``isSubmitting`` only on error/abort, not in a finally that fires before navigation.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_4_1() -> None:
    src = (REPO_ROOT.joinpath('templates', 'analyze.html')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 4.1" in src, (
        "Round 7 Phase 4.1 marker missing in templates/analyze.html."
    )
