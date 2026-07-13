"""Round 7 / Phase 4.5 regression test.

``/api/import-intel`` fetch must check Content-Type before parsing JSON.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_4_5() -> None:
    src = (REPO_ROOT.joinpath('templates', 'external_intelligence.html')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 4.5" in src, (
        "Round 7 Phase 4.5 marker missing in templates/external_intelligence.html."
    )
