"""Round 124 / F2 -- Leader AP + CP team headlines deduped by distinct ID.

The Leader "Key Insights" and "Team Performance Metrics" tables summed per-CSSM
row counts for Action Plans and Customer Pulse. When an account is shared across
CSSMs (the R72 ``_ATTRIBUTED_BY_ACCOUNT`` pathway), the same Snowflake row (same
``ID``) lands in multiple per-CSSM frames, so the raw sum over-counts the team
headline (Build 92: Brian AP 655 vs 553-row deduped sheet, CP 44 vs 39). The fix
mirrors the R53.2 AB dedup: recompute AP/CP totals as distinct ``ID`` counts.
"""

from pathlib import Path

import pandas as pd

import canonical_metrics as cm

_LRG = Path(__file__).resolve().parent.parent / "leader_report_generator.py"


def test_count_total_action_plans_distinct_id():
    df = pd.DataFrame({"ID": ["a", "a", "b", "c", "c"]})
    assert cm.count_total_action_plans(df) == 3


def test_count_total_customer_pulse_distinct_id():
    df = pd.DataFrame({"ID": ["p1", "p1", "p2"]})
    assert cm.count_total_customer_pulse(df) == 2


def test_helpers_none_and_empty():
    assert cm.count_total_action_plans(None) == 0
    assert cm.count_total_customer_pulse(pd.DataFrame()) == 0


def test_helpers_fallback_rowcount_when_no_id_column():
    # No ID column -> behaviour-preserving rowcount fallback.
    df = pd.DataFrame({"Title": ["x", "y", "z"]})
    assert cm.count_total_action_plans(df) == 3
    assert cm.count_total_customer_pulse(df) == 3


def test_helpers_fallback_rowcount_when_all_ids_null():
    df = pd.DataFrame({"ID": [None, None]})
    assert cm.count_total_action_plans(df) == 2


def test_shared_account_collapses_to_distinct_count():
    # CSSM A and CSSM B both own shared account -> AP id "shared" appears twice.
    cssm_a = pd.DataFrame({"ID": ["ap1", "shared"]})
    cssm_b = pd.DataFrame({"ID": ["ap2", "shared"]})
    summed = len(cssm_a) + len(cssm_b)  # 4 (the old over-count)
    deduped = cm.count_total_action_plans(
        pd.concat([cssm_a, cssm_b], ignore_index=True)
    )
    assert summed == 4
    assert deduped == 3


def test_leader_source_carries_r124_apcp_dedup_markers():
    src = _LRG.read_text(encoding="utf-8")
    # Key Insights / Team Activity Summary recompute.
    assert "cm.count_total_action_plans(_r124_ap_combined)" in src
    assert "cm.count_total_customer_pulse(_r124_cp_combined)" in src
    # Per-person "Team Performance Metrics" recompute.
    assert "cm.count_total_action_plans(" in src
    assert "Round 124 / F2" in src
    # Must NOT have removed the R53.2 AB dedup it mirrors.
    assert "cm.count_total_barriers(_r532_combined)" in src


def test_tac_team_headline_uses_logical_union():
    # Direct callers and legacy cached team_data can bypass one-CSSM
    # attribution, so the team headline must collapse the combined TAC frame.
    src = _LRG.read_text(encoding="utf-8")
    assert "_logical_tac_combined" in src
    assert "total_tac = self._count_logical_tac_cases(_logical_tac_combined)" in src
