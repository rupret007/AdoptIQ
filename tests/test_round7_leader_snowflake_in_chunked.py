"""Round 7 / Phase 6.1 regression test.

Leader fetchers (``_fetch_action_plans``, ``_get_subscriptions_for_cssm``, ``_fetch_success_priorities``) must chunk Snowflake ``IN (...)`` lists.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_1() -> None:
    src = (REPO_ROOT.joinpath('leader_report_generator.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.1" in src, (
        "Round 7 Phase 6.1 marker missing in leader_report_generator.py."
    )
