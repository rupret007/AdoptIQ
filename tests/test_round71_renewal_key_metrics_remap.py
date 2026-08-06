"""Round 71 / Phase 0 (#2) -- Renewal Key_Metrics.Risk_Category remap.

Pre-R71 the single-customer Renewal XLSX path's ``Key_Metrics.Risk_Category``
cell carried a raw band string from upstream scoring -- which meant a
customer scored as ``MEDIUM`` would land that exact word in the
operator-visible Excel cell, contradicting the user-facing remap that
the multi-customer ``Renewal_Summary`` sheet already applied.

Round 71 / Phase 0 (#2) plumbs the same ``MEDIUM`` -> ``MODERATE`` remap
into the single-customer path.  These tests pin the source-shape so a
future refactor cannot silently revert.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8", errors="replace")


def test_round71_renewal_key_metrics_label_remap_dict_defined() -> None:
    """The ``_r71_key_metrics_label_remap`` dict MUST be defined inside
    the renewal Key_Metrics writer so MEDIUM gets remapped to MODERATE
    on the operator-visible cell."""
    src = _read_app_simple()
    assert_in_source(src, "_r71_key_metrics_label_remap", label='src')


def test_round71_renewal_key_metrics_remap_covers_three_casings() -> None:
    """The remap dict MUST cover at least the three casings the upstream
    pipeline can return: ``MEDIUM``, ``Medium``, and ``medium``.  Pre-R71
    one of these casings always slipped through unmapped."""
    src = _read_app_simple()
    # Find the assignment line and slurp a window around it for the dict body.
    lower = src.lower()
    idx = lower.find("_r71_key_metrics_label_remap")
    assert idx >= 0
    window = src[idx : idx + 400]
    for casing in ("MEDIUM", "Medium", "medium"):
        assert_in_source(window, casing, label="window")


def test_round71_renewal_key_metrics_overwrites_existing_risk_category() -> None:
    """The patch MUST also remap any pre-existing ``Risk_Category`` value
    that another upstream branch may have populated -- not just the
    fresh-write branch.  Pre-R71 the override branch was a silent gap."""
    src = _read_app_simple()
    assert_in_source(src, "_r71_existing_cat", label='src')
