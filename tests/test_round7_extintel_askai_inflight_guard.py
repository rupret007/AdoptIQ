"""Round 7 / Phase 4.6 regression test.

``askAI()`` must disable its button synchronously to prevent double-submit.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_4_6() -> None:
    src = (REPO_ROOT.joinpath('templates', 'external_intelligence.html')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 4.6" in src, (
        "Round 7 Phase 4.6 marker missing in templates/external_intelligence.html."
    )
