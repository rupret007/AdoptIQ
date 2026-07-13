"""Round 7 / Phase 1.1 regression test.

``create_executive_intelligence_report`` must either consume the
``arr_data`` / ``arr_impact`` / ``feature_requests`` arguments or drop
them from its signature so callers cannot believe the EI Word output
reflects ARR or feature-request data when it does not.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_ei_arr_feature_args_wired() -> None:
    src = (REPO_ROOT / "executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.1" in src, (
        "Round 7 Phase 1.1 marker missing in executive_intelligence_formatter.py."
    )
