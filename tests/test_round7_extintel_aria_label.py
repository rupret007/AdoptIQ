"""Round 7 / Phase 4.7 regression test.

``#intelSearch`` and ``#clearSearch`` must have ``aria-label`` for screen readers.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_4_7() -> None:
    src = (REPO_ROOT.joinpath('templates', 'external_intelligence.html')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 4.7" in src, (
        "Round 7 Phase 4.7 marker missing in templates/external_intelligence.html."
    )
    assert 'aria-label' in src, (
        "Round 7 / Phase 4.7: expected pattern " + 'aria-label' + " missing in templates/external_intelligence.html."
    )
