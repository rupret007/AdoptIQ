"""Round 126 / Build 95 (K2) -- support-case count narrative reconciler."""
from source_shape_utils import assert_in_source

import inspect

import adoptiq_backend as ab


def test_reconcile_support_cases_leading_number():
    narrative = "The portfolio shows 343 support cases over the window."
    out = ab.reconcile_support_case_count_claim(narrative, 369)
    assert "369 support cases" in out
    assert "343 support cases" not in out


def test_reconcile_support_cases_trailing_number():
    narrative = "Total support cases: 55 for this account."
    out = ab.reconcile_support_case_count_claim(narrative, 61)
    assert "Total support cases: 61" in out


def test_reconcile_tac_cases_alias():
    narrative = "We logged 120 TAC cases in scope."
    out = ab.reconcile_support_case_count_claim(narrative, 125)
    assert "125 TAC cases" in out


def test_reconcile_idempotent():
    narrative = "Portfolio has 10 support cases."
    once = ab.reconcile_support_case_count_claim(narrative, 10)
    twice = ab.reconcile_support_case_count_claim(once, 10)
    assert once == twice


def test_reconcile_invalid_canon_noop():
    narrative = "Portfolio has 10 support cases."
    assert ab.reconcile_support_case_count_claim(narrative, None) == narrative
    assert ab.reconcile_support_case_count_claim(narrative, -1) == narrative


def test_compact_path_wires_reconciler():
    import app_simple

    src = inspect.getsource(app_simple.run_compact_analysis)
    assert_in_source(src, "reconcile_support_case_count_claim", label='src')
    assert_in_source(src, "Round 126 / Build 95 (K2)", label='src')
