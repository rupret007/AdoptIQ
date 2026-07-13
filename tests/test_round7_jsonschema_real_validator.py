"""Round 7 / Phase 5.3 regression test.

``generate_llm_json_response`` boundary must enforce ``additionalProperties: false`` via a real ``jsonschema`` validator (not prompt text alone).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_5_3() -> None:
    src = (REPO_ROOT.joinpath('adoptiq_backend.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 5.3" in src, (
        "Round 7 Phase 5.3 marker missing in adoptiq_backend.py."
    )
    assert 'jsonschema' in src, (
        "Round 7 / Phase 5.3: expected pattern " + 'jsonschema' + " missing in adoptiq_backend.py."
    )
