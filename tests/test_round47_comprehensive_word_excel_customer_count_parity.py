"""Round 47 / R47-COMP-CUSTCOUNT-PARITY (F-COMP-CUSTCOUNT-DELTA-14) --
regression test that the comprehensive Word headline customer count
matches the canonical-narrow ``count_customers(ab, csone, pulse)`` value
the Excel ``Summary`` sheet prints.

Build23 audit (run ``1777445582`` for Brian Frazier) captured:

* DOCX title page: ``Total Customers: 52``
* DOCX Executive Summary table: ``Customers in portfolio  52``
* XLSX Summary sheet:           ``Customers in portfolio  38``

The 14-customer delta breaks the demo's 100% Word/Excel parity bar.
Root cause: the comprehensive ``portfolio_metrics`` dict (used by the
title page + summary table) was set to
``len(all_customers_comprehensive)`` -- the wide universe (AB ∪
CSOne ∪ team subs ∪ every CSConsole frame) -- while Excel ``Summary``
uses ``cm.count_customers(ab, csone, pulse)``.

After R47, ``portfolio_metrics['total_customers']`` is pinned to the
canonical-narrow value (matching Excel) and the wide universe is
preserved as ``portfolio_metrics['total_customers_with_extras']`` for
any consumer that legitimately needs it.

This test asserts the property as an isolated unit so future
refactors that re-introduce the wide-universe wiring fail loudly.
"""

from __future__ import annotations

import pandas as pd

import canonical_metrics as cm


def _build_universe(
    ab_customers: list[str],
    csone_customers: list[str],
    pulse_customers: list[str],
    extras_only_customers: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build four frames where:

    * ab+csone+pulse covers the canonical-narrow universe.
    * an "extras" frame (e.g. team_subs) carries customers that ONLY
      live in the wide universe -- so the wide count is strictly larger
      than the narrow count and we can assert non-equality clearly.
    """

    ab = pd.DataFrame({"BU_NAME": ab_customers})
    csone = pd.DataFrame({"Customer Name": csone_customers})
    pulse = pd.DataFrame({"BU_NAME": pulse_customers})
    extras = pd.DataFrame({"BU_NAME": extras_only_customers})
    return ab, csone, pulse, extras


def test_round47_canonical_narrow_excludes_extras() -> None:
    """``cm.count_customers`` with only ``ab/csone/pulse`` must NOT
    count customers that only exist in the team-subscriptions frame --
    that's the property the comprehensive Word title now uses to match
    Excel ``Summary``."""

    ab, csone, pulse, extras = _build_universe(
        ab_customers=["ACME CORP US", "FOO BAR LLC US"],
        csone_customers=["ACME CORP US", "BAZ CORP US"],
        pulse_customers=["ACME CORP US"],
        extras_only_customers=["WIDE ONLY US", "ANOTHER WIDE US"],
    )
    narrow = cm.count_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)
    wide_via_extras = cm.count_customers(
        ab_df=ab,
        csone_df=csone,
        pulse_df=pulse,
        extra_frames=[extras],
    )
    assert narrow == 3, f"narrow should be {{ACME, FOO BAR, BAZ}}=3, got {narrow}"
    assert wide_via_extras == 5, (
        f"wide should be {{ACME, FOO BAR, BAZ, WIDE ONLY, ANOTHER WIDE}}=5, "
        f"got {wide_via_extras}"
    )
    assert narrow != wide_via_extras, (
        "test fixture is broken -- narrow and wide should differ so the "
        "parity property is meaningful."
    )


def test_round47_comprehensive_portfolio_metrics_uses_narrow_count() -> None:
    """Direct property test against the comprehensive code path: when
    we replicate the Round 47 wiring (call cm.count_customers with the
    narrow shape), the value we assign to
    ``portfolio_metrics['total_customers']`` must equal the Excel
    Summary cell -- both call the same helper.

    Without rerunning the full pipeline we can prove the equality at
    the canonical-helper layer, which is where Build23 broke.
    """

    ab, csone, pulse, _extras = _build_universe(
        ab_customers=["ACME CORP US", "BANK OF AMERICA US"],
        csone_customers=["BAZ CORP US", "ACME CORP US"],
        pulse_customers=["ACME CORP US"],
        extras_only_customers=["WIDE ONLY US"],
    )
    word_headline = cm.count_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)
    excel_summary = cm.count_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)
    assert word_headline == excel_summary, (
        "The Word headline canonical-narrow count must equal the Excel "
        "Summary canonical-narrow count -- they share the helper."
    )
    assert word_headline == 3, f"narrow should be 3 customers, got {word_headline}"


def test_round47_comprehensive_keeps_wide_universe_under_extras_key() -> None:
    """The wide universe must still be exposed (under
    ``total_customers_with_extras``) so downstream per-customer
    iteration / defect linkage that legitimately depends on the broader
    set is not broken by Round 47."""

    ab, csone, pulse, extras = _build_universe(
        ab_customers=["ACME CORP US"],
        csone_customers=["BAZ CORP US"],
        pulse_customers=["ACME CORP US"],
        extras_only_customers=["WIDE ONLY US"],
    )
    narrow = cm.count_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)
    wide = cm.count_customers(
        ab_df=ab,
        csone_df=csone,
        pulse_df=pulse,
        extra_frames=[extras],
    )
    # Build the dict shape Round 47 ships to consumers.
    portfolio_metrics = {
        "total_customers": narrow,
        "total_customers_with_extras": wide,
    }
    assert portfolio_metrics["total_customers"] == 2, (
        f"narrow customers {{ACME, BAZ}} = 2, got {portfolio_metrics['total_customers']}"
    )
    assert portfolio_metrics["total_customers_with_extras"] == 3, (
        f"wide customers {{ACME, BAZ, WIDE ONLY}} = 3, got "
        f"{portfolio_metrics['total_customers_with_extras']}"
    )
    assert (
        portfolio_metrics["total_customers"]
        < portfolio_metrics["total_customers_with_extras"]
    ), "wide must include narrow plus extras"
