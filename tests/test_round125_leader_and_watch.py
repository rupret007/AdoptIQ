"""Round 125 / Build 94 (D1, B5) -- Leader per-CSSM dedup + Watch tile math.

* D1: the Leader DOCX per-CSSM activity body + the XLSX ``Team_Summary``
  rows must count DISTINCT source IDs so a shared-account record that is
  attributed to multiple CSSMs is not raw-summed above the deduped TOTAL.
  ``canonical_metrics.count_total_action_plans`` / ``count_total_customer_pulse``
  are the SSoT distinct-ID counters; this pins they collapse a shared ID.
* B5: the "Score 4-6 (Watch)" tile uses ``count_score_range`` on the 0-10
  scale with the literal [4.0, 6.0) bounds the label advertises (not the
  band-derived 3.5-5.5 cutoffs).
"""

import pandas as pd

import canonical_metrics as cm


# --------------------------------------------------------------------------
# D1: distinct-ID counters collapse shared-account fan-out
# --------------------------------------------------------------------------

def test_count_total_action_plans_dedups_shared_id():
    # Same AP ID attributed to two CSSMs -> distinct count is 1, not 2.
    df = pd.DataFrame({"ID": ["AP-1", "AP-1", "AP-2"], "owner": ["a", "b", "a"]})
    assert cm.count_total_action_plans(df) == 2


def test_count_total_customer_pulse_dedups_shared_id():
    df = pd.DataFrame({"ID": ["CP-1", "CP-1"], "owner": ["a", "b"]})
    assert cm.count_total_customer_pulse(df) == 1


def test_count_total_action_plans_empty_and_none():
    assert cm.count_total_action_plans(None) == 0
    assert cm.count_total_action_plans(pd.DataFrame()) == 0


def test_per_person_can_sum_above_deduped_total():
    # Two CSSMs each see the SAME shared AP plus one unique each.
    cssm_a = pd.DataFrame({"ID": ["AP-shared", "AP-a"]})
    cssm_b = pd.DataFrame({"ID": ["AP-shared", "AP-b"]})
    per_person_sum = (
        cm.count_total_action_plans(cssm_a)
        + cm.count_total_action_plans(cssm_b)
    )
    deduped_total = cm.count_total_action_plans(
        pd.concat([cssm_a, cssm_b], ignore_index=True)
    )
    # per-person rows legitimately sum ABOVE the deduped headline.
    assert per_person_sum == 4
    assert deduped_total == 3
    assert per_person_sum > deduped_total


# --------------------------------------------------------------------------
# B5: Watch tile uses literal [4.0, 6.0) on the 0-10 scale
# --------------------------------------------------------------------------

def _profiles_0_10(scores):
    return {f"cust{i}": {"risk_score_0_10": s} for i, s in enumerate(scores)}


def test_watch_count_uses_literal_4_to_6_bounds():
    # 3.9 (out, below), 4.0 (in), 5.5 (in), 5.99 (in), 6.0 (out, exclusive high).
    profiles = _profiles_0_10([3.9, 4.0, 5.5, 5.99, 6.0])
    count = cm.count_score_range(profiles, low=4.0, high=6.0, scale=cm.RISK_SCALE_0_TO_10)
    assert count == 3


def test_watch_count_excludes_old_band_derived_cutoff():
    # A 3.6 score (inside the OLD 3.5-5.5 band-derived window) must NOT be
    # counted under the corrected [4.0, 6.0) bounds.
    profiles = _profiles_0_10([3.6])
    count = cm.count_score_range(profiles, low=4.0, high=6.0, scale=cm.RISK_SCALE_0_TO_10)
    assert count == 0
