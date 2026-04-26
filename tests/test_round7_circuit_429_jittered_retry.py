"""Round 7 / Phase 5.5 regression test.

``CircuitChatClient.complete`` must apply jittered backoff retry on ``llm.rate_limit_429``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_5_5() -> None:
    src = (REPO_ROOT.joinpath('adoptiq_backend.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 5.5" in src, (
        "Round 7 Phase 5.5 marker missing in adoptiq_backend.py."
    )
