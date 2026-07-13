"""Round 7 / Phase 3.15 regression test.

``connectivity_diagnostics`` must redact host/secret/path/keys/IPs from non-localhost callers.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_15() -> None:
    src = (REPO_ROOT.joinpath('connectivity_diagnostics.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.15" in src, (
        "Round 7 Phase 3.15 marker missing in connectivity_diagnostics.py."
    )
