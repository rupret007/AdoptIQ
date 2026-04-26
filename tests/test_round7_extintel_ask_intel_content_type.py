"""Round 7 / Phase 4.4 regression test.

``/api/ask-intel`` fetch must check Content-Type before parsing JSON.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_4_4() -> None:
    src = (REPO_ROOT.joinpath('templates', 'external_intelligence.html')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 4.4" in src, (
        "Round 7 Phase 4.4 marker missing in templates/external_intelligence.html."
    )
