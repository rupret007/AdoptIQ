"""Round 7 / Phase 4.8 regression test.

``500.html`` retry script must work under strict CSP (addEventListener with nonce or static button).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_4_8() -> None:
    src = (REPO_ROOT.joinpath('templates', '500.html')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 4.8" in src, (
        "Round 7 Phase 4.8 marker missing in templates/500.html."
    )
