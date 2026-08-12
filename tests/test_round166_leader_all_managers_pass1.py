"""Round 166 All Managers Leader roster must include every CSSM email."""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import app_simple as app_mod


def _leader_cssm_emails(manager: str) -> list[str]:
    return [
        email
        for mgr, _name, email in app_mod.TEAM_ROSTER
        if mgr == manager or manager == "All Managers"
    ]


def test_leader_worker_includes_all_managers_roster_branch() -> None:
    source = Path(app_mod.__file__).read_text(encoding="utf-8")
    anchor = source.index("def run_leader_report_generation(")
    region = source[anchor : anchor + 12000]
    assert_in_source(
        region,
        'if mgr == manager or manager == "All Managers"',
        label="leader cssm roster",
    )


def test_all_managers_cssm_emails_cover_entire_team_roster() -> None:
    selected = _leader_cssm_emails("All Managers")
    assert selected
    assert len(selected) == len(app_mod.TEAM_ROSTER)


def test_named_manager_cssm_emails_stay_manager_scoped() -> None:
    selected = _leader_cssm_emails("Brian Frazier")
    expected = [email for mgr, _name, email in app_mod.TEAM_ROSTER if mgr == "Brian Frazier"]
    assert selected == expected
    assert len(selected) < len(app_mod.TEAM_ROSTER)
