"""Round 70 / Phase 5 (#14) -- vocabulary lint: no MEDIUM in user-facing
surfaces.

Build 43 acceptance audit found ``MEDIUM`` leaking into user-facing
surfaces in 3 reports:

- Compact Risk_Band: 6 customers labeled MEDIUM (should be MODERATE).
- Renewal Risk_Level: 8 customers labeled MEDIUM.
- Renewal Word doc: 8 cells in Table 1 (Customer Health / Top-N) carry
  MEDIUM. (1 of 9 hits is in the deliberate ``Risk Score Methodology
  (0-100 scale)`` paragraph; that single instance is fine.)

R67/B1 + R67/B6 contract: the user-facing vocabulary is MODERATE; the
canonical band key (CRITICAL/HIGH/MEDIUM/LOW/HEALTHY) may stay in
internal lookup tables but never surfaces to the user.

The Round 70 fix adds the ``{MEDIUM -> MODERATE}`` remap at every
user-facing render site in the renewal Word builder + the matplotlib
gauge labels. This test pins the source-shape so a future edit can't
silently drop the remap.
"""
from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-shape pins on the user-facing render sites
# ---------------------------------------------------------------------------


def test_top10_focus_table_remaps_medium_to_moderate() -> None:
    """R70/Phase 3 (#11): the Top-10 Focus Accounts table cell paint
    MUST go through ``_r70_focus_LABEL_REMAP``."""
    src = _read_app_simple()
    assert "_r70_focus_LABEL_REMAP" in src, (
        "Round 70 / #11: Top-10 Focus Accounts table MUST define a "
        "MEDIUM->MODERATE remap before painting cell text."
    )
    assert "_r70_focus_LABEL_REMAP.get(_r70_cat, _r70_cat)" in src, (
        "Round 70 / #11: cell paint MUST consume the remapped label."
    )


def test_risk_score_box_remaps_medium_to_moderate() -> None:
    """R70/Phase 3 (#11): the Risk Score box in the renewal Word
    narrative MUST remap ``risk_category`` before printing."""
    src = _read_app_simple()
    assert "_r70_rsbox_LABEL_REMAP" in src, (
        "Round 70 / #11: Risk Score box MUST define the user-facing "
        "MEDIUM->MODERATE remap."
    )
    assert "_r70_rsbox_label" in src, (
        "Round 70 / #11: the score_run text MUST consume the remapped "
        "label, not the raw ``risk_category``."
    )


def test_donut_gauge_remaps_medium_to_moderate() -> None:
    """R70/Phase 3 (#11): the matplotlib donut gauge text MUST remap
    ``risk_category`` before rendering the PNG that ends up in the
    Word doc."""
    src = _read_app_simple()
    assert "_r70_gauge_LABEL_REMAP" in src, (
        "Round 70 / #11: donut gauge MUST define the MEDIUM->MODERATE "
        "remap so the embedded PNG carries the user-facing vocabulary."
    )
    assert "_r70_gauge_label" in src, (
        "Round 70 / #11: ax.text MUST consume the remapped label, not "
        "the raw band key."
    )


def test_panel2_2x2_chart_remaps_medium_to_moderate() -> None:
    """R70/Phase 3 (#11): the panel-2 of the 2x2 portfolio chart MUST
    remap ``risk_cat`` before painting the wedge label."""
    src = _read_app_simple()
    assert "_r70_panel2_LABEL_REMAP" in src, (
        "Round 70 / #11: panel 2 wedge label MUST go through the "
        "MEDIUM->MODERATE remap."
    )
    assert "_r70_panel2_label" in src, (
        "Round 70 / #11: ax2.pie labels= MUST consume the remapped label."
    )


def test_compact_risk_summary_band_user_facing_remap_applied() -> None:
    """R70/Phase 3 (#11): the Compact ``Risk_Band`` column MUST be
    remapped at the row-build site so the artifact carries MODERATE."""
    src = _read_app_simple()
    assert "_r70_risk_band_user" in src, (
        "Round 70 / #11: Compact MUST remap Risk_Band through the "
        "MEDIUM->MODERATE label table at the row-build site."
    )


# ---------------------------------------------------------------------------
# Lint: any string-literal ``MEDIUM`` in app_simple.py that lands in a
# user-facing column / cell paint MUST be mapped through a remap.  This
# is a structural lint; it doesn't catch every leak but does catch the
# specific shape of "row dict literal carrying 'MEDIUM' as a value".
# ---------------------------------------------------------------------------


def test_no_unmapped_risk_level_medium_literal_in_row_dict() -> None:
    """R70/Phase 3 (#11): there MUST be no row dict literal of the
    shape ``'Risk_Level': 'MEDIUM'`` -- any user-facing Risk_Level
    value must come from the remap helper, not a hard-coded string."""
    src = _read_app_simple()
    # Allow ``'Risk_Level': _r67_b6_risk_level,`` (helper output).
    # Forbid: ``'Risk_Level': 'MEDIUM',`` literal.
    bad = re.findall(r"'Risk_Level':\s*'MEDIUM'", src)
    assert not bad, (
        f"Round 70 / #11: found {len(bad)} hard-coded 'Risk_Level': "
        "'MEDIUM' literal(s) -- these MUST go through the MEDIUM-> "
        "MODERATE remap."
    )


def test_no_unmapped_risk_band_medium_literal_outside_color_lookup() -> None:
    """R70/Phase 3 (#11): user-facing rows must not carry
    ``'Risk_Band': 'MEDIUM'`` literally.  The internal color/lookup
    tables (e.g. ``RISK_BAND_COLORS = {'MEDIUM': '#ffd700'}``) are
    fine because they are dict KEYS, not values rendered to the user."""
    src = _read_app_simple()
    # Find: ``'Risk_Band': 'MEDIUM',`` (row dict literal — bad)
    bad = re.findall(r"'Risk_Band':\s*'MEDIUM'", src)
    assert not bad, (
        f"Round 70 / #11: found {len(bad)} hard-coded 'Risk_Band': "
        "'MEDIUM' literal(s) -- these MUST go through the MEDIUM-> "
        "MODERATE remap."
    )
