"""Round 7 / Phase 3.12 regression test.

``incident_storage`` must not call ``init_db()`` at import time; bootstrap should call it.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_12() -> None:
    src = (REPO_ROOT.joinpath('incident_storage.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.12" in src, (
        "Round 7 Phase 3.12 marker missing in incident_storage.py."
    )
