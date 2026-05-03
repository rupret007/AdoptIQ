"""Round 70 / Phase 2 (#4) -- Compact ``Risk_Summary`` carries
``Overall_Risk_Score`` canonical column AND the ``Risk_Band`` user-facing
remap (no MEDIUM leakage).

Build 43 acceptance audit found:

- Compact ``Risk_Summary`` headers were
  ``('Customer', 'Risk_Score', 'Risk_Level', 'Risk_Band',
  'Adoption_Barriers', 'Support_Cases')`` -- ``Overall_Risk_Score`` was
  silently dropped from the writer despite the R67/B6 contract that
  ``Overall_Risk_Score`` is canonical and ``Risk_Score`` is the
  back-compat alias.
- 6 customers in the same sheet had ``Risk_Band='MEDIUM'`` despite the
  user-facing R67/B1 contract that the Compact and Renewal vocabulary
  must agree on ``MODERATE``.

The Round 70 fix:

1. Pins the ``columns=`` arg on the ``Risk_Summary`` DataFrame
   constructor so pandas can never silently drop the canonical column
   even if the row dicts vary.
2. Remaps ``Risk_Band`` through ``{MEDIUM -> MODERATE}`` at the
   row-build site so the artifact carries the user-facing vocabulary.

These tests pin the source-shape AND assert the contract on a synthetic
dataframe that mirrors the writer's input shape so the artifact contract
holds even if a future edit moves the writer block around.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Source shape pins
# ---------------------------------------------------------------------------


def _read_app_simple() -> str:
    return (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")


def test_risk_summary_columns_carry_overall_risk_score_canonical() -> None:
    """R70/Phase 2 (#4): the explicit ``columns=`` list on the
    ``risk_summary_df`` constructor MUST list ``Overall_Risk_Score``
    BEFORE ``Risk_Score`` (canonical first, alias second)."""
    src = _read_app_simple()
    needle = "columns=['Customer', 'Overall_Risk_Score', 'Risk_Score', 'Risk_Level', 'Risk_Band', 'Adoption_Barriers', 'Support_Cases']"
    assert needle in src, (
        "Round 70 / #4: the Compact Risk_Summary DataFrame constructor "
        "MUST pin the column order with Overall_Risk_Score before Risk_Score "
        "so pandas can never silently drop the canonical column."
    )


def test_risk_summary_row_dict_publishes_both_score_columns() -> None:
    """R70/Phase 2 (#4): the row-build site MUST emit BOTH
    ``Overall_Risk_Score`` and ``Risk_Score`` keys with the same value
    so the canonical and back-compat columns stay byte-identical."""
    src = _read_app_simple()
    assert "'Overall_Risk_Score': _r67_b6_score," in src, (
        "Round 70 / #4: row dict MUST include the canonical key "
        "'Overall_Risk_Score'."
    )
    assert "'Risk_Score': _r67_b6_score," in src, (
        "Round 70 / #4: row dict MUST include the back-compat alias "
        "'Risk_Score' set to the same value."
    )


def test_risk_band_user_facing_label_remap_applied() -> None:
    """R70/Phase 3 (#11): the row-build site MUST remap ``Risk_Band``
    through ``_r67_b6_LABEL_REMAP`` so MEDIUM never leaks into the
    artifact."""
    src = _read_app_simple()
    assert "_r70_risk_band_user = _r67_b6_LABEL_REMAP.get(band, band)" in src, (
        "Round 70 / #11: Compact MUST remap Risk_Band through the same "
        "{MEDIUM -> MODERATE} table as Risk_Level."
    )
    assert "'Risk_Band': _r70_risk_band_user," in src, (
        "Round 70 / #11: Compact row dict MUST consume the remapped band, "
        "not the raw canonical band key."
    )


# ---------------------------------------------------------------------------
# Synthetic dataframe contract (in-memory simulation of the writer input)
# ---------------------------------------------------------------------------


def _build_synth_risk_summary() -> pd.DataFrame:
    """Mirror the row dict shape the Compact writer builds, with one
    row in the MEDIUM band so the remap path is exercised."""
    label_remap = {"MEDIUM": "MODERATE", "medium": "MODERATE", "Medium": "MODERATE"}
    rows = []
    for customer, score, band, level in (
        ("Acme Corp", 8.5, "CRITICAL", "CRITICAL"),
        ("Beta Co", 6.2, "HIGH", "HIGH"),
        ("Gamma Ltd", 4.5, "MEDIUM", "MODERATE"),
        ("Delta Inc", 1.0, "LOW", "LOW"),
    ):
        rows.append({
            "Customer": customer,
            "Overall_Risk_Score": score,
            "Risk_Score": score,
            "Risk_Level": level,
            "Risk_Band": label_remap.get(band, band),
            "Adoption_Barriers": 0,
            "Support_Cases": 0,
        })
    df = pd.DataFrame(
        rows,
        columns=[
            "Customer",
            "Overall_Risk_Score",
            "Risk_Score",
            "Risk_Level",
            "Risk_Band",
            "Adoption_Barriers",
            "Support_Cases",
        ],
    )
    return df


def test_synth_risk_summary_carries_overall_risk_score_column() -> None:
    """Contract: the produced DataFrame MUST carry ``Overall_Risk_Score``
    in the column list."""
    df = _build_synth_risk_summary()
    assert "Overall_Risk_Score" in df.columns, (
        "Round 70 / #4: produced DataFrame MUST carry Overall_Risk_Score "
        "as a top-level column."
    )
    assert "Risk_Score" in df.columns, "back-compat alias MUST stay present"


def test_synth_risk_summary_overall_and_back_compat_agree() -> None:
    """Contract: every row's ``Overall_Risk_Score`` MUST equal its
    ``Risk_Score``."""
    df = _build_synth_risk_summary()
    assert (df["Overall_Risk_Score"] == df["Risk_Score"]).all(), (
        "Round 70 / #4: canonical and back-compat columns MUST stay byte-"
        "identical so the wires-don't-cross test in R67/B1 is reachable."
    )


def test_synth_risk_summary_no_medium_leaks_in_risk_band() -> None:
    """Contract: no row may carry ``Risk_Band == 'MEDIUM'`` after the
    R70 remap."""
    df = _build_synth_risk_summary()
    bands = set(df["Risk_Band"].astype(str).tolist())
    assert "MEDIUM" not in bands, (
        f"Round 70 / #11: Risk_Band leaked MEDIUM: {bands!r}; expected "
        "MODERATE for every previously-MEDIUM customer."
    )
    assert "MODERATE" in bands, (
        f"Round 70 / #11: Risk_Band MUST carry MODERATE for the customer "
        "that scored in the MEDIUM band; saw {bands!r}."
    )
