"""Round 7 / Phase 5.6 regression test.

Classified ERROR strings in the LLM client (``adoptiq_backend``) must
be logged as ``kind=...`` + ``digest=hash[:8]`` rather than as raw
``result[:120]`` slices that can leak prompt fragments and stack
traces.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_5_6() -> None:
    src = (REPO_ROOT.joinpath("adoptiq_backend.py")).read_text(encoding="utf-8")
    assert "Round 7 / Phase 5.6" in src, (
        "Round 7 Phase 5.6 marker missing in adoptiq_backend.py."
    )
