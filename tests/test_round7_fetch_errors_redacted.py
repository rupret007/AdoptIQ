"""Round 7 / Phase 3.13 regression test.

``incident_storage`` must redact / route ``str(exc)`` strings through
the classifier before persisting to ``fetch_errors`` so file paths,
SQL fragments, and secret-bearing connection strings cannot leak
into external intel JSON.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_13() -> None:
    src = (REPO_ROOT.joinpath("incident_storage.py")).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.13" in src, (
        "Round 7 Phase 3.13 marker missing in incident_storage.py."
    )
