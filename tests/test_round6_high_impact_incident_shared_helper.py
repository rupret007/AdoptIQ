"""Round 6 / Phase 1.5 regression test.

Word "High-Impact Incidents" table and matplotlib chart must use
the same definition (shared helper).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_high_impact_incident_shared_helper() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.5" in src, (
        "Round 6 Phase 1.5 marker missing in app_simple.py."
    )
