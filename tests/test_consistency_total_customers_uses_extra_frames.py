"""Round 2 / Phase 1.11 regression test.

``report_consistency.validate_report`` must accept ``extra_frames``
and ``account_to_customer`` so the validator's customer universe
matches the report's universe in every shape they participate in.

Round 25 / Phase A note: the validator's HEADLINE
``metrics['total_customers']`` is now derived from the narrow
``count_customers(ab_df, csone_df, pulse_df=...)`` shape (the same
shape used by the Excel ``Summary`` row and the post-Round 25 Word
headline tile).  ``extra_frames`` no longer widens the headline
count; instead it feeds ``metrics['total_customers_with_extras']``,
which preserves the pre-Round 25 wide-universe count for downstream
defect-customer linkage and per-section coverage diagnostics.
"""
from __future__ import annotations

import inspect
import re

import canonical_metrics as cm
import pandas as pd
from report_consistency import validate_report_consistency as validate_report


def test_validate_report_accepts_extra_frames_and_account_map() -> None:
    sig = inspect.signature(validate_report)
    assert "extra_frames" in sig.parameters, (
        "Round 2 Phase 1.11: report_consistency.validate_report must "
        "accept extra_frames so callers can include subscription-only "
        "/ pulse-only customers in the validation universe."
    )
    assert "account_to_customer" in sig.parameters, (
        "Round 2 Phase 1.11: report_consistency.validate_report must "
        "accept account_to_customer so it can backfill BU_NAME from "
        "ACCOUNT_ID_C the same way the report does."
    )


def test_validate_report_forwards_extra_frames_to_count_customers() -> None:
    src = inspect.getsource(validate_report)
    assert re.search(
        r"count_customers\([^)]*extra_frames\s*=\s*extra_frames",
        src,
        re.DOTALL,
    ), (
        "Round 2 Phase 1.11 / Round 25: validate_report must forward "
        "extra_frames to cm.count_customers (now in the "
        "``total_customers_with_extras`` diagnostic call) so the "
        "validator still surfaces the wide-universe count for "
        "callers that need it for defect-customer linkage."
    )


def test_validator_counts_subs_only_customer_via_extra_frames() -> None:
    """Round 25 / Phase A update: a subscription-only customer
    (no AB/CSOne/Pulse rows) supplied via ``extra_frames`` no
    longer enters the headline ``metrics['total_customers']``
    because the headline was narrowed to the displayed-sheets
    universe.  The wide-universe count remains available as
    ``metrics['total_customers_with_extras']`` for diagnostic
    purposes.
    """
    ab = pd.DataFrame([{"customer_name": "Acme"}])
    cs = pd.DataFrame([{"customer_name": "Acme"}])
    subs = pd.DataFrame([
        {"BU_NAME": "Beta",  "ACCOUNT_ID_C": "A2"},
        {"BU_NAME": "Acme",  "ACCOUNT_ID_C": "A1"},
    ])
    result = validate_report(
        ab_df=ab,
        csone_df=cs,
        extra_frames=[subs],
    )
    metrics = result["metrics"]

    # Round 25 / Phase A: headline narrows to (AB ∪ CSOne ∪ Pulse).
    # Beta lives only in extras, so it must NOT appear in the
    # narrow headline.
    assert metrics["total_customers"] == 1, (
        "Round 25 / Phase A: subscription-only Beta supplied via "
        "extra_frames must NOT inflate the headline "
        "total_customers (which is now the displayed-sheets "
        "universe AB ∪ CSOne ∪ Pulse).  Beta belongs in "
        f"total_customers_with_extras instead.  Got headline "
        f"{metrics['total_customers']}."
    )

    # The wide universe MUST still surface Beta -- this is the
    # diagnostic that preserves the pre-Round 25 count for
    # downstream consumers (defect linkage, per-section coverage).
    assert metrics["total_customers_with_extras"] == 2, (
        "Round 25 / Phase A: the wide-universe diagnostic "
        "``total_customers_with_extras`` must still include "
        "subscription-only Beta when extras are supplied.  Got "
        f"{metrics.get('total_customers_with_extras')}."
    )
    canonical_wide = cm.count_customers(
        ab_df=ab, csone_df=cs, extra_frames=[subs],
    )
    assert metrics["total_customers_with_extras"] == canonical_wide, (
        "Round 25 / Phase A: validator total_customers_with_extras "
        "must equal cm.count_customers(...) on the same inputs."
    )
