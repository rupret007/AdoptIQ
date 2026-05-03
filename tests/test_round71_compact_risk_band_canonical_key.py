"""Round 71 / Phase 0 (#1) -- Compact Risk_Band restored to canonical key.

Round 70 / Phase 3 (#11) over-reached: it remapped Compact ``Risk_Band``
cells to ``MODERATE`` to address operator-visible ``MEDIUM`` complaints.
But R67/B6's contract is explicit -- ``Risk_Band`` keeps the canonical
band key (``CRITICAL`` / ``HIGH`` / ``MEDIUM`` / ``LOW`` / ``HEALTHY``)
for downstream filters and color lookups; only ``Risk_Level`` carries
the user-facing remap (``MEDIUM`` -> ``MODERATE``).

Round 71 / Phase 0 (#1) reverts the Round 70 over-reach.  These tests
pin the corrected contract: ``Risk_Band`` stays canonical (``MEDIUM``
allowed), ``Risk_Level`` stays user-facing (``MODERATE`` enforced).
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8", errors="replace")


def test_round71_compact_risk_band_uses_canonical_band_not_remapped() -> None:
    """Round 71 / Phase 0 (#1): the Compact Risk_Summary row writer MUST
    populate ``Risk_Band`` from the raw canonical ``band`` value, NOT
    from a ``MODERATE``-remapped variant.  This is the fix that walks
    back the Round 70 / Phase 3 (#11) over-reach.
    """
    src = _read_app_simple()
    # The pre-revert pattern was assigning the remapped value to the
    # Risk_Band cell; the post-revert pattern assigns ``band`` directly.
    assert "'Risk_Band': band," in src or "\"Risk_Band\": band," in src, (
        "Compact Risk_Summary writer no longer assigns the canonical "
        "``band`` value to ``Risk_Band``.  Round 71 / Phase 0 (#1) "
        "requires the cell to carry the canonical band key (MEDIUM "
        "stays MEDIUM there) so downstream filters and color lookups "
        "still match.  Round 70 / Phase 3 (#11)'s remap-into-Risk_Band "
        "was the over-reach being corrected here."
    )


def test_round71_compact_risk_level_carries_user_facing_remap() -> None:
    """Round 71 / Phase 0 (#1): ``Risk_Level`` (the user-facing column)
    MUST carry the ``MEDIUM`` -> ``MODERATE`` remap (via the
    ``_r67_b6_risk_level`` helper) so the analyst-visible vocabulary
    stays consistent with the rest of the suite.
    """
    src = _read_app_simple()
    assert "'Risk_Level': _r67_b6_risk_level," in src or "\"Risk_Level\": _r67_b6_risk_level," in src, (
        "Compact Risk_Summary writer must still pass the remapped "
        "``_r67_b6_risk_level`` value into the user-facing ``Risk_Level`` "
        "column.  Round 71 reverted the remap into ``Risk_Band`` (the "
        "canonical column) but kept the existing remap-on-Risk_Level path."
    )


def test_round71_r67_b6_label_remap_helper_present() -> None:
    """The shared ``_r67_b6_LABEL_REMAP`` dict MUST still be defined --
    it's the canonical mapping used by the user-facing ``Risk_Level``
    surface and the Compact narrative gate."""
    src = _read_app_simple()
    assert "_r67_b6_LABEL_REMAP" in src, (
        "_r67_b6_LABEL_REMAP must remain defined so user-facing surfaces "
        "have a single mapping source for MEDIUM -> MODERATE."
    )


def test_round71_no_compact_risk_band_remap_assignment() -> None:
    """Defense-in-depth: there must be NO line that assigns the remapped
    value back to ``Risk_Band`` in the Compact path.  A future cleanup
    that re-introduces the over-reach must trip this lint."""
    src = _read_app_simple()
    forbidden_patterns = [
        "'Risk_Band': _r70_risk_band_user",
        '"Risk_Band": _r70_risk_band_user',
        "'Risk_Band': _r67_b6_risk_level",
        '"Risk_Band": _r67_b6_risk_level',
        "'Risk_Band': _r67_b6_LABEL_REMAP",
        '"Risk_Band": _r67_b6_LABEL_REMAP',
    ]
    for pat in forbidden_patterns:
        assert pat not in src, (
            f"Compact Risk_Summary writer must NOT assign a remapped value "
            f"back to ``Risk_Band``.  Found forbidden pattern: ``{pat}``.  "
            f"Round 71 / Phase 0 (#1) reverted that exact over-reach."
        )
