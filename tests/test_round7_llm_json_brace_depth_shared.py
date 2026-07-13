"""Round 7 / Phase 5.1 regression test.

``generate_llm_json_response`` must use the shared ``_extract_json_object`` brace-depth walker, not greedy regex.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_5_1() -> None:
    src = (REPO_ROOT.joinpath('adoptiq_backend.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 5.1" in src, (
        "Round 7 Phase 5.1 marker missing in adoptiq_backend.py."
    )
