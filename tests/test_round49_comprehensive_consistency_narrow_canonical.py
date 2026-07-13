"""Round 49 / F-COMP-CONSIST-WIDTH-MISMATCH regression tests.

Build25 re-audit caught the comprehensive Brian Frazier report
failing at consistency-check time with::

    Portfolio metric mismatch: total_customers=38 (Word headline) !=
    52 (canonical AB ∪ CSOne ∪ Pulse universe)

Root cause: R47-B4 narrowed the comprehensive Word headline to the
``count_customers(ab, csone, pulse)`` shape (38), but
``report_consistency.validate_report_consistency`` still derived
``metrics['total_customers']`` from the wider ``customer_universe``
arg (52, the comprehensive iteration roster from
``_get_all_customers_from_all_sources``).  Word + Excel agreed; the
validator was the third side of the triangle and blocked the build.

R49-A1 fix: ``customer_universe`` is now telemetry-only.  The
validator's ``metrics['total_customers']`` always equals
``count_customers(ab_df, csone_df, pulse_df)`` regardless of the
``customer_universe`` arg.  The wider iteration roster is surfaced
as ``metrics['customer_universe_total']`` for callers that legitimately
need it.

These tests pin:

1. The narrow-vs-narrow parity passes when Word headline matches
   the narrow ``count_customers`` shape, even when
   ``customer_universe`` is wider.
2. The error string still fires when Word headline drifts from the
   narrow shape (caller bug, NOT a universe-vs-headline mismatch).
3. ``metrics['customer_universe_total']`` reflects the wider roster
   so coverage / iteration callers can still introspect it.
4. The error string no longer claims the canonical is the "AB ∪
   CSOne ∪ Pulse universe" -- it now correctly references
   ``count_customers(ab_df, csone_df, pulse_df)``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import canonical_metrics as cm  # noqa: E402
from report_consistency import validate_report_consistency  # noqa: E402


def _build_narrow_frames():
    """Return (ab_df, csone_df, pulse_df) whose narrow
    count_customers shape resolves to 3 (Acme, Beta, Gamma).
    """
    ab = pd.DataFrame([
        {"customer_name": "Acme", "sub_technology": "UCCE"},
        {"customer_name": "Beta", "sub_technology": "WebEx"},
    ])
    csone = pd.DataFrame([
        {"customer_name": "Acme", "Severity": "P2"},
        {"customer_name": "Gamma", "Severity": "P3"},
    ])
    pulse = pd.DataFrame([
        {"customer_name": "Beta", "rating": "Yellow"},
    ])
    return ab, csone, pulse


def test_total_customers_parity_passes_when_universe_is_wider() -> None:
    """The comprehensive scenario: Word headline = narrow (3),
    customer_universe = wide (10).  Pre-R49 the validator widened
    its canonical to 10 and erroneously raised ``38 != 52`` style.
    Post-R49 the validator stays narrow at 3 and parity passes.
    """
    ab, csone, pulse = _build_narrow_frames()
    narrow_count = cm.count_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)
    assert narrow_count == 3, "narrow_count fixture must resolve to 3"

    wider_universe = [
        "Acme", "Beta", "Gamma", "Delta", "Epsilon",
        "Zeta", "Eta", "Theta", "Iota", "Kappa",
    ]
    pm = cm.build_portfolio_metrics(ab_df=ab, csone_df=csone)
    pm["total_customers"] = narrow_count

    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone,
        portfolio_metrics=pm,
        customer_universe=wider_universe,
        pulse_df=pulse,
    )
    assert result["is_valid"] is True, (
        "R49-A1: parity headline must use narrow count_customers, not "
        f"customer_universe.  errors={result['errors']!r}"
    )
    assert result["metrics"]["total_customers"] == 3
    assert result["metrics"]["customer_universe_total"] == 10, (
        "R49-A1: wider customer_universe should be preserved as a "
        "diagnostic metric so coverage / iteration callers can "
        "introspect the iteration roster."
    )


def test_total_customers_drift_still_fires_when_pm_drifts_from_narrow() -> None:
    """The validator's parity gate is still active.  If
    ``portfolio_metrics['total_customers']`` drifts from the narrow
    canonical, the error MUST fire (otherwise we would mask a real
    headline bug).  The fix is narrow-vs-narrow, not gate-removal.
    """
    ab, csone, pulse = _build_narrow_frames()
    pm = cm.build_portfolio_metrics(ab_df=ab, csone_df=csone)
    pm["total_customers"] = 99  # drift

    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone,
        portfolio_metrics=pm,
        customer_universe=["Acme", "Beta", "Gamma", "Delta"],
        pulse_df=pulse,
    )
    assert result["is_valid"] is False
    drift_errors = [e for e in result["errors"] if "total_customers" in e]
    assert drift_errors, (
        "R49-A1: parity gate must still fire when the Word headline "
        "drifts from the narrow canonical."
    )


def test_error_string_references_narrow_count_customers_canonical() -> None:
    """R49-A1 also re-words the error string so the
    "(canonical AB ∪ CSOne ∪ Pulse universe)" label correctly
    references ``count_customers(ab_df, csone_df, pulse_df)``,
    matching the actual computation.  Pre-R49 the label claimed the
    canonical was the universe (a lie -- the universe was the wide
    iteration roster, not the narrow displayed-sheets count).
    """
    ab, csone, pulse = _build_narrow_frames()
    pm = cm.build_portfolio_metrics(ab_df=ab, csone_df=csone)
    pm["total_customers"] = 99
    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone,
        portfolio_metrics=pm,
        pulse_df=pulse,
    )
    drift_errors = [e for e in result["errors"] if "total_customers" in e]
    assert drift_errors
    msg = drift_errors[0]
    assert "count_customers(ab_df, csone_df, pulse_df)" in msg, (
        f"R49-A1: error string must reference the narrow "
        f"count_customers canonical.  got: {msg!r}"
    )


def test_customer_universe_telemetry_present_even_without_pm() -> None:
    """Sanity: ``metrics['customer_universe_total']`` is populated
    whenever ``customer_universe`` is provided, even when no
    ``portfolio_metrics`` parity check runs.  This keeps the
    telemetry surface stable for coverage / per-customer iteration
    callers that only care about the wider roster.
    """
    ab, csone, pulse = _build_narrow_frames()
    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone,
        customer_universe=["Acme", "Beta", "Gamma", "Delta", "Epsilon"],
        pulse_df=pulse,
    )
    assert result["metrics"]["customer_universe_total"] == 5
    assert result["metrics"]["total_customers"] == 3, (
        "Sanity: total_customers is the narrow shape regardless of "
        "customer_universe."
    )


def test_no_customer_universe_passes_through_narrow_shape() -> None:
    """When ``customer_universe`` is omitted, behaviour is unchanged
    from R25 / R47: ``metrics['total_customers']`` is the narrow
    ``count_customers(ab, csone, pulse)`` shape, and there is no
    ``customer_universe_total`` diagnostic surface.
    """
    ab, csone, pulse = _build_narrow_frames()
    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone,
        pulse_df=pulse,
    )
    assert result["metrics"]["total_customers"] == 3
    assert "customer_universe_total" not in result["metrics"], (
        "R49-A1: customer_universe_total surface only appears when "
        "the caller passes customer_universe."
    )
