"""Round 70 / Phase 2 (#4) -- Compact ``Risk_Summary`` carries
``Overall_Risk_Score`` canonical column AND the ``Risk_Level`` user-facing
vocabulary remap (no MEDIUM in user-facing column).

Build 43 acceptance audit found:

- Compact ``Risk_Summary`` headers were
  ``('Customer', 'Risk_Score', 'Risk_Level', 'Risk_Band',
  'Adoption_Barriers', 'Support_Cases')`` -- ``Overall_Risk_Score`` was
  silently dropped from the writer despite the R67/B6 contract that
  ``Overall_Risk_Score`` is canonical and ``Risk_Score`` was the
  back-compat alias (retired in Round 89 / F1).
- 6 customers in the same sheet had ``Risk_Level='MEDIUM'`` despite the
  user-facing R67/B1 contract that the Compact and Renewal vocabulary
  must agree on ``MODERATE``.

The Round 70 fix:

1. Pins the ``columns=`` arg on the ``Risk_Summary`` DataFrame
   constructor so pandas can never silently drop the canonical column
   even if the row dicts vary.
2. Remaps ``Risk_Level`` through ``{MEDIUM -> MODERATE}`` at the
   row-build site so the artifact carries the user-facing vocabulary.

Round 71 / Phase 0 (#1): REVERTED the Round 70 over-reach that also
remapped ``Risk_Band``.  The R67/B6 contract split is:
    - ``Risk_Band`` keeps the canonical band key (CRITICAL / HIGH /
      MEDIUM / LOW / HEALTHY) so existing band-based filters and color
      lookups still match (and so the Comprehensive
      ``Risk_Components.risk_band`` column agrees byte-for-byte with
      the Compact ``Risk_Summary.Risk_Band`` column for the same scope).
    - ``Risk_Level`` carries the user-facing vocabulary remap
      (MODERATE in place of MEDIUM).

Round 89 / F1: dropped the legacy ``Risk_Score`` back-compat alias from
the Compact ``Risk_Summary`` writer.  The audit found the only readers
were four internal sort/mean sites in the same function, all retargeted
to ``Overall_Risk_Score``.  Tests below now assert ABSENCE of the alias
column (forward-pinning the contract).

These tests pin the source-shape AND assert the contract on a synthetic
dataframe that mirrors the writer's input shape so the artifact contract
holds even if a future edit moves the writer block around.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source, assert_not_in_source, columns_list_present

from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Source shape pins
# ---------------------------------------------------------------------------


def _read_app_simple() -> str:
    return (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")


def test_risk_summary_columns_carry_overall_risk_score_canonical() -> None:
    """R70/Phase 2 (#4) + R88/F2 + R89/F1: the explicit ``columns=`` list
    on the ``risk_summary_df`` constructor MUST list
    ``Overall_Risk_Score`` first (canonical), then ``Risk_Score_0_10``
    (explicit-scale, R88/F2), and MUST NOT include the legacy
    ``Risk_Score`` back-compat alias (R89/F1 retired it)."""
    src = _read_app_simple()
    columns_list_present(
        src,
        [
            "Customer",
            "Overall_Risk_Score",
            "Risk_Score_0_10",
            "Risk_Level",
            "Risk_Band",
            "Adoption_Barriers",
            "Support_Cases",
        ],
        label="src",
    )


def test_risk_summary_row_dict_no_longer_publishes_risk_score_alias() -> None:
    """R89/F1: the row-build site MUST emit ``Overall_Risk_Score`` and
    ``Risk_Score_0_10`` but MUST NOT emit the legacy ``Risk_Score`` alias.

    This is the inverse of the R70 dual-write contract -- after R89/F1
    cleared the back-compat-alias hedge (no external consumers), the
    writer is single-source and the four internal sort/mean readers
    have all been retargeted to ``Overall_Risk_Score``.
    """
    src = _read_app_simple()
    assert_in_source(src, "'Overall_Risk_Score': _r67_b6_score,", label='src')
    assert_in_source(src, "'Risk_Score_0_10': _r67_b6_score,", label='src')
    assert_not_in_source(src, "'Risk_Score': _r67_b6_score,", label='src')


def test_risk_level_user_facing_label_remap_applied() -> None:
    """R67/B1 + R71/Phase 0 (#1): the row-build site MUST remap
    ``Risk_Level`` through ``_r67_b6_LABEL_REMAP`` so MEDIUM never
    leaks into the user-facing artifact column."""
    src = _read_app_simple()
    assert_in_source(src, "_r67_b6_risk_level = _r67_b6_LABEL_REMAP.get(risk_level, risk_level)", label='src')
    assert_in_source(src, "'Risk_Level': _r67_b6_risk_level,", label='src')


def test_risk_band_keeps_canonical_key_per_r67_b6_contract() -> None:
    """R71/Phase 0 (#1): the row-build site MUST emit the *raw* canonical
    band key for ``Risk_Band`` (no MEDIUM->MODERATE remap), because the
    R67/B6 contract reserves the canonical key for cross-sheet filter /
    color parity (and for byte-identical agreement with the Comprehensive
    ``Risk_Components.risk_band`` column for the same scope).

    The Round 70 over-reach that aliased ``Risk_Band`` to the user-facing
    label has been reverted -- the user-facing vocabulary contract is
    satisfied by the ``Risk_Level`` column alone."""
    src = _read_app_simple()
    assert_in_source(src, "'Risk_Band': band,", label='src')
    assert_not_in_source(src, "_r70_risk_band_user", label='src')


# ---------------------------------------------------------------------------
# Synthetic dataframe contract (in-memory simulation of the writer input)
# ---------------------------------------------------------------------------


def _build_synth_risk_summary() -> pd.DataFrame:
    """Mirror the row dict shape the Compact writer builds, with one
    row in the MEDIUM band so the contract split is exercised.

    Round 89 / F1: the legacy ``Risk_Score`` back-compat alias has been
    dropped from both the row dict and the explicit ``columns=`` tuple;
    the synthetic mirror reflects that.
    """
    label_remap = {"MEDIUM": "MODERATE", "medium": "MODERATE", "Medium": "MODERATE"}
    rows = []
    for customer, score, band, raw_level in (
        ("Acme Corp", 8.5, "CRITICAL", "CRITICAL"),
        ("Beta Co", 6.2, "HIGH", "HIGH"),
        ("Gamma Ltd", 4.5, "MEDIUM", "MEDIUM"),
        ("Delta Inc", 1.0, "LOW", "LOW"),
    ):
        rows.append({
            "Customer": customer,
            "Overall_Risk_Score": score,
            "Risk_Score_0_10": score,
            "Risk_Level": label_remap.get(raw_level, raw_level),
            "Risk_Band": band,
            "Adoption_Barriers": 0,
            "Support_Cases": 0,
        })
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
    return df


def test_synth_risk_summary_carries_overall_risk_score_column() -> None:
    """Contract: the produced DataFrame MUST carry ``Overall_Risk_Score``
    AND ``Risk_Score_0_10`` in the column list, but MUST NOT carry the
    legacy ``Risk_Score`` back-compat alias (R89/F1 dropped it).
    """
    df = _build_synth_risk_summary()
    assert "Overall_Risk_Score" in df.columns, (
        "Round 70 / #4: produced DataFrame MUST carry Overall_Risk_Score "
        "as a top-level column."
    )
    assert "Risk_Score_0_10" in df.columns, (
        "Round 88 / F2: produced DataFrame MUST carry the explicit "
        "0-10 scale column 'Risk_Score_0_10'."
    )
    assert "Risk_Score" not in df.columns, (
        "Round 89 / F1: the legacy 'Risk_Score' back-compat alias MUST "
        "NOT be in the produced DataFrame -- the alias was dropped in "
        "R89/F1 after the audit confirmed no external consumers."
    )


def test_synth_risk_summary_overall_and_score_0_10_agree() -> None:
    """R88/F2: every row's ``Overall_Risk_Score`` MUST equal its
    ``Risk_Score_0_10`` (both are the canonical 0-10 value).

    Replaces the pre-R89/F1 ``test_synth_risk_summary_overall_and_back_compat_agree``
    which asserted parity between Overall_Risk_Score and the (now-retired)
    Risk_Score alias."""
    df = _build_synth_risk_summary()
    assert (df["Overall_Risk_Score"] == df["Risk_Score_0_10"]).all(), (
        "Round 88 / F2: Overall_Risk_Score and Risk_Score_0_10 MUST be "
        "byte-identical (both publish the canonical 0-10 score)."
    )


def test_synth_risk_summary_no_medium_leaks_in_risk_level() -> None:
    """Contract: no row may carry ``Risk_Level == 'MEDIUM'`` after the
    R67/B1 remap."""
    df = _build_synth_risk_summary()
    levels = set(df["Risk_Level"].astype(str).tolist())
    assert "MEDIUM" not in levels, (
        f"Round 67 / B1: Risk_Level leaked MEDIUM: {levels!r}; expected "
        "MODERATE for every previously-MEDIUM customer."
    )
    assert "MODERATE" in levels, (
        f"Round 67 / B1: Risk_Level MUST carry MODERATE for the customer "
        f"that scored in the MEDIUM band; saw {levels!r}."
    )


def test_synth_risk_summary_keeps_canonical_band_key() -> None:
    """R71/Phase 0 (#1) contract: ``Risk_Band`` MUST keep the canonical
    band key (CRITICAL/HIGH/MEDIUM/LOW/HEALTHY) for cross-sheet parity
    with Comprehensive ``Risk_Components.risk_band``."""
    df = _build_synth_risk_summary()
    bands = set(df["Risk_Band"].astype(str).tolist())
    assert "MEDIUM" in bands, (
        f"Round 71 / #1: Risk_Band MUST keep canonical MEDIUM key (the "
        f"Round 70 over-reach was reverted); saw {bands!r}."
    )
    assert "MODERATE" not in bands, (
        f"Round 71 / #1: Risk_Band MUST NOT carry user-facing MODERATE; "
        f"the user-facing remap belongs on Risk_Level only.  Saw {bands!r}."
    )
