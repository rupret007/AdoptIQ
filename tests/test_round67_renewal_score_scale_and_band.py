"""Round 67 / Build 41 (B1) -- Renewal score scale and band parity.

Build 40 acceptance saw Renewal publishing scores on a 0-100 scale
with band labels including ``MEDIUM`` while the Compact format used a
0-10 scale with ``MODERATE``. Operators reading both formats for the
same scope saw Compact say ``4.9`` and Renewal say ``50.0`` for the
same customer -- visually divergent even though the underlying
profile was identical.

Round 67 / B1 aligns Renewal to the Compact convention:

- ``Renewal_Summary.Overall_Risk_Score`` is published on a 0-10 scale.
- ``Risk_Score_0_100`` is preserved alongside as a back-compat column.
- ``Risk_Level`` is remapped MEDIUM -> MODERATE (vocab parity with
  the Compact narrative). The canonical band key (CRITICAL / HIGH /
  MEDIUM / LOW / HEALTHY) is preserved as ``Risk_Band`` so existing
  band-based color lookups still match.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_APP_SIMPLE = Path(__file__).resolve().parent.parent / "app_simple.py"


def _read_app_simple() -> str:
    return _APP_SIMPLE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Excel writer: Renewal_Summary publishes Overall_Risk_Score on 0-10 scale
# and preserves Risk_Score_0_100 alongside.
# ---------------------------------------------------------------------------


def test_renewal_xlsx_publishes_score_on_0_10_scale() -> None:
    """R67/B1 contract (post-R86 implementation): the Renewal_Summary
    writer MUST publish ``Overall_Risk_Score`` on the 0-10 scale.

    Round 86 / Build 62 (P0/F1) replaced the buggy R67 numeric
    heuristic (``if _r67_orig_f > 10.0`` -- which mis-classified
    HEALTHY customers' 0-100 scores under 10 as 0-10 inputs and
    saturated the back-compat column) with explicit projection from
    ``cust_analysis['renewal_risk_score_10']`` (canonical 0-10) and
    ``cust_analysis['renewal_risk_score']`` (canonical 0-100). The
    R67/B1 contract still holds: the published 0-10 score MUST equal
    the engine's ``risk_score_0_10`` field.
    """
    src = _read_app_simple()
    assert "Round 67 / Build 41 (B1)" in src, "R67/B1 marker MUST be present"
    # Post-R86: the projection reads ``renewal_risk_score_10`` as the
    # primary 0-10 source. This is the new SSoT contract.
    assert "renewal_risk_score_10" in src, (
        "R67/B1 (post-R86): the Renewal portfolio loop MUST read "
        "``renewal_risk_score_10`` (canonical 0-10 from engine) "
        "rather than guessing the scale from the numeric value."
    )
    # Round 86 / Build 62 marker.
    assert "Round 86 / Build 62 (P0/F1)" in src, (
        "R86/F1: marker MUST be present so future rounds know the "
        "post-R67 projection contract."
    )


def test_renewal_xlsx_preserves_risk_score_0_100_back_compat_column() -> None:
    """R67/B1 contract (post-R86 implementation): ``Risk_Score_0_100``
    MUST be persisted on every Renewal_Summary row so existing
    tile / dashboard consumers that pinned to the 0-100 axis don't
    break.

    Post-R86 the column is populated from
    ``cust_analysis['renewal_risk_score']`` (canonical 0-100 from
    engine, no numeric heuristic).
    """
    src = _read_app_simple()
    # The column is referenced (a) in the portfolio loop, (b) in the
    # post-loop normalizer for single-customer fallback, (c) in the
    # R70 canonical_cols projection. All three MUST stay.
    assert "'Risk_Score_0_100'" in src, (
        "R67/B1: Risk_Score_0_100 column MUST be present in the "
        "Renewal_Summary writer."
    )
    # Post-R86: the loop reads ``renewal_risk_score`` directly as the
    # 0-100 source -- no scale guessing.
    assert "_r86_score_100 = cust_analysis.get('renewal_risk_score')" in src, (
        "R67/B1 (post-R86): Risk_Score_0_100 MUST come from the "
        "explicit ``renewal_risk_score`` field on cust_analysis "
        "(NOT from a numeric multiplication of Overall_Risk_Score)."
    )


def test_renewal_xlsx_label_remap_medium_to_moderate() -> None:
    """R67/B1: ``Risk_Level`` MUST flip 'MEDIUM' -> 'MODERATE' so the
    Renewal label matches Compact for the same band."""
    src = _read_app_simple()
    pattern = re.compile(
        r"_r67_RISK_LEVEL_REMAP\s*=\s*\{[^}]*'MEDIUM'\s*:\s*'MODERATE'",
        re.DOTALL,
    )
    assert pattern.search(src), (
        "R67/B1: _r67_RISK_LEVEL_REMAP MUST include 'MEDIUM' -> 'MODERATE'"
    )


def test_renewal_xlsx_preserves_risk_band_for_band_filters() -> None:
    """R67/B1: ``Risk_Band`` MUST carry the canonical band key
    (CRITICAL / HIGH / MEDIUM / LOW / HEALTHY) so existing band-based
    color lookups continue to work even after the user-facing
    ``Risk_Level`` label flips MEDIUM -> MODERATE."""
    src = _read_app_simple()
    assert "_r67_row['Risk_Band']" in src, (
        "R67/B1: Risk_Band MUST be set on every renewal_summary row "
        "so existing band-based filters keep working"
    )


# ---------------------------------------------------------------------------
# Word narrative: same 0-10 scale + MODERATE label
# ---------------------------------------------------------------------------


def test_renewal_word_publishes_0_10_scale_with_back_compat() -> None:
    """R67/B1: the renewal Word narrative (portfolio + single-customer
    paths) MUST publish ``X.Y/10`` and include the 0-100 score in
    parentheses for back-compat.

    Round 71 / Phase 4 (#23): the format string was changed from
    ``:.2f`` to ``:.1f`` to align with the SSoT in ``risk_scoring``
    (which rounds 0-10 scores to 1 decimal).  Pre-R71 the renewal
    surfaces published ``5.40/10`` while every other surface said
    ``5.4/10`` for the SAME customer.
    """
    src = _read_app_simple()
    # Portfolio path -- Round 71: now :.1f, not :.2f.
    assert "_r67_score_10:.1f}/10" in src, (
        "Round 71 / Phase 4 (#23): renewal narrative MUST publish 0-10 "
        "score using '_r67_score_10:.1f}/10' format (was :.2f pre-R71)"
    )
    assert "risk_score:.1f}/100" in src, (
        "R67/B1: renewal narrative MUST keep the 0-100 score in parentheses "
        "for back-compat"
    )


def test_renewal_word_dashboard_uses_moderate_label() -> None:
    """The Customer Health Dashboard's Risk Category cell MUST use
    the MODERATE-remapped label (``_r67_risk_label``) so the table
    column matches the executive summary's wording."""
    src = _read_app_simple()
    assert "('Risk Category', _r67_risk_label)" in src, (
        "R67/B1: dashboard Risk Category MUST read from _r67_risk_label "
        "(the MODERATE-remapped value)"
    )


def test_renewal_word_dashboard_overall_risk_score_uses_0_10_format() -> None:
    """The Customer Health Dashboard's Overall Risk Score row MUST
    show the 0-10 value with the 0-100 in parentheses.

    Round 71 / Phase 4 (#23): the dashboard format string was changed
    from ``:.2f`` to ``:.1f`` to align with the SSoT in
    ``risk_scoring`` (which rounds 0-10 scores to 1 decimal).
    """
    src = _read_app_simple()
    assert "_r67_score_10:.1f}/10" in src
    assert (
        "f'{_r67_score_10:.1f}/10 ({_r67_risk_label}; {risk_score:.1f}/100)'"
        in src
    )
    assert "else 'N/A (UNKNOWN; insufficient evidence)'" in src
    assert "('Overall Risk Score', _risk_score_display)" in src


# ---------------------------------------------------------------------------
# Compact + Renewal vocabulary parity
# ---------------------------------------------------------------------------


def test_compact_risk_summary_remaps_medium_to_moderate() -> None:
    """R67/B1 (vocab parity): the Compact ``Risk_Summary`` writer also
    remaps MEDIUM -> MODERATE so both report formats use the same
    vocabulary for the same band."""
    src = _read_app_simple()
    assert "_r67_b6_LABEL_REMAP" in src, (
        "R67/B1+B6: Compact Risk_Summary MUST define a label remap for MEDIUM -> MODERATE"
    )
    pattern = re.compile(
        r"_r67_b6_LABEL_REMAP\s*=\s*\{[^}]*'MEDIUM'\s*:\s*'MODERATE'",
        re.DOTALL,
    )
    assert pattern.search(src), (
        "R67/B1+B6: Compact Risk_Summary MUST flip MEDIUM -> MODERATE"
    )
