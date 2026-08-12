"""Round 7 / Phase 4.9 regression test.

``templates/psirt_search.html`` must ``{% extends "base.html" %}`` so it inherits the global CSP and security context.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_4_9() -> None:
    src = (REPO_ROOT.joinpath('templates', 'psirt_search.html')).read_text(encoding="utf-8")
    assert 'extends "base.html"' in src, (
        "Round 7 / Phase 4.9: expected pattern extends \"base.html\" missing in templates/psirt_search.html."
    )
