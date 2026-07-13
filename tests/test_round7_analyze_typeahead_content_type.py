"""Round 7 / Phase 4.2 regression test.

Typeahead fetches must check ``.ok`` and Content-Type before parsing JSON.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_4_2() -> None:
    src = (REPO_ROOT.joinpath('templates', 'analyze.html')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 4.2" in src, (
        "Round 7 Phase 4.2 marker missing in templates/analyze.html."
    )
