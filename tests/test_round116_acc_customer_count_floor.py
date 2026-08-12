"""Round 116 / Build 85 (B) — Comprehensive "All Contact Center" customer-count floor.

Background
----------
R93 strict "All Contact Center" (ACC) scoping drops AB rows for customers
whose AB rows carry no contact-center technology evidence
(``adoptiq_backend._apply_scope_filter_ab``).  Those customers can then
disappear from the narrow ``AB ∪ CSOne ∪ Pulse`` headline universe even
though they ARE in the manager's Contact-Center subscription roster.  The
Brian Frazier / All Contact Center / 90d run surfaced ``Customers in
portfolio: 24`` where the team carries ~37-49 CC customers.

Fix
---
For the ACC comprehensive path ONLY, anchor the headline customer-count
universe on the team Contact-Center subscription set ∪ scoped AB ∪ CSOne ∪
Pulse so a CC-subscription customer whose AB rows were dropped by R93 is
still counted.  The widening is threaded coherently through the FOUR
parity sites so Word, the band buckets, the consistency validator, and the
Excel ``Summary`` sheet all agree:

1. ``app_simple._r47_comp_total_narrow`` (Word headline / ``portfolio_metrics``)
2. ``app_simple._r64_narrow_customer_list`` (title-page risk-band buckets)
3. ``report_consistency.validate_report_consistency`` (strict parity gate)
4. ``report_export_styling.build_summary_rows`` (Excel Summary sheet, via
   ``write_excel_workbook`` -> ``write_summary_sheet``)

All four take an additive ``subscriptions_df`` / ``subs_df`` argument that
defaults to ``None`` (byte-identical for every non-ACC caller).  The
AB_Detail_All sheet itself stays strictly scoped — only the headline
universe widens.

The exact live floor (~37-49) needs a VPN run to confirm; these synthetic
tests pin the over-exclusion *behaviour* regardless.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import re
from pathlib import Path

import pandas as pd

import canonical_metrics as cm
import report_export_styling as styling
from report_consistency import validate_report_consistency

_APP_SIMPLE = Path(__file__).resolve().parent.parent / "app_simple.py"


def _ab_frame_strict_acc() -> pd.DataFrame:
    """Strictly-scoped AB: only customers with explicit CC tech evidence.

    Mirrors the post-R93 ``_apply_scope_filter_ab`` output for ACC — a
    customer whose AB rows had unknown tech has already been dropped, so
    they are simply absent here.
    """
    return pd.DataFrame(
        {
            "ID": ["AB-1", "AB-2"],
            "BU_NAME": ["Alpha Corp", "Beta LLC"],
            "Technology": ["Contact Center", "Contact Center"],
        }
    )


def _csone_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Case Number": ["C-1"],
            "BU_NAME": ["Alpha Corp"],
        }
    )


def _pulse_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "BU_NAME": ["Beta LLC"],
            "PULSE_SCORE": [5.0],
        }
    )


def _team_cc_subs_frame() -> pd.DataFrame:
    """Team Contact-Center subscription roster.

    ``Gamma Industries`` is a CC-subscription customer whose AB rows were
    all unknown-tech (dropped by R93) and who has no CSOne / Pulse
    activity — the exact customer that vanished from the pre-R116 count.
    """
    return pd.DataFrame(
        {
            "BU_NAME": ["Alpha Corp", "Beta LLC", "Gamma Industries"],
            "ACCOUNT_ID_C": ["A1", "A2", "A3"],
            "SUBSCRIPTION_ID": ["S1", "S2", "S3"],
        }
    )


# ---------------------------------------------------------------------------
# 1. Over-exclusion guard at the canonical_metrics layer
# ---------------------------------------------------------------------------


def test_count_customers_without_subs_drops_subscription_only_customer():
    """Reproduce the regression: the narrow (ab, csone, pulse) universe
    misses a CC-subscription customer whose AB rows were dropped by R93."""
    narrow = cm.count_customers(
        ab_df=_ab_frame_strict_acc(),
        csone_df=_csone_frame(),
        pulse_df=_pulse_frame(),
    )
    assert narrow == 2  # Alpha + Beta; Gamma is missing -> the bug


def test_count_customers_with_subs_includes_subscription_only_customer():
    """The R116 fix: threading the CC subscription frame restores Gamma."""
    widened = cm.count_customers(
        ab_df=_ab_frame_strict_acc(),
        csone_df=_csone_frame(),
        pulse_df=_pulse_frame(),
        subs_df=_team_cc_subs_frame(),
    )
    assert widened == 3  # Alpha + Beta + Gamma


def test_list_customers_with_subs_includes_subscription_only_customer():
    names = cm.list_customers(
        ab_df=_ab_frame_strict_acc(),
        csone_df=_csone_frame(),
        pulse_df=_pulse_frame(),
        subs_df=_team_cc_subs_frame(),
    )
    folded = {n.lower() for n in names}
    assert any("gamma" in n for n in folded), names


# ---------------------------------------------------------------------------
# 2. Excel Summary parity (build_summary_rows + subscriptions_df)
# ---------------------------------------------------------------------------


def _customers_value(rows):
    for label, value in rows:
        if label == "Customers in portfolio":
            return value
    raise AssertionError("Customers in portfolio row not found")


def test_build_summary_rows_default_is_narrow():
    """Default (no subscriptions_df) preserves the pre-R116 narrow count."""
    rows = styling.build_summary_rows(
        {
            "AB_Detail_All": _ab_frame_strict_acc(),
            "CSOne_Detail_All": _csone_frame(),
        },
        {"customer_pulse": _pulse_frame()},
    )
    assert _customers_value(rows) == "2"


def test_build_summary_rows_with_subscriptions_widens_for_acc():
    rows = styling.build_summary_rows(
        {
            "AB_Detail_All": _ab_frame_strict_acc(),
            "CSOne_Detail_All": _csone_frame(),
        },
        {"customer_pulse": _pulse_frame()},
        subscriptions_df=_team_cc_subs_frame(),
    )
    assert _customers_value(rows) == "3"


# ---------------------------------------------------------------------------
# 3. Consistency validator parity (Word headline == validator narrow count)
# ---------------------------------------------------------------------------


def test_validator_default_narrow_count_matches_pre_r116():
    result = validate_report_consistency(
        _ab_frame_strict_acc(),
        _csone_frame(),
        customer_pulse_df=_pulse_frame(),
    )
    assert result["metrics"]["total_customers"] == 2


def test_validator_with_subscriptions_widens_and_word_parity_holds():
    """With subscriptions_df threaded, the validator narrow count widens
    to match a Word headline computed the same way — so the strict
    parity gate does NOT fire for the ACC widening."""
    subs = _team_cc_subs_frame()
    word_headline = cm.count_customers(
        ab_df=_ab_frame_strict_acc(),
        csone_df=_csone_frame(),
        pulse_df=_pulse_frame(),
        subs_df=subs,
    )
    result = validate_report_consistency(
        _ab_frame_strict_acc(),
        _csone_frame(),
        portfolio_metrics={
            "total_customers": word_headline,
            "total_barriers": cm.count_total_barriers(_ab_frame_strict_acc()),
            "total_cases": cm.count_total_tac(_csone_frame()),
        },
        customer_pulse_df=_pulse_frame(),
        subscriptions_df=subs,
        # strict_mode=False: the total_customers parity check still
        # populates ``errors`` (strict_mode only controls raising), and
        # this test is solely about that one check — not the unrelated
        # grounding / source-attribution guards strict_mode also enforces.
        strict_mode=False,
    )
    assert result["metrics"]["total_customers"] == word_headline == 3
    # No total_customers mismatch in the strict gate.
    assert not any(
        "total_customers" in e for e in result.get("errors", [])
    ), result.get("errors")


# ---------------------------------------------------------------------------
# 4. AB sheet stays strictly scoped (R93 contract preserved)
# ---------------------------------------------------------------------------


def test_widening_does_not_mutate_ab_sheet():
    """The headline universe widens, but the AB frame the report displays
    must NOT gain the subscription-only customer — R93 strictness holds."""
    ab = _ab_frame_strict_acc()
    before_ids = set(ab["ID"])
    _ = cm.count_customers(
        ab_df=ab,
        csone_df=_csone_frame(),
        pulse_df=_pulse_frame(),
        subs_df=_team_cc_subs_frame(),
    )
    assert set(ab["ID"]) == before_ids
    assert "Gamma Industries" not in set(ab["BU_NAME"])


# ---------------------------------------------------------------------------
# 5. Source-shape: app_simple threads the ACC-gated subs frame
# ---------------------------------------------------------------------------


def _app_simple_src() -> str:
    return _APP_SIMPLE.read_text(encoding="utf-8")


def test_app_simple_defines_acc_gate():
    src = _app_simple_src()
    assert_in_source(src, "_r116_acc_count = status.get('tech') == 'All Contact Center'", label='src')
    assert_in_source(src, "_r116_acc_subs_df", label='src')


def test_app_simple_threads_subs_into_all_four_parity_sites():
    src = _app_simple_src()
    # Every scope now uses the authoritative, technology-scoped subscription
    # roster and the consistency gate opts into the joined-source universe.
    assert_in_source(src, "_r116_acc_subs_df = team_subs_for_customer_counting", label='src')
    assert_in_source(src, "subscriptions_df=team_subs_for_customer_counting", label='src')
    assert_in_source(src, "include_all_customer_sources=True", label='src')
    assert_in_source(src, "xlsx_path = _r142_write_source_workbook", label='src')


def test_acc_gate_is_scoped_to_all_contact_center_only():
    """Named-tech runs use scoped subscriptions instead of dropping them."""
    src = _app_simple_src()
    assert_in_source(src, "_r116_acc_count = status.get('tech') == 'All Contact Center'", label='src')
    assert_in_source(src, "_r116_acc_subs_df = team_subs_for_customer_counting", label='src')
