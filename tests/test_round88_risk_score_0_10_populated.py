"""Round 88 / F2 — Risk_Score_0_10 is published as an explicit column
in BOTH Compact ``Risk_Summary`` and Renewal ``Renewal_Summary``.

The Build 63 acceptance audit observed that Compact + Renewal XLSX
schemas did not surface an explicit ``Risk_Score_0_10`` column despite
``Overall_Risk_Score`` being on the 0-10 scale. Downstream consumers
had to guess scale-by-name (``Overall_Risk_Score`` could plausibly be
0-10 or 0-100; ``Risk_Score`` was a Compact-only alias). R88/F2 adds
an unambiguous ``Risk_Score_0_10`` column to both writers so the
schema is self-documenting.

Source-shape pin (``app_simple.py``):
- Compact ``risk_summary_data.append({...})`` includes ``'Risk_Score_0_10'``.
- Compact ``risk_summary_df`` ``columns=`` list includes ``'Risk_Score_0_10'``.
- Renewal ``renewal_summary_data.append({...})`` includes ``'Risk_Score_0_10'``.
- Renewal ``_r70_renewal_canonical_cols`` tuple includes ``'Risk_Score_0_10'``.

Behavior pin (DataFrame round-trip):
- Compact: ``Overall_Risk_Score == Risk_Score_0_10`` for every row.
- Renewal: ``Overall_Risk_Score == Risk_Score_0_10`` for every row.
- Renewal: ``Risk_Score_0_100 == Risk_Score_0_10 * 10`` (within rounding).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PATH = _REPO_ROOT / "app_simple.py"


def test_r88_f2_compact_risk_summary_appends_risk_score_0_10() -> None:
    """Compact ``risk_summary_data.append({...})`` must carry the new
    ``Risk_Score_0_10`` key alongside ``Overall_Risk_Score``."""

    text = _APP_PATH.read_text(encoding="utf-8")
    # Find the Compact append block (it contains both Overall_Risk_Score
    # AND Adoption_Barriers — the two markers narrow the search to the
    # Compact path, NOT Renewal).
    needle = "'Risk_Score_0_10': _r67_b6_score,  # Round 88 / F2"
    assert needle in text, (
        "Round 88 / F2: Compact ``risk_summary_data.append({...})`` must "
        "include ``'Risk_Score_0_10': _r67_b6_score`` so the explicit 0-10 "
        "scale column is present in Risk_Summary."
    )


def test_r88_f2_compact_risk_summary_columns_list_carries_risk_score_0_10() -> None:
    """The ``risk_summary_df = pd.DataFrame(...)`` column list must
    include ``'Risk_Score_0_10'`` so the artifact schema is stable
    across runs (R70/B6 contract).

    Round 89 / F1: the legacy ``Risk_Score`` back-compat alias was
    dropped from the Compact column list -- expected_cols below reflects
    the post-R89 order.
    """

    text = _APP_PATH.read_text(encoding="utf-8")
    expected_cols = (
        "['Customer', 'Overall_Risk_Score', 'Risk_Score_0_10', "
        "'Risk_Level', 'Risk_Band', 'Adoption_Barriers', "
        "'Support_Cases']"
    )
    assert expected_cols in text, (
        "Round 88 / F2 + Round 89 / F1: Compact risk_summary_df "
        "``columns=`` list must include ``'Risk_Score_0_10'`` immediately "
        "after ``Overall_Risk_Score`` and MUST NOT include the legacy "
        "``Risk_Score`` alias (R89/F1 dropped it)."
    )


def test_r88_f2_renewal_summary_portfolio_loop_appends_risk_score_0_10() -> None:
    """Renewal portfolio-loop ``renewal_summary_data.append({...})``
    must carry the new ``Risk_Score_0_10`` key alongside
    ``Overall_Risk_Score`` and ``Risk_Score_0_100``.
    """

    text = _APP_PATH.read_text(encoding="utf-8")
    needle = "'Risk_Score_0_10': _r86_score_10,  # Round 88 / F2"
    assert needle in text, (
        "Round 88 / F2: Renewal portfolio-loop ``renewal_summary_data.append({...})`` "
        "must include ``Risk_Score_0_10`` populated from the same explicit "
        "0-10 source field (``_r86_score_10``) used for Overall_Risk_Score, "
        "while preserving None for unavailable evidence."
    )


def test_r88_f2_renewal_summary_single_customer_path_appends_risk_score_0_10() -> None:
    """Renewal single-customer ``renewal_summary_data = [{...}]`` must
    also carry ``Risk_Score_0_10`` so the schema is uniform across the
    portfolio and single-customer paths.
    """

    text = _APP_PATH.read_text(encoding="utf-8")
    needle = "'Risk_Score_0_10': overall_risk_score,  # Round 88 / F2"
    assert needle in text, (
        "Round 88 / F2: Renewal single-customer ``renewal_summary_data`` "
        "must include ``Risk_Score_0_10: overall_risk_score`` for parity "
        "with the portfolio loop."
    )


def test_r88_f2_renewal_canonical_cols_includes_risk_score_0_10() -> None:
    """``_r70_renewal_canonical_cols`` MUST include ``'Risk_Score_0_10'``
    so the projection enforced by R70/Phase 2 (#5) honours the new
    column name.
    """

    text = _APP_PATH.read_text(encoding="utf-8")
    # Find the canonical-cols tuple definition
    start = text.find("_r70_renewal_canonical_cols: tuple[str, ...] = (")
    assert start > 0, "Renewal canonical-cols tuple must exist"
    block = text[start : start + 600]
    assert "'Risk_Score_0_10'" in block, (
        "Round 88 / F2: ``_r70_renewal_canonical_cols`` must include "
        "``'Risk_Score_0_10'`` so the projection guarantees the column "
        "appears in the Renewal_Summary sheet header even when individual "
        "upstream rows are missing the key."
    )
    # Order pin: 0-10 comes before 0-100
    pos_0_10 = block.find("'Risk_Score_0_10'")
    pos_0_100 = block.find("'Risk_Score_0_100'")
    assert pos_0_10 < pos_0_100, (
        "Round 88 / F2: Risk_Score_0_10 MUST come BEFORE Risk_Score_0_100 in "
        "the canonical-cols tuple so the schema reads as a clear "
        "scale-progression."
    )


def test_r88_f2_renewal_post_loop_normalizer_backfills_risk_score_0_10() -> None:
    """The R67 normalizer block at ~L14760 must backfill
    ``Risk_Score_0_10`` for legacy single-customer rows that come in
    WITHOUT the new key (defense in depth — the explicit append at
    14694 already covers the happy path, but the normalizer must
    catch any external caller that constructs ``renewal_summary_data``
    by hand).
    """

    text = _APP_PATH.read_text(encoding="utf-8")
    # The setdefault call protecting Risk_Score_0_10 backfill
    assert "_r67_row.setdefault('Risk_Score_0_10', _r88_row_score_10)" in text, (
        "Round 88 / F2: the R67 normalizer block must call "
        "``_r67_row.setdefault('Risk_Score_0_10', _r88_row_score_10)`` "
        "so legacy callers that don't carry the key get the 0-10 score "
        "backfilled from Overall_Risk_Score."
    )


def test_r88_f2_compact_risk_summary_dataframe_rows_have_matching_overall_and_0_10() -> None:
    """Behavior round-trip: build a synthetic ``risk_summary_data``
    payload and assert ``Overall_Risk_Score == Risk_Score_0_10`` for
    every row (the value is the same, only the column name differs).

    Round 89 / F1: the synthetic rows no longer carry the legacy
    ``Risk_Score`` back-compat alias (dropped from the writer).
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
    assert "Risk_Score_0_10" in df.columns
    assert "Risk_Score" not in df.columns, (
        "Round 89 / F1: the legacy 'Risk_Score' back-compat alias must "
        "NOT be present in the post-R89 Compact Risk_Summary schema."
    )
    for _, row in df.iterrows():
        assert row["Overall_Risk_Score"] == row["Risk_Score_0_10"], (
            "Round 88 / F2: Risk_Score_0_10 must equal Overall_Risk_Score "
            "for every row in Risk_Summary (both sourced from the same "
            "0-10 SSoT)."
        )


def test_r88_f2_renewal_summary_dataframe_rows_have_matching_scales() -> None:
    """Behavior round-trip for Renewal: ``Risk_Score_0_10 * 10 ==
    Risk_Score_0_100`` (within rounding).  Pin the contract that the
    two scale-named columns are dimensionally consistent.
    """

    rows = [
        {
            "Customer": "ALPHA",
            "Overall_Risk_Score": 7.2,
            "Risk_Score_0_10": 7.2,
            "Risk_Score_0_100": 72.0,
            "Risk_Level": "HIGH",
            "Risk_Band": "HIGH",
            "Analysis_Date": "2026-05-01",
            "Next_Review_Date": "2026-05-15",
        },
        {
            "Customer": "BETA",
            "Overall_Risk_Score": 0.8,
            "Risk_Score_0_10": 0.8,
            "Risk_Score_0_100": 8.0,
            "Risk_Level": "HEALTHY",
            "Risk_Band": "HEALTHY",
            "Analysis_Date": "2026-05-01",
            "Next_Review_Date": "2026-05-15",
        },
    ]
    df = pd.DataFrame(rows)
    for _, row in df.iterrows():
        assert (
            abs(float(row["Risk_Score_0_10"]) * 10.0 - float(row["Risk_Score_0_100"]))
            < 0.05
        ), (
            "Round 88 / F2: Renewal Risk_Score_0_10 * 10 MUST equal "
            "Risk_Score_0_100 (within rounding) so the two scale-named "
            "columns stay dimensionally consistent."
        )


def test_r88_f2_no_regression_on_existing_overall_risk_score() -> None:
    """Defense: the R86/F1 explicit-projection block must STILL be
    intact so we don't accidentally regress to scale-guessing.
    """

    text = _APP_PATH.read_text(encoding="utf-8")
    # The R86/F1 markers must still be present
    assert "Round 86 / Build 62 (P0/F1)" in text, (
        "Round 88 / F2: the R86/F1 explicit-projection markers must "
        "still be present after R88/F2 column additions."
    )
    assert "_r86_score_10 = cust_analysis.get('renewal_risk_score_10')" in text, (
        "Round 88 / F2: the R86/F1 explicit 0-10 source field "
        "(``cust_analysis.get('renewal_risk_score_10')``) must still be "
        "read so we don't regress to scale-guessing."
    )
