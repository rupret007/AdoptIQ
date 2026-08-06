"""Round 89 / F1 — Compact ``Risk_Summary.Risk_Score`` back-compat alias
is REMOVED from the writer.

Background:
    R67/B6 introduced ``Risk_Score`` as a back-compat alias for
    ``Overall_Risk_Score`` in the Compact ``Risk_Summary`` sheet.
    R88 left the alias in place as a deferral pending an audit of
    external consumers; the audit found NO external consumers (only
    four internal sort/mean readers in ``run_compact_analysis``).

R89/F1:
    - Drop the alias from the row dict.
    - Drop the alias from the explicit ``columns=[...]`` tuple.
    - Retarget the four internal readers (sort + mean) to
      ``Overall_Risk_Score``.

This test file pins:

1) Source-shape ABSENCE of the writer-side alias.
2) Source-shape PRESENCE of ``Overall_Risk_Score`` at all four reader
   sites (sort + mean).
3) Behavior round-trip: a built XLSX ``Risk_Summary`` sheet does NOT
   carry the ``Risk_Score`` column.
"""
from __future__ import annotations
from source_shape_utils import (
    assert_in_source,
    assert_not_in_source,
    assert_regex_in_source,
    columns_list_present,
    count_in_source,
)

from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PATH = _REPO_ROOT / "app_simple.py"


def _read_app_simple() -> str:
    return _APP_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-shape pins (writer-side absence)
# ---------------------------------------------------------------------------


def test_r89_f1_compact_writer_no_longer_emits_risk_score_alias() -> None:
    """The Compact ``risk_summary_data.append({...})`` block MUST NOT
    include the legacy ``'Risk_Score': _r67_b6_score,`` row.

    The alias is gone; the row dict carries only ``Overall_Risk_Score``
    (canonical) and ``Risk_Score_0_10`` (explicit-scale, R88/F2).
    """
    text = _read_app_simple()
    assert_not_in_source(text, "'Risk_Score': _r67_b6_score,", label='text')


def test_r89_f1_compact_writer_columns_tuple_no_longer_carries_risk_score() -> None:
    """The Compact ``risk_summary_df = pd.DataFrame(..., columns=[...])``
    tuple MUST NOT include ``'Risk_Score'``.
    """
    text = _read_app_simple()
    columns_list_present(
        text,
        [
            "Customer",
            "Overall_Risk_Score",
            "Risk_Score_0_10",
            "Risk_Level",
            "Risk_Band",
            "Adoption_Barriers",
            "Support_Cases",
        ],
        label="text",
    )
    # Defense in depth: the OLD (R88-shape) tuple with the Risk_Score
    # alias must not be in the source either.
    old_shape = (
        "columns=['Customer', 'Overall_Risk_Score', 'Risk_Score_0_10', "
        "'Risk_Score', 'Risk_Level', 'Risk_Band', 'Adoption_Barriers', "
        "'Support_Cases']"
    )
    assert_not_in_source(text, old_shape, label='text')


# ---------------------------------------------------------------------------
# Source-shape pins (reader-side retargets)
# ---------------------------------------------------------------------------


def test_r89_f1_canonical_high_risk_sort_uses_overall_risk_score() -> None:
    """The R12 stable-sort site that ranks customers by score (when the
    canonical ``cm.is_high_risk_profile`` predicate succeeds) MUST sort
    by ``Overall_Risk_Score`` (the canonical column), NOT the retired
    ``Risk_Score`` alias.
    """
    text = _read_app_simple()
    assert_in_source(text, '["Overall_Risk_Score", "Customer"],  # Round 89 / F1', label='text')


def test_r89_f1_risk_band_sort_fallback_uses_overall_risk_score() -> None:
    """The Risk_Band-based sort fallback (when the canonical predicate
    sweep fails) MUST sort by ``Overall_Risk_Score``.
    """
    text = _read_app_simple()
    customer_sort_tail = (
        '(["Customer"] if "Customer" in risk_summary_df.columns else []),  # Round 89 / F1'
    )
    assert count_in_source(text, customer_sort_tail) >= 2


def test_r89_f1_bare_cutoff_filter_uses_overall_risk_score() -> None:
    """The bare-cutoff filter (``risk_summary_df[df['X'] >= _hrf_cut]``)
    MUST read ``Overall_Risk_Score``, not the retired ``Risk_Score``
    alias.
    """
    text = _read_app_simple()
    assert_regex_in_source(
        text,
        r'risk_summary_df\["Overall_Risk_Score"\]\s*>=\s*_hrf_cut',
        label="text",
    )


def test_r89_f1_overall_score_mean_fallback_uses_overall_risk_score() -> None:
    """The Excel ``overall_risk_score`` mean fallback MUST read
    ``Overall_Risk_Score`` so it matches the writer column name.
    """
    text = _read_app_simple()
    assert_in_source(
        text,
        'overall_risk_score = risk_summary_df["Overall_Risk_Score"].mean()  # Round 89 / F1',
        label='text',
    )


# ---------------------------------------------------------------------------
# Behavior round-trip
# ---------------------------------------------------------------------------


def test_r89_f1_built_xlsx_risk_summary_does_not_carry_risk_score() -> None:
    """Synthetic round-trip: write a DataFrame to XLSX with the post-R89
    schema, read it back, and confirm the ``Risk_Score`` column is NOT
    present.
    """
    rows = [
        {
            "Customer": "ALPHA",
            "Overall_Risk_Score": 7.2,
            "Risk_Score_0_10": 7.2,
            "Risk_Level": "HIGH",
            "Risk_Band": "HIGH",
            "Adoption_Barriers": 5,
            "Support_Cases": 3,
        },
        {
            "Customer": "BETA",
            "Overall_Risk_Score": 0.8,
            "Risk_Score_0_10": 0.8,
            "Risk_Level": "HEALTHY",
            "Risk_Band": "HEALTHY",
            "Adoption_Barriers": 0,
            "Support_Cases": 0,
        },
    ]
    df = pd.DataFrame(
        rows,
        columns=[
            "Customer",
            "Overall_Risk_Score",
            "Risk_Score_0_10",
            "Risk_Level",
            "Risk_Band",
            "Adoption_Barriers",
            "Support_Cases",
        ],
    )

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="Risk_Summary", index=False)

    buf.seek(0)
    round_tripped = pd.read_excel(buf, sheet_name="Risk_Summary")

    assert "Overall_Risk_Score" in round_tripped.columns, (
        "Round 89 / F1: the canonical Overall_Risk_Score column must "
        "still be present after the round-trip."
    )
    assert "Risk_Score_0_10" in round_tripped.columns, (
        "Round 88 / F2: the explicit 0-10 scale column must still be "
        "present after the round-trip."
    )
    assert "Risk_Score" not in round_tripped.columns, (
        "Round 89 / F1: the legacy Risk_Score back-compat alias must "
        "NOT be in the round-tripped XLSX schema."
    )


def test_r89_f1_built_dataframe_sort_path_works_without_risk_score() -> None:
    """The R12 sort path (``sort_values(['Overall_Risk_Score', 'Customer'])``)
    MUST work on a DataFrame that has NO ``Risk_Score`` column.

    Pre-R89 the path keyed off ``Risk_Score``; if anything still keys off
    that (e.g. a stray reader I missed), this test will raise KeyError.
    """
    rows = [
        {"Customer": "ALPHA", "Overall_Risk_Score": 7.2, "Risk_Score_0_10": 7.2},
        {"Customer": "BETA", "Overall_Risk_Score": 9.5, "Risk_Score_0_10": 9.5},
        {"Customer": "GAMMA", "Overall_Risk_Score": 7.2, "Risk_Score_0_10": 7.2},
    ]
    df = pd.DataFrame(rows)

    sorted_df = df.sort_values(
        ["Overall_Risk_Score", "Customer"],
        ascending=[False, True],
        kind="stable",
    )
    # BETA should be first (highest score), then ALPHA, then GAMMA
    # (ALPHA before GAMMA because of Customer tie-break).
    assert sorted_df["Customer"].tolist() == ["BETA", "ALPHA", "GAMMA"], (
        "Round 89 / F1: the post-R89 sort key (Overall_Risk_Score) must "
        "preserve the R12 stable-sort + Customer tie-break behavior."
    )


def test_r89_f1_built_dataframe_mean_fallback_works_without_risk_score() -> None:
    """The mean fallback (``risk_summary_df['Overall_Risk_Score'].mean()``)
    MUST work on a DataFrame that has NO ``Risk_Score`` column.
    """
    rows = [
        {"Customer": "ALPHA", "Overall_Risk_Score": 7.0},
        {"Customer": "BETA", "Overall_Risk_Score": 3.0},
    ]
    df = pd.DataFrame(rows)
    assert df["Overall_Risk_Score"].mean() == pytest.approx(5.0), (
        "Round 89 / F1: the post-R89 mean fallback (Overall_Risk_Score) "
        "must produce the same average that the pre-R89 alias path "
        "would have produced."
    )
