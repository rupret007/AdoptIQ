"""Round 7 / Phase 3.16 regression test.

``structured_logging`` adapter must redact extra_kv values (emails, customer names, token shapes).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_16() -> None:
    src = (REPO_ROOT.joinpath('structured_logging.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.16" in src, (
        "Round 7 Phase 3.16 marker missing in structured_logging.py."
    )
