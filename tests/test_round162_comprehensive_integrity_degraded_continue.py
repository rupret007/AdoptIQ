"""Round 162 — Comprehensive integrity gate degrades instead of hard-aborting."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app_simple


def test_r162_both_empty_with_team_subs_does_not_abort() -> None:
    team_subs = pd.DataFrame({"BU_NAME": ["ACME"], "SUBSCRIPTION_ID": ["SUB1"]})
    should_abort, warnings = app_simple._r162_comprehensive_integrity_should_abort(
        pd.DataFrame(),
        pd.DataFrame(),
        team_subs_df=team_subs,
        csconsole_action_plans=pd.DataFrame(),
        integrity_reason=app_simple._R162_INTEGRITY_BOTH_EMPTY_REASON,
    )
    assert should_abort is False
    kinds = {w.get("kind") for w in warnings}
    assert "scoped_ab_empty" in kinds
    assert "scoped_csone_empty" in kinds


def test_r162_both_empty_without_team_or_csconsole_aborts() -> None:
    should_abort, warnings = app_simple._r162_comprehensive_integrity_should_abort(
        pd.DataFrame(),
        pd.DataFrame(),
        team_subs_df=pd.DataFrame(),
        csconsole_action_plans=pd.DataFrame(),
        integrity_reason=app_simple._R162_INTEGRITY_BOTH_EMPTY_REASON,
    )
    assert should_abort is True
    assert warnings == []


def test_r162_csconsole_only_continues_without_team_subs() -> None:
    action_plans = pd.DataFrame({"ID": ["AP1"], "BU_NAME": ["ACME"]})
    should_abort, warnings = app_simple._r162_comprehensive_integrity_should_abort(
        pd.DataFrame(),
        pd.DataFrame(),
        team_subs_df=pd.DataFrame(),
        csconsole_action_plans=action_plans,
        integrity_reason=app_simple._R162_INTEGRITY_BOTH_EMPTY_REASON,
    )
    assert should_abort is False
    assert any(w.get("kind") == "scoped_csone_empty" for w in warnings)


def test_r162_quality_integrity_reason_still_aborts() -> None:
    should_abort, _warnings = app_simple._r162_comprehensive_integrity_should_abort(
        pd.DataFrame({"title": ["x"], "description": ["y"]}),
        pd.DataFrame(),
        team_subs_df=pd.DataFrame({"BU_NAME": ["ACME"]}),
        integrity_reason="Detected 3 future-dated AB rows.",
    )
    assert should_abort is True


def test_r162_helper_present_in_app_simple_source() -> None:
    src = Path(app_simple.__file__).read_text(encoding="utf-8")
    assert "def _r162_comprehensive_integrity_should_abort" in src
    assert "Partial Data — Continuing" in src
