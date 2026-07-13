"""Round 4 / Phase 1.3 regression test.

The Executive Excel ``Risk_Summary`` per-customer barrier/case counts
must be joined through ``normalize_customer_name`` on both sides.
Pre-Round-4 the writer filtered on the raw ``customer_name`` while
``risk_scores`` keys came through ``normalize_customer_name``, so
same-customer rows under-counted (often to 0).
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_risk_summary_uses_normalize_customer_name() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # The Round 4 fix introduces a ``_norm_cust_name`` helper that
    # routes through ``normalize_customer_name`` (or wraps it). At a
    # minimum the writer must reference normalize_customer_name in the
    # risk-summary join area.
    assert "normalize_customer_name" in src, (
        "Round 4 Phase 1.3: Executive Excel Risk_Summary join must "
        "go through normalize_customer_name so the risk_scores keys "
        "and the per-customer barrier/case counts align."
    )
    # The Round 4 fix specifically introduces a ``_norm_cust_name``
    # helper or applies ``normalize_customer_name`` to the customer
    # iteration variable.  Either pattern is acceptable.
    assert (
        re.search(r"_norm_cust_name", src)
        or re.search(r"normalize_customer_name\s*\(\s*customer", src)
    ), (
        "Round 4 Phase 1.3: Executive Excel Risk_Summary must apply "
        "normalize_customer_name to the customer key when looking up "
        "barrier/case counts; otherwise raw vs normalized keys drift."
    )
