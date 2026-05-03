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
    """R67/B1: the Renewal_Summary writer MUST normalise the score to
    a 0-10 scale via ``round(_r67_orig_f / 10.0, 2)`` for any value
    that arrives on the 0-100 axis."""
    src = _read_app_simple()
    assert "Round 67 / Build 41 (B1)" in src, "R67/B1 marker MUST be present"
    pattern = re.compile(
        r"_r67_row\['Overall_Risk_Score'\]\s*=\s*round\(_r67_orig_f\s*/\s*10\.0\s*,\s*2\)"
    )
    assert pattern.search(src), (
        "R67/B1: Renewal_Summary Overall_Risk_Score MUST be normalised "
        "via `round(_r67_orig_f / 10.0, 2)` for 0-100 scaled inputs"
    )


def test_renewal_xlsx_preserves_risk_score_0_100_back_compat_column() -> None:
    """R67/B1: ``Risk_Score_0_100`` MUST be set alongside
    ``Overall_Risk_Score`` so existing tile / dashboard consumers
    that pinned to the 0-100 axis don't break."""
    src = _read_app_simple()
    pattern = re.compile(
        r"_r67_row\['Risk_Score_0_100'\]\s*=\s*round\(_r67_orig_f\s*,\s*1\)"
    )
    assert pattern.search(src), (
        "R67/B1: Renewal_Summary MUST persist Risk_Score_0_100 = round(_r67_orig_f, 1) "
        "for 0-100 scaled inputs (back-compat)"
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
    # The dashboard row uses the format f'{_r67_score_10:.1f}/10  ({risk_score:.1f}/100)'.
    pattern = re.compile(
        r"f'\{_r67_score_10:\.1f\}/10\s+\(\{risk_score:\.1f\}/100\)'"
    )
    assert pattern.search(src), (
        "Round 71 / Phase 4 (#23): dashboard Overall Risk Score format "
        "MUST be '{0-10:.1f}/10  ({0-100:.1f}/100)' so both scales are "
        "visible AND the rounding precision matches the SSoT in "
        "risk_scoring."
    )


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
