"""Round 2 / Phase 1.11 regression test.

``report_consistency.validate_report`` must accept ``extra_frames``
and ``account_to_customer`` and forward them to ``cm.count_customers``
when computing ``metrics['total_customers']``.

Without this thread, the validator counts only the AB+CSOne universe
while the report itself counts subscription-only / pulse-only customers
too, so the validator silently disagrees with the headline tile.
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
        "Round 2 Phase 1.11: validate_report must forward "
        "extra_frames to cm.count_customers so the validator's "
        "total_customers matches the report's headline tile."
    )


def test_validator_counts_subs_only_customer_via_extra_frames() -> None:
    """Functional check: a subscription-only customer (no AB/CSOne
    rows) must appear in metrics['total_customers'] when supplied via
    ``extra_frames``.
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
    assert metrics["total_customers"] == 2, (
        "Round 2 Phase 1.11: with subscription-only Beta supplied via "
        "extra_frames, total_customers must be 2 (Acme + Beta), not "
        f"1.  Got {metrics['total_customers']}."
    )
    canonical = cm.count_customers(ab_df=ab, csone_df=cs, extra_frames=[subs])
    assert metrics["total_customers"] == canonical, (
        "Round 2 Phase 1.11: validator total_customers must equal "
        "cm.count_customers(...) on the same inputs."
    )
