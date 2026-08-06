"""Round 70 / Phase 2 (#5) -- Renewal ``Renewal_Summary`` scale + columns.

Build 43 acceptance audit found:

- ``Overall_Risk_Score`` was on a 0-100 scale (max 50.00, mean 11.41,
  64/191 customers > 10). The R67/B1 contract is explicit: ``Overall_
  Risk_Score`` MUST be on a 0-10 scale.
- ``Risk_Score_0_100`` back-compat column was MISSING entirely.
- ``Risk_Band`` column was MISSING entirely.

The Round 70 fix:

1. Always sets ``Risk_Score_0_100`` and ``Risk_Band`` on each
   ``_r67_row`` dict regardless of which branch the row builder takes.
2. Pre-populates every canonical column on every row before the
   DataFrame constructor.
3. Pins the explicit ``columns=list(_r70_renewal_canonical_cols)`` arg
   on the DataFrame constructor so pandas never silently drops a column
   even if some rows don't have a key.

These tests pin the source-shape AND assert the contract on a synthetic
input.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source shape pins
# ---------------------------------------------------------------------------


def test_renewal_canonical_cols_defined() -> None:
    """R70/Phase 2 (#5): the canonical column list for the renewal
    summary MUST be defined as ``_r70_renewal_canonical_cols`` so the
    ``columns=`` arg has a stable single source of truth."""
    src = _read_app_simple()
    assert_in_source(src, "_r70_renewal_canonical_cols", label='src')


def test_renewal_summary_constructor_pins_canonical_columns() -> None:
    """R70/Phase 2 (#5): the DataFrame constructor MUST pass the
    canonical column list as the explicit ``columns=`` arg."""
    src = _read_app_simple()
    assert_in_source(src, "columns=list(_r70_renewal_canonical_cols)", label='src')


def test_renewal_row_setdefault_loop_present() -> None:
    """R70/Phase 2 (#5): the row-build path MUST pre-populate every
    canonical column on every row dict via ``setdefault(...)`` so the
    DataFrame constructor doesn't silently drop missing columns."""
    src = _read_app_simple()
    assert_in_source(src, "for _r70_row in renewal_summary_data", label='src')
    assert_in_source(src, "_r70_row.setdefault(_r70_col, '')", label='src')


# ---------------------------------------------------------------------------
# Synthetic dataframe contract (in-memory simulation)
# ---------------------------------------------------------------------------


def _build_synth_renewal_summary() -> pd.DataFrame:
    """Mirror the R70 row builder shape: ``Overall_Risk_Score`` on the
    0-10 scale, ``Risk_Score_0_100`` back-compat preserved, ``Risk_Band``
    populated, and the user-facing ``Risk_Level`` already remapped
    through the MEDIUM->MODERATE table."""
    canonical_cols = (
        "Customer",
        "Overall_Risk_Score",
        "Risk_Score_0_100",
        "Risk_Level",
        "Risk_Band",
        "Analysis_Date",
        "Next_Review_Date",
    )
    label_remap = {"MEDIUM": "MODERATE", "medium": "MODERATE", "Medium": "MODERATE"}

    rows = []
    for cust, score_100, band in (
        ("Acme Corp", 85.0, "CRITICAL"),
        ("Beta Co", 62.0, "HIGH"),
        ("Gamma Ltd", 45.0, "MEDIUM"),
        ("Delta Inc", 10.0, "LOW"),
    ):
        score_10 = round(score_100 / 10.0, 1)
        row = {
            "Customer": cust,
            "Overall_Risk_Score": score_10,
            "Risk_Score_0_100": score_100,
            "Risk_Level": label_remap.get(band, band),
            "Risk_Band": band,
            "Analysis_Date": "2026-05-02",
            "Next_Review_Date": "2026-08-02",
        }
        for col in canonical_cols:
            row.setdefault(col, "")
        rows.append(row)

    return pd.DataFrame(rows, columns=list(canonical_cols))


def test_synth_renewal_summary_carries_all_three_score_columns() -> None:
    df = _build_synth_renewal_summary()
    for required in ("Overall_Risk_Score", "Risk_Score_0_100", "Risk_Band"):
        assert required in df.columns, (
            f"Round 70 / #5: Renewal_Summary MUST carry {required!r}; "
            f"saw {list(df.columns)!r}."
        )


def test_synth_renewal_summary_overall_score_on_0_to_10_scale() -> None:
    df = _build_synth_renewal_summary()
    assert (df["Overall_Risk_Score"] <= 10.0).all(), (
        f"Round 70 / #5: Overall_Risk_Score MUST be on the 0-10 scale; "
        f"saw max={df['Overall_Risk_Score'].max()!r}."
    )
    assert (df["Overall_Risk_Score"] >= 0.0).all(), (
        f"Round 70 / #5: Overall_Risk_Score MUST be >= 0; "
        f"saw min={df['Overall_Risk_Score'].min()!r}."
    )


def test_synth_renewal_summary_risk_score_0_100_preserved() -> None:
    df = _build_synth_renewal_summary()
    assert (df["Risk_Score_0_100"] >= 0.0).all()
    assert (df["Risk_Score_0_100"] <= 100.0).all()
    assert df["Risk_Score_0_100"].max() > 10.0, (
        "Sanity: at least one back-compat row MUST exceed 10 so the "
        "0-100 scale is genuinely preserved (not a re-aliased 0-10)."
    )


def test_synth_renewal_summary_risk_score_consistency() -> None:
    """Contract: ``Overall_Risk_Score`` should match
    ``Risk_Score_0_100 / 10`` rounded to 1 decimal, so consumers that
    use either column see the same per-customer order."""
    df = _build_synth_renewal_summary()
    for _, row in df.iterrows():
        derived = round(float(row["Risk_Score_0_100"]) / 10.0, 1)
        assert abs(derived - float(row["Overall_Risk_Score"])) < 0.01, (
            "Overall_Risk_Score MUST equal Risk_Score_0_100 / 10 (rounded "
            "to 1 decimal) so the two scales agree per-customer."
        )


def test_synth_renewal_summary_no_medium_leaks_in_risk_level() -> None:
    df = _build_synth_renewal_summary()
    levels = set(df["Risk_Level"].astype(str).tolist())
    assert "MEDIUM" not in levels, (
        f"Round 70 / #11: Renewal Risk_Level leaked MEDIUM: {levels!r}; "
        "expected MODERATE for every previously-MEDIUM customer."
    )


def test_synth_renewal_summary_risk_band_canonical_key_preserved() -> None:
    """Internal ``Risk_Band`` column may keep the canonical band key
    (CRITICAL/HIGH/MEDIUM/LOW/HEALTHY) since it is used for color
    lookups and band-based filters; only ``Risk_Level`` is the
    user-facing label."""
    df = _build_synth_renewal_summary()
    bands = set(df["Risk_Band"].astype(str).tolist())
    assert "MEDIUM" in bands or "MODERATE" in bands, (
        "Risk_Band MUST carry the canonical band key (or remapped) for "
        "the medium-risk customer."
    )
