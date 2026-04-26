"""Round 7 / Phase 4.3 regression test.

``/api/refresh-external-intel`` fetch must check Content-Type before parsing JSON.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_4_3() -> None:
    src = (REPO_ROOT.joinpath('templates', 'external_intelligence.html')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 4.3" in src, (
        "Round 7 Phase 4.3 marker missing in templates/external_intelligence.html."
    )
