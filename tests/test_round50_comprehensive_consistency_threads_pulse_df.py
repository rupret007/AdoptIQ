"""Round 50 / F-COMP-CONSIST-PULSE-THREAD regression tests.

Build 26 / Round 49 narrowed the validator's
``metrics['total_customers']`` to ``count_customers(ab_df, csone_df,
pulse_df)`` regardless of ``customer_universe`` -- but the comprehensive
call site at ``app_simple.py:13946`` did not thread the pulse frame
through, so the validator's narrow count fell back to
``count_customers(ab, csone, pulse=None)``.  When pulse contributed
customers that AB / CSOne did not (a portfolio with pulse-only
customers, e.g. the Brian Frazier / Dee Kindrick 90d demo runs of
2026-04-29), the validator's narrow count and the Word headline's
narrow count diverged by exactly the pulse-only customer count, and
the strict-mode parity gate fired::

    Portfolio metric mismatch: total_customers=38 (Word headline) !=
    28 (canonical count_customers(ab_df, csone_df, pulse_df)).

This blocked the comprehensive report at consistency-check time even
though Word and Excel were already in lockstep.

R50 fix: thread ``customer_pulse_df=csconsole_customer_pulse`` into
the comprehensive ``validate_report_consistency(...)`` call so the
validator's narrow count matches the Word headline by construction.
The leader path has done this since Round 6 / Phase 5.8; this test
pins the same behaviour for the comprehensive path.

These tests pin:

1. Source-shape: the comprehensive ``validate_report_consistency(...)``
   call inside ``run_comprehensive_analysis`` passes a pulse keyword
   (``customer_pulse_df=`` or ``pulse_df=``).
2. Behaviour-positive: when the Word headline's
   ``portfolio_metrics['total_customers']`` is ``count_customers(ab,
   csone, pulse)`` AND the validator receives the same pulse frame,
   parity passes.
3. Behaviour-negative (regression reproducer): without the pulse arg,
   the same scenario reproduces the user-reported error string -- so
   the parity gate is still active and would catch a future call site
   that drops the threading.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import canonical_metrics as cm  # noqa: E402
from report_consistency import validate_report_consistency  # noqa: E402


def _build_pulse_widening_frames():
    """Return (ab_df, csone_df, pulse_df) where pulse contributes a
    customer that AB / CSOne do not.

    Narrow shapes:
      - count_customers(ab, csone)             -> 2 (Acme, Beta)
      - count_customers(ab, csone, pulse)      -> 3 (Acme, Beta, Pulse-Only Co)

    The 1-customer delta is exactly the pulse-only customer; this is
    the same shape as the user-reported Brian Frazier 90d 38 vs 28
    failure, just sized down for unit-test economy.
    """
    ab = pd.DataFrame([
        {"customer_name": "Acme", "sub_technology": "UCCE"},
        {"customer_name": "Beta", "sub_technology": "WebEx"},
    ])
    csone = pd.DataFrame([
        {"customer_name": "Acme", "Severity": "P2"},
    ])
    pulse = pd.DataFrame([
        {"customer_name": "Pulse-Only Co", "rating": "Yellow"},
    ])
    return ab, csone, pulse


# ---------------------------------------------------------------------------
# 1. Source-shape: comprehensive call site threads a pulse keyword.
# ---------------------------------------------------------------------------


def _find_comprehensive_validator_call() -> ast.Call:
    """Locate the ``validate_report_consistency(...)`` call inside the
    ``run_comprehensive_analysis`` function in ``app_simple.py``.

    Returns the AST Call node so individual tests can assert on its
    keyword arguments without coupling to line numbers (which drift as
    surrounding code evolves).
    """
    src = (PROJECT_ROOT / "app_simple.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "run_comprehensive_analysis":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if isinstance(func, ast.Name) and func.id == "validate_report_consistency":
                return sub
        break
    raise AssertionError(
        "Round 50: could not locate validate_report_consistency(...) call "
        "inside run_comprehensive_analysis -- did the comprehensive "
        "consistency check get refactored or moved?"
    )


def test_comprehensive_validator_call_threads_pulse_keyword() -> None:
    """The comprehensive ``validate_report_consistency(...)`` call MUST
    pass either ``customer_pulse_df=`` or ``pulse_df=`` so the
    validator's narrow ``count_customers(ab, csone, pulse)`` matches
    the Word headline narrow count at app_simple.py:13867-13871.

    Pre-R50 the call did not pass either, and the validator's narrow
    count fell back to ``count_customers(ab, csone, pulse=None)`` --
    diverging from the Word headline by exactly the pulse-only
    customer count.
    """
    call = _find_comprehensive_validator_call()
    keyword_names = {kw.arg for kw in call.keywords if kw.arg is not None}
    assert ("customer_pulse_df" in keyword_names) or ("pulse_df" in keyword_names), (
        "Round 50 / F-COMP-CONSIST-PULSE-THREAD: comprehensive "
        "validate_report_consistency(...) call must thread pulse via "
        "either customer_pulse_df= or pulse_df= so the validator's "
        "narrow count matches the Word headline.  found kwargs: "
        f"{sorted(keyword_names)!r}"
    )


def test_comprehensive_validator_pulse_uses_csconsole_customer_pulse() -> None:
    """The validator must receive the same criteria-scoped pulse frame
    used by the all-source customer universe.
    """
    call = _find_comprehensive_validator_call()
    pulse_kw = next(
        (
            kw
            for kw in call.keywords
            if kw.arg in {"customer_pulse_df", "pulse_df"}
        ),
        None,
    )
    assert pulse_kw is not None, (
        "Round 50: pulse keyword missing on comprehensive validator call "
        "(see test_comprehensive_validator_call_threads_pulse_keyword)."
    )
    src_segment = ast.unparse(pulse_kw.value)
    assert src_segment == "_count_customer_pulse", (
        "Comprehensive validation must use the criteria-scoped pulse "
        f"frame, not the manager-wide raw source. found: {src_segment!r}"
    )


# ---------------------------------------------------------------------------
# 2. Behaviour-positive: parity passes when both sides see pulse.
# ---------------------------------------------------------------------------


def test_consistency_passes_when_pulse_is_threaded_through() -> None:
    """Mirrors the Round 50 fix: the Word headline narrow count
    includes pulse, and the validator now receives the same pulse
    frame.  Parity passes; ``metrics['total_customers']`` agrees with
    the Word headline.

    Mirrors the production call shape at ``app_simple.py:13946`` --
    ``strict_mode=False``, with the call site checking ``is_valid``
    on its own.  We assert there are no ``total_customers`` parity
    errors (other unrelated strict-mode warnings, e.g. factual_claims,
    are out of scope for this fix and tracked separately).
    """
    ab, csone, pulse = _build_pulse_widening_frames()

    word_headline_narrow = cm.count_customers(
        ab_df=ab, csone_df=csone, pulse_df=pulse
    )
    assert word_headline_narrow == 3, (
        "fixture sanity: pulse should widen the narrow count to 3"
    )

    pm = cm.build_portfolio_metrics(ab_df=ab, csone_df=csone)
    pm["total_customers"] = word_headline_narrow

    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone,
        portfolio_metrics=pm,
        customer_pulse_df=pulse,
        strict_mode=False,  # mirrors comprehensive call site
    )
    drift_errors = [e for e in result["errors"] if "total_customers" in e]
    assert not drift_errors, (
        "Round 50: when pulse is threaded into the validator, the "
        "narrow count_customers(ab, csone, pulse) shape must agree "
        f"with the Word headline.  total_customers errors={drift_errors!r}"
    )
    assert result["metrics"]["total_customers"] == word_headline_narrow


# ---------------------------------------------------------------------------
# 3. Behaviour-negative: omitting pulse reproduces the user-reported
#    failure (regression reproducer; pins that the gate is still
#    active and would catch a future drop of the threading).
# ---------------------------------------------------------------------------


def test_omitting_pulse_reproduces_user_reported_mismatch() -> None:
    """If a future change drops ``customer_pulse_df=`` from the
    comprehensive validator call, the parity gate must still fire.
    This pins the regression reproducer for the user-reported
    ``Portfolio metric mismatch: total_customers=38 (Word headline)
    != 28 (canonical count_customers(ab_df, csone_df, pulse_df))``
    error from the Brian Frazier 90d run.
    """
    ab, csone, pulse = _build_pulse_widening_frames()

    word_headline_narrow = cm.count_customers(
        ab_df=ab, csone_df=csone, pulse_df=pulse
    )
    pm = cm.build_portfolio_metrics(ab_df=ab, csone_df=csone)
    pm["total_customers"] = word_headline_narrow

    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone,
        portfolio_metrics=pm,
        # NOTE: no customer_pulse_df / pulse_df -- this is the pre-R50
        # comprehensive call shape; the gate should fire.
        strict_mode=False,
    )
    drift = [e for e in result["errors"] if "total_customers" in e]
    assert drift, (
        "Round 50 regression reproducer: without pulse threading, the "
        "validator's narrow count must NOT match the Word headline "
        "narrow count (which includes pulse).  If this list is empty, "
        "the pulse-only widening shape is being silently absorbed -- "
        "investigate before relying on the narrow-vs-narrow contract.  "
        f"all errors={result['errors']!r}"
    )
    assert "count_customers(ab_df, csone_df, pulse_df)" in drift[0], (
        "Round 50: error string must reference the narrow canonical "
        f"shape so the remediation hint points at the right helper.  "
        f"got: {drift[0]!r}"
    )
