"""Round 79 / Build 55 / Phase 3 (B3): per-technology focus-area rollup tests.

Pins ``be_priority_scorer.compute_be_focus_areas`` -- the deterministic
groupby + cluster ranking that drives the new ``BE_Focus_Areas`` XLSX
sheet AND the new Word section.

Determinism contract (must hold byte-for-byte across runs):
    1. Technology ASC
    2. Cluster_Focus_Score DESC
    3. Theme ASC

Round 79 / Phase 3 (B3).  Made-with: Cursor.
"""

from __future__ import annotations

import pandas as pd
import pytest

import be_priority_scorer as bes


def _scored_frame(rows: list[dict]) -> pd.DataFrame:
    """Wrap a list of dicts into the AB-scored DataFrame shape that
    ``compute_be_focus_areas`` consumes."""
    df = pd.DataFrame(rows)
    if "be_priority_score" not in df.columns:
        df["be_priority_score"] = 50.0
    if "be_llm_class" not in df.columns:
        df["be_llm_class"] = "UNCLASSIFIED"
    return df


# ---------------------------------------------------------------------------
# Tier 1: empty / provenance fallback
# ---------------------------------------------------------------------------


def test_empty_input_returns_provenance_row():
    out = bes.compute_be_focus_areas(pd.DataFrame())
    assert len(out) == 1
    assert "_adoptiq_provenance_row" in out.columns
    assert bool(out["_adoptiq_provenance_row"].iloc[0]) is True
    assert out["AdoptIQ_Status"].iloc[0] == "EMPTY"


def test_none_input_returns_provenance_row():
    out = bes.compute_be_focus_areas(None)
    assert len(out) == 1
    assert bool(out["_adoptiq_provenance_row"].iloc[0]) is True


def test_all_clusters_below_threshold_returns_provenance_row():
    """When every cluster's focus score is below ``min_cluster_score``,
    a single provenance row is returned (R67/B2 always-present contract)."""
    df = _scored_frame([
        {
            "ID": "AB-1",
            "title": "small issue",
            "description": "x",
            "sub_technology": "Webex",
            "ab_category_final": "Onboarding",
            "be_priority_score": 5.0,
            "customer_name": "ACME",
        }
    ])
    out = bes.compute_be_focus_areas(df, min_cluster_score=100.0)
    assert len(out) == 1
    assert bool(out["_adoptiq_provenance_row"].iloc[0]) is True
    assert "min_cluster_score" in out["AdoptIQ_Message"].iloc[0]


# ---------------------------------------------------------------------------
# Tier 2: groupby determinism
# ---------------------------------------------------------------------------


def test_groupby_determinism_same_input_same_order():
    """Same input frame -> identical row order, byte-for-byte."""
    df = _scored_frame([
        {"ID": "AB-1", "title": "t1", "description": "d1",
         "sub_technology": "Webex Calling", "ab_category_final": "Onboarding",
         "be_priority_score": 70, "customer_name": "ACME"},
        {"ID": "AB-2", "title": "t2", "description": "d2",
         "sub_technology": "Webex Calling", "ab_category_final": "Onboarding",
         "be_priority_score": 60, "customer_name": "BETA"},
        {"ID": "AB-3", "title": "t3", "description": "d3",
         "sub_technology": "Cloud Calling", "ab_category_final": "Migration",
         "be_priority_score": 80, "customer_name": "GAMMA"},
        {"ID": "AB-4", "title": "t4", "description": "d4",
         "sub_technology": "Cloud Calling", "ab_category_final": "Migration",
         "be_priority_score": 75, "customer_name": "DELTA"},
    ])
    a = bes.compute_be_focus_areas(df)
    b = bes.compute_be_focus_areas(df.copy())
    assert a.equals(b)


def test_technology_sorted_ascending():
    """Technology column is sorted alphabetically -- "A..." before "Z...".
    Build a frame where the rows arrive in reverse-tech order to confirm
    the output is re-sorted."""
    df = _scored_frame([
        {"ID": "AB-1", "title": "z", "description": "z",
         "sub_technology": "Zenith Tech", "ab_category_final": "Theme1",
         "be_priority_score": 80, "customer_name": "C1"},
        {"ID": "AB-2", "title": "z", "description": "z",
         "sub_technology": "Zenith Tech", "ab_category_final": "Theme1",
         "be_priority_score": 60, "customer_name": "C2"},
        {"ID": "AB-3", "title": "a", "description": "a",
         "sub_technology": "Alpha Tech", "ab_category_final": "Theme1",
         "be_priority_score": 70, "customer_name": "C3"},
        {"ID": "AB-4", "title": "a", "description": "a",
         "sub_technology": "Alpha Tech", "ab_category_final": "Theme1",
         "be_priority_score": 50, "customer_name": "C4"},
    ])
    out = bes.compute_be_focus_areas(df)
    techs = out["Technology"].tolist()
    # Alpha must come before Zenith
    assert techs[0].startswith("Alpha"), f"expected Alpha first, got {techs}"


def test_cluster_focus_score_sorted_descending_within_tech():
    """Within a technology, clusters are ordered by Cluster_Focus_Score DESC."""
    df = _scored_frame([
        {"ID": f"AB-{i}", "title": f"t{i}", "description": f"d{i}",
         "sub_technology": "Webex Calling",
         "ab_category_final": "Theme A" if i < 3 else "Theme B",
         "be_priority_score": 90 if i < 3 else 50,
         "customer_name": f"Customer{i}"}
        for i in range(6)
    ])
    out = bes.compute_be_focus_areas(df)
    # Theme A has higher scores -> higher cluster_focus_score -> appears first
    scores = out["Cluster_Focus_Score"].tolist()
    assert scores == sorted(scores, reverse=True), (
        f"expected DESC order, got {scores}"
    )


# ---------------------------------------------------------------------------
# Tier 3: cluster_focus_score formula
# ---------------------------------------------------------------------------


def test_cluster_focus_score_formula():
    """Verify formula: sum(be_priority) * 0.35 + true_blockers * 5
    + customers * 3 + open_count * 1."""
    df = _scored_frame([
        {"ID": "AB-1", "title": "t1", "description": "d1",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 80.0, "customer_name": "ACME",
         "be_llm_class": "TRUE_BLOCKER"},
        {"ID": "AB-2", "title": "t2", "description": "d2",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 60.0, "customer_name": "BETA",
         "be_llm_class": "TRAINING_GAP"},
        {"ID": "AB-3", "title": "t3", "description": "d3",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 40.0, "customer_name": "ACME",  # same customer as AB-1
         "be_llm_class": "AMBIGUOUS"},
    ])
    out = bes.compute_be_focus_areas(df, min_cluster_score=0.0)
    # sum(be_priority) = 80 + 60 + 40 = 180
    # true_blockers = 1 (only AB-1)
    # customers = 2 (ACME, BETA)
    # open_count = 3
    # focus_score = 180*0.35 + 1*5 + 2*3 + 3 = 63 + 5 + 6 + 3 = 77
    assert out.iloc[0]["Cluster_Focus_Score"] == pytest.approx(77.0, abs=0.1)
    assert out.iloc[0]["True_Blocker_Count"] == 1
    assert out.iloc[0]["Customers_Affected"] == 2
    assert out.iloc[0]["Open_Barriers"] == 3


def test_true_blocker_count_boosts_cluster_rank():
    """A cluster with TRUE_BLOCKER tags scores higher than a cluster with
    the SAME be_priority sum but no TRUE_BLOCKER tags."""
    base_kwargs = dict(title="t", description="d",
                       sub_technology="Webex", be_priority_score=50.0,
                       customer_name="ACME")
    df = _scored_frame([
        {**base_kwargs, "ID": "AB-A1", "ab_category_final": "ThemeA",
         "be_llm_class": "TRUE_BLOCKER"},
        {**base_kwargs, "ID": "AB-A2", "ab_category_final": "ThemeA",
         "be_llm_class": "TRUE_BLOCKER", "customer_name": "BETA"},
        {**base_kwargs, "ID": "AB-B1", "ab_category_final": "ThemeB",
         "be_llm_class": "AMBIGUOUS"},
        {**base_kwargs, "ID": "AB-B2", "ab_category_final": "ThemeB",
         "be_llm_class": "AMBIGUOUS", "customer_name": "BETA"},
    ])
    out = bes.compute_be_focus_areas(df, min_cluster_score=0.0)
    theme_a = out[out["Theme"] == "ThemeA"].iloc[0]
    theme_b = out[out["Theme"] == "ThemeB"].iloc[0]
    assert theme_a["Cluster_Focus_Score"] > theme_b["Cluster_Focus_Score"]


# ---------------------------------------------------------------------------
# Tier 4: max_per_tech cap
# ---------------------------------------------------------------------------


def test_max_per_tech_caps_clusters():
    """A technology with many clusters above threshold is capped at
    ``max_per_tech``."""
    rows = []
    for i in range(20):
        rows.append({
            "ID": f"AB-{i}",
            "title": f"t{i}", "description": f"d{i}",
            "sub_technology": "Webex",
            "ab_category_final": f"Theme-{i:02d}",  # 20 distinct themes
            "be_priority_score": 100.0,
            "customer_name": f"Cust{i}",
            "be_llm_class": "TRUE_BLOCKER",
        })
    df = _scored_frame(rows)
    out = bes.compute_be_focus_areas(df, max_per_tech=10, min_cluster_score=0.0)
    assert len(out) == 10
    # Tech_Rank goes 1..10
    assert out["Tech_Rank"].tolist() == list(range(1, 11))


def test_max_per_tech_per_technology_independently():
    """Two technologies each get their own ``max_per_tech`` cap."""
    rows = []
    for tech in ["Alpha", "Beta"]:
        for i in range(15):
            rows.append({
                "ID": f"{tech[0]}{i}",
                "title": f"{tech}-{i}", "description": "d",
                "sub_technology": tech,
                "ab_category_final": f"Theme-{i:02d}",
                "be_priority_score": 100.0,
                "customer_name": f"Cust{i}",
                "be_llm_class": "TRUE_BLOCKER",
            })
    df = _scored_frame(rows)
    out = bes.compute_be_focus_areas(df, max_per_tech=5, min_cluster_score=0.0)
    # Both Alpha and Beta capped at 5 -> 10 rows total
    assert len(out) == 10
    assert (out["Technology"] == "Alpha").sum() == 5
    assert (out["Technology"] == "Beta").sum() == 5


# ---------------------------------------------------------------------------
# Tier 5: column shape + sample issues
# ---------------------------------------------------------------------------


def test_output_column_shape():
    """Output carries the canonical column set in canonical order."""
    df = _scored_frame([
        {"ID": "AB-1", "title": "t1", "description": "d1",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 80, "customer_name": "ACME"},
        {"ID": "AB-2", "title": "t2", "description": "d2",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 60, "customer_name": "BETA"},
    ])
    out = bes.compute_be_focus_areas(df, min_cluster_score=0.0)
    expected_cols = [
        "Tech_Rank", "Technology", "Theme",
        "Customers_Affected", "Open_Barriers",
        "Avg_BE_Priority", "True_Blocker_Count",
        "Top_Customers", "Sample_Issues",
        "Cluster_Focus_Score",
    ]
    assert list(out.columns) == expected_cols


def test_sample_issues_picks_top_3_by_score():
    """Sample_Issues lists the top 3 AB titles in the cluster by score."""
    df = _scored_frame([
        {"ID": "AB-1", "title": "high", "description": "d",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 90, "customer_name": "C1"},
        {"ID": "AB-2", "title": "mid1", "description": "d",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 60, "customer_name": "C2"},
        {"ID": "AB-3", "title": "mid2", "description": "d",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 55, "customer_name": "C3"},
        {"ID": "AB-4", "title": "low", "description": "d",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 10, "customer_name": "C4"},
    ])
    out = bes.compute_be_focus_areas(df, min_cluster_score=0.0)
    sample = out.iloc[0]["Sample_Issues"]
    # Top 3 by score: high, mid1, mid2 -- in that order
    assert "high" in sample
    assert "mid1" in sample
    assert "mid2" in sample
    assert "low" not in sample


def test_top_customers_capped_at_5():
    """``Top_Customers`` is comma-separated, capped at 5 names alphabetised."""
    df = _scored_frame([
        {"ID": f"AB-{i}", "title": "t", "description": "d",
         "sub_technology": "Webex", "ab_category_final": "Theme",
         "be_priority_score": 50, "customer_name": f"Customer{i:02d}"}
        for i in range(8)
    ])
    out = bes.compute_be_focus_areas(df, min_cluster_score=0.0)
    customers = out.iloc[0]["Top_Customers"].split(", ")
    assert len(customers) == 5
    # Alphabetical
    assert customers == sorted(customers)


# ---------------------------------------------------------------------------
# Tier 6: edge cases
# ---------------------------------------------------------------------------


def test_missing_columns_handled_gracefully():
    """A scored frame missing optional columns falls back to defaults."""
    df = pd.DataFrame([
        {"ID": "AB-1", "be_priority_score": 80.0, "title": "t1"},
        {"ID": "AB-2", "be_priority_score": 60.0, "title": "t2"},
    ])
    out = bes.compute_be_focus_areas(df, min_cluster_score=0.0)
    # Should still produce a row (even if Technology / Theme defaulted).
    assert len(out) >= 1
    if "_adoptiq_provenance_row" not in out.columns:
        # Round 121 / G3: the bare "Unknown" sentinel is now relabeled to
        # "Other / Unclassified" at the display layer (grouping key unchanged).
        assert "Other / Unclassified" in out["Technology"].tolist()


def test_nan_technology_collapses_to_unknown():
    """Rows with NaN sub_technology bucket under the unclassified label."""
    df = pd.DataFrame([
        {"ID": "AB-1", "title": "t1", "description": "d1",
         "sub_technology": None, "ab_category_final": "Theme",
         "be_priority_score": 50, "customer_name": "C1"},
        {"ID": "AB-2", "title": "t2", "description": "d2",
         "sub_technology": float("nan"), "ab_category_final": "Theme",
         "be_priority_score": 50, "customer_name": "C2"},
    ])
    df["be_llm_class"] = "UNCLASSIFIED"
    out = bes.compute_be_focus_areas(df, min_cluster_score=0.0)
    if "_adoptiq_provenance_row" not in out.columns:
        # Round 121 / G3: displayed Technology relabeled from "Unknown".
        techs = out["Technology"].unique().tolist()
        assert techs == ["Other / Unclassified"]
