"""Round 28 / Phase 3 — pin the multi-currency contract for
``RENEWAL_ARR_THRESHOLDS`` consumers in
``advanced_renewal_analyzer._calculate_renewal_risk_score``.

The contract (formalized in ``risk_scoring.RENEWAL_ARR_THRESHOLDS``
module docstring) requires consumers to skip every USD-basis
threshold compare when ``financial_metrics["is_multi_currency"]`` is
True OR ``financial_metrics["currency"].upper() != currency_basis``,
and to emit a disclosure factor naming the customer's currency vs
the basis so the omission is visible in the rendered narrative.

These tests parametrize over (currency, is_multi_currency, ARR-band)
and assert:

  1. When the gate would skip, the high/low ARR success/risk factor
     does NOT appear in ``risk_factors`` / ``success_factors``.
  2. When the gate would skip, a disclosure factor IS appended that
     names the customer currency (single-currency-non-USD case) or
     declares "multi-currency portfolio" (multi-currency case).
  3. When the gate applies (single-currency USD), the high/low ARR
     factor IS produced as expected.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

from typing import Any, Dict

import pytest

from advanced_renewal_analyzer import AdvancedRenewalAnalyzer


def _make_analysis(
    *,
    total_arr: float,
    currency: str,
    is_multi_currency: bool,
) -> Dict[str, Any]:
    """Build a minimal ``analysis_results`` dict that exercises the
    ARR-gate branch of ``_calculate_renewal_risk_score``
    without firing other risk factors."""
    return {
        'contract_information': {
            'contracts': [],
            'auto_renewal_contracts': 1,
            'manual_renewal_contracts': 0,
            'contracts_expiring_soon': [],
            'total_arr': total_arr,
        },
        'financial_metrics': {
            'total_arr': total_arr,
            'currency': currency,
            'is_multi_currency': is_multi_currency,
            # Below high_discount_pct gate so it does not contaminate
            # the disclosure-factor assertion.
            'discount_percentage': 0,
        },
        'usage_metrics': {
            # Mid-range so neither low_completion nor high_completion
            # gate fires.
            'overall_completion_rate': 0.65,
            'recent_engagement_score': 50,
        },
        'support_metrics': {
            'engagement_level': 'MEDIUM',
            'completion_rate': 0.7,
        },
        'adoption_metrics': {
            'adoption_health_score': 65,
            'high_severity_barriers': 0,
        },
    }


@pytest.mark.parametrize(
    'currency, is_multi_currency, total_arr, expect_disclosure',
    [
        # single-currency USD: gate applies; no disclosure
        ('USD', False, 200_000, False),
        ('USD', False, 5_000, False),
        # single-currency non-USD: gate skipped; non-USD disclosure
        ('EUR', False, 200_000, True),
        ('EUR', False, 5_000, True),
        ('JPY', False, 200_000, True),
        # multi-currency: gate skipped; multi-currency disclosure
        ('MIXED', True, 200_000, True),
        ('USD', True, 5_000, True),
    ],
)
def test_round28_arr_gate_skip_emits_disclosure(
    currency: str,
    is_multi_currency: bool,
    total_arr: float,
    expect_disclosure: bool,
) -> None:
    """Round 28 contract: when the ARR gate skips, the analyzer
    MUST append a disclosure factor naming the customer currency
    (or 'multi-currency portfolio') so the omission is visible."""
    analyzer = AdvancedRenewalAnalyzer.__new__(AdvancedRenewalAnalyzer)
    analysis = _make_analysis(
        total_arr=total_arr,
        currency=currency,
        is_multi_currency=is_multi_currency,
    )
    out = analyzer._calculate_renewal_risk_score(analysis)
    factors = list(out.get('risk_factors', []))
    success = list(out.get('success_factors', []))

    if expect_disclosure:
        all_factors = factors + success
        assert any(
            'ARR gates skipped' in str(f) for f in all_factors
        ), (
            f"Round 28: gate-skip case (currency={currency!r}, "
            f"is_multi_currency={is_multi_currency}) MUST emit an "
            f"'ARR gates skipped' disclosure factor; got: "
            f"factors={factors!r}, success={success!r}"
        )
    else:
        all_factors = factors + success
        assert not any(
            'ARR gates skipped' in str(f) for f in all_factors
        ), (
            f"Round 28: USD single-currency case must NOT emit a "
            f"gate-skip disclosure; got: factors={factors!r}, "
            f"success={success!r}"
        )


def test_round28_arr_gate_skipped_no_high_or_low_arr_factor() -> None:
    """When the gate skips, neither the 'High-value customer' nor
    the 'Low-value customer' factor should appear -- the underlying
    USD threshold compare must not have been performed."""
    analyzer = AdvancedRenewalAnalyzer.__new__(AdvancedRenewalAnalyzer)

    for currency, is_mc in [('EUR', False), ('JPY', False), ('MIXED', True)]:
        for arr in [5_000, 200_000]:
            analysis = _make_analysis(
                total_arr=arr,
                currency=currency,
                is_multi_currency=is_mc,
            )
            out = analyzer._calculate_renewal_risk_score(analysis)
            all_factors = list(out.get('risk_factors', [])) + list(
                out.get('success_factors', [])
            )
            assert not any(
                'High-value customer' in str(f) for f in all_factors
            ), (
                f"Round 28: gate-skip case (currency={currency!r}, "
                f"is_mc={is_mc}, arr={arr}) leaked a 'High-value "
                f"customer' factor; ARR gate compared against the "
                f"USD-basis threshold despite mismatched currency. "
                f"factors={all_factors!r}"
            )
            assert not any(
                'Low-value customer' in str(f) for f in all_factors
            ), (
                f"Round 28: gate-skip case (currency={currency!r}, "
                f"is_mc={is_mc}, arr={arr}) leaked a 'Low-value "
                f"customer' factor; ARR gate compared against the "
                f"USD-basis threshold despite mismatched currency. "
                f"factors={all_factors!r}"
            )


def test_round28_arr_gate_applies_on_single_currency_usd_high_value() -> None:
    """Sanity: USD single-currency high-value triggers the
    'High-value customer' success factor."""
    analyzer = AdvancedRenewalAnalyzer.__new__(AdvancedRenewalAnalyzer)
    analysis = _make_analysis(
        total_arr=250_000,
        currency='USD',
        is_multi_currency=False,
    )
    out = analyzer._calculate_renewal_risk_score(analysis)
    success = [str(f) for f in out.get('success_factors', [])]
    assert any('High-value customer' in f for f in success), (
        "Round 28: USD single-currency 250k must trigger 'High-value "
        f"customer' success factor; got: {success!r}"
    )


def test_round28_arr_gate_applies_on_single_currency_usd_low_value() -> None:
    """Sanity: USD single-currency low-value triggers the
    'Low-value customer' risk factor."""
    analyzer = AdvancedRenewalAnalyzer.__new__(AdvancedRenewalAnalyzer)
    analysis = _make_analysis(
        total_arr=5_000,
        currency='USD',
        is_multi_currency=False,
    )
    out = analyzer._calculate_renewal_risk_score(analysis)
    factors = [str(f) for f in out.get('risk_factors', [])]
    assert any('Low-value customer' in f for f in factors), (
        "Round 28: USD single-currency 5k must trigger 'Low-value "
        f"customer' risk factor; got: {factors!r}"
    )


def test_round28_renewal_arr_thresholds_contract_doc_present() -> None:
    """Round 28: pin the contract docstring in risk_scoring.py so
    a future edit cannot silently drop the consumer contract."""
    import inspect

    import risk_scoring

    src = inspect.getsource(risk_scoring)
    assert 'Round 28' in src, (
        "Round 28: risk_scoring.py module must carry a Round 28 "
        "marker stamping the multi-currency contract."
    )
    # Pin the contract-statement keywords so a casual edit cannot
    # silently weaken the docstring.
    assert_in_source(src, 'currency_basis', label='src')
    assert_in_source(src, 'is_multi_currency', label='src')
    assert 'disclosure factor' in src, (
        "Round 28: the contract docstring must explicitly require "
        "consumers to emit a disclosure factor on skip."
    )
