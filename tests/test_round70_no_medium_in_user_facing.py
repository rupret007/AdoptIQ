"""Round 70 / Phase 5 (#14) -- vocabulary lint: no MEDIUM in user-facing
surfaces.

Build 43 acceptance audit found ``MEDIUM`` leaking into user-facing
surfaces in 3 reports:

- Compact Risk_Level: 6 customers labeled MEDIUM (should be MODERATE).
- Renewal Risk_Level: 8 customers labeled MEDIUM.
- Renewal Word doc: 8 cells in Table 1 (Customer Health / Top-N) carry
  MEDIUM. (1 of 9 hits is in the deliberate ``Risk Score Methodology
  (0-100 scale)`` paragraph; that single instance is fine.)

R67/B1 + R67/B6 contract:
    - The user-facing vocabulary on the ``Risk_Level`` column is
      MODERATE (the operator reads this column first).
    - The canonical band key on the ``Risk_Band`` column stays
      CRITICAL / HIGH / MEDIUM / LOW / HEALTHY for cross-sheet parity
      (Comprehensive ``Risk_Components.risk_band`` agrees byte-for-byte)
      and so internal filter / color lookups still resolve correctly.

Round 71 / Phase 0 (#1): REVERTED the Round 70 over-reach that also
remapped ``Risk_Band`` -- the user-facing vocabulary contract is
satisfied by ``Risk_Level`` alone.

The Round 70 fix adds the ``{MEDIUM -> MODERATE}`` remap at every
user-facing render site in the renewal Word builder + the matplotlib
gauge labels. This test pins the source-shape so a future edit can't
silently drop the remap.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

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
    assert_in_source(src, "_r70_focus_LABEL_REMAP", label='src')
    assert_in_source(src, "_r70_focus_LABEL_REMAP.get(_r70_cat, _r70_cat)", label='src')


def test_risk_score_box_remaps_medium_to_moderate() -> None:
    """R70/Phase 3 (#11): the Risk Score box in the renewal Word
    narrative MUST remap ``risk_category`` before printing."""
    src = _read_app_simple()
    assert_in_source(src, "_r70_rsbox_LABEL_REMAP", label='src')
    assert_in_source(src, "_r70_rsbox_label", label='src')


def test_donut_gauge_remaps_medium_to_moderate() -> None:
    """R70/Phase 3 (#11): the matplotlib donut gauge text MUST remap
    ``risk_category`` before rendering the PNG that ends up in the
    Word doc."""
    src = _read_app_simple()
    assert_in_source(src, "_r70_gauge_LABEL_REMAP", label='src')
    assert_in_source(src, "_r70_gauge_label", label='src')


def test_panel2_2x2_chart_remaps_medium_to_moderate() -> None:
    """R70/Phase 3 (#11): the panel-2 of the 2x2 portfolio chart MUST
    remap ``risk_cat`` before painting the wedge label."""
    src = _read_app_simple()
    assert_in_source(src, "_r70_panel2_LABEL_REMAP", label='src')
    assert_in_source(src, "_r70_panel2_label", label='src')


def test_compact_risk_summary_level_user_facing_remap_applied() -> None:
    """R67/B1 + R71/Phase 0 (#1): the Compact ``Risk_Level`` column
    MUST be remapped at the row-build site so the artifact carries
    MODERATE.  The ``Risk_Band`` column is intentionally NOT remapped
    here (R67/B6 contract: canonical band key for cross-sheet parity)."""
    src = _read_app_simple()
    assert_in_source(src, "_r67_b6_risk_level = _r67_b6_LABEL_REMAP.get(risk_level, risk_level)", label='src')
    assert_in_source(src, "'Risk_Level': _r67_b6_risk_level,", label='src')


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


# ---------------------------------------------------------------------------
# Round 71 / Phase 6 (#30): extended structural lints to catch the
# shapes the Round 70 lint missed -- f-string interpolations and
# ``str.format()`` insertions that put a literal ``MEDIUM`` straight
# into a user-facing Word/Excel paragraph or row.
#
# These lints are intentionally narrow: they look for the specific
# patterns where a literal ``"MEDIUM"`` (case-sensitive, in single OR
# double quotes) appears inside an f-string that ends up rendered.
# False positives in this app would be: code paths where ``MEDIUM``
# is used as a dict key (e.g. ``COLOR_LOOKUP['MEDIUM']``), as a
# regex pattern (e.g. ``re.search(r'MEDIUM', ...)``), as a comment,
# or as a docstring.  These are excluded by the refined regex below.
# ---------------------------------------------------------------------------


def test_no_medium_inside_user_facing_fstring() -> None:
    """R71/Phase 6 (#30): catch ``f"... {something_or_literal} MEDIUM ..."``
    style hardcoded literals inside f-strings that end up in
    add_run / add_paragraph / add_heading calls.

    Pre-R71 the Round 70 lint only checked dict-literal shapes
    (``'Risk_Level': 'MEDIUM',``) and would not have caught a
    formatter that did ``add_run(f"Risk: MEDIUM customer: {name}")``.
    Now scan for the literal ``MEDIUM`` token anywhere inside a
    quoted string passed to add_run / add_paragraph.
    """
    src = _read_app_simple()
    # Flexible match: f-string OR plain string passed to add_run / add_paragraph
    # that contains a bare ``MEDIUM`` token.  Tighten the boundary
    # check so we don't match ``MEDIUM`` inside a longer word
    # (``MEDIUMSIZE`` should not match) and ignore comment lines.
    bad: list[tuple[int, str]] = []
    for lineno, line in enumerate(src.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Match: add_run("...MEDIUM...") OR add_run(f"...MEDIUM...")
        # AND the MEDIUM token is bounded by non-word chars or quotes.
        if re.search(
            r"add_(?:run|paragraph|heading)\([^)]*[\"\']\b.*?\bMEDIUM\b.*?[\"\'][^)]*\)",
            line,
        ):
            bad.append((lineno, line.strip()[:120]))
    assert not bad, (
        f"Round 71 / Phase 6 (#30): found {len(bad)} add_run / "
        "add_paragraph / add_heading site(s) with a hard-coded "
        f"MEDIUM literal in the rendered text:\n"
        + "\n".join(f"  L{n}: {ln}" for n, ln in bad[:10])
        + "\nUse a {MEDIUM -> MODERATE} remap helper before rendering."
    )


def test_no_medium_inside_format_call() -> None:
    """R71/Phase 6 (#30): catch ``"...{}...".format(..., 'MEDIUM', ...)``
    style hardcoded literals.

    The Round 70 lint missed any ``str.format()`` call that
    interpolates a hardcoded ``MEDIUM`` token; the f-string lint
    above missed the equivalent pattern.  Together both lints close
    the user-facing-vocabulary gap.
    """
    src = _read_app_simple()
    bad: list[tuple[int, str]] = []
    for lineno, line in enumerate(src.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Match: ``.format(..., 'MEDIUM', ...)`` or ``.format('MEDIUM')``
        # but NOT dict-key access like ``COLOR_LOOKUP['MEDIUM']``.
        if re.search(r"\.format\([^)]*[\"\']MEDIUM[\"\'][^)]*\)", line):
            bad.append((lineno, line.strip()[:120]))
    assert not bad, (
        f"Round 71 / Phase 6 (#30): found {len(bad)} .format(...) "
        f"site(s) with a hard-coded 'MEDIUM' interpolation:\n"
        + "\n".join(f"  L{n}: {ln}" for n, ln in bad[:10])
        + "\nUse a {MEDIUM -> MODERATE} remap helper before rendering."
    )


def test_round71_lint_helpers_present_for_medium_remap() -> None:
    """R71/Phase 6 (#30): the canonical remap helper symbols MUST be
    present in app_simple.py.  Pins the source-shape so a future
    refactor can't accidentally remove the remap and break the
    user-facing vocabulary contract for Compact / Renewal Excel.
    """
    src = _read_app_simple()
    # Round 67 / B1 / B6 + Round 71 / Phase 0 (#1) symbols.
    required_helpers = ["_r67_b6_LABEL_REMAP", "_r71_key_metrics_label_remap"]
    for symbol in required_helpers:
        assert_in_source(src, symbol, label='src')
