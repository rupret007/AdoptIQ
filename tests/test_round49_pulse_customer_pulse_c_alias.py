"""Round 49 / F-DV-CONTRACT-DRIFT-R49 regression tests.

The Build25 re-audit (~/.adoptiq/adoptiq.27265.log lines 434-437)
showed the customer_pulse contract still failing despite Round 48's
alias expansion::

    [[CONTRACT]] Row contract violation for customer_pulse:
    missing required slot(s) - slot 'rating' (one of:
    PULSE_RATING__C, PULSE_RATING, Rating, RATING, SCORE__C,
    SCORE_C, Score, SCORE, Pulse Rating, pulse_rating, rating,
    OVERALL_RATING__C, OVERALL_RATING)

Root cause: the live Snowflake ``ESA_C360_CUSTOMER_PULSE__C`` view
delivers the rating value under the column literally named
``CUSTOMER_PULSE__C`` -- not ``PULSE_RATING__C``.  The
``_normalize_column_name`` helper case-folds and strips the trailing
``__C`` suffix, so ``CUSTOMER_PULSE__C`` normalizes to
``customer_pulse``, which does NOT match the normalized form of any
R48 alias (``pulse_rating`` / ``rating`` / ``score`` /
``overall_rating``).

R49-A2 fix: add the missing ``CUSTOMER_PULSE__C`` and
``CUSTOMER_PULSE`` aliases so the contract passes against the live
column.  This pins:

1. The contract validator passes when the only rating column
   present is ``CUSTOMER_PULSE__C`` (the live Snowflake column).
2. ``annotate_with_contract`` does not escalate to ``schema_drift``
   for a frame whose only rating column is ``CUSTOMER_PULSE__C``.
3. The alias list is documented to include ``CUSTOMER_PULSE__C`` /
   ``CUSTOMER_PULSE`` so the next reader does not have to rediscover
   the variant.
4. The R48 aliases keep working (no regression).
5. A truly missing rating column (no aliases present) still
   escalates the schema_drift on a non-empty frame.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_contracts import (  # noqa: E402
    ROW_CONTRACT_ALIASES,
    annotate_with_contract,
    validate_row_contract,
)


@pytest.mark.parametrize(
    "rating_col",
    [
        # Round 49 additions
        "CUSTOMER_PULSE__C",
        "CUSTOMER_PULSE",
        # Round 48 baseline must still pass (no regression)
        "PULSE_RATING__C",
        "PULSE_RATING",
        "Pulse Rating",
        "SCORE__C",
        "SCORE_C",
        "OVERALL_RATING__C",
        "OVERALL_RATING",
    ],
)
def test_pulse_rating_slot_accepts_customer_pulse_c_and_r48_baseline(rating_col):
    df = pd.DataFrame(
        {
            "ID": [1, 2, 3],
            rating_col: ["Green", "Yellow", "Red"],
            "BU_NAME": ["Acme", "Foo", "Bar"],
            "ACCOUNT__C": ["a1", "a2", "a3"],
            "CREATEDDATE": ["2026-04-01", "2026-04-15", "2026-04-20"],
        }
    )
    result = validate_row_contract(df, dataset="customer_pulse")
    assert result["is_valid"], (
        f"R49-A2: customer_pulse rating slot rejected the {rating_col!r} "
        f"column; missing={result['missing_slots']}"
    )
    assert result["matched_columns"]["rating"] == rating_col


def test_pulse_rating_alias_list_documents_customer_pulse_c_variants():
    aliases = ROW_CONTRACT_ALIASES["customer_pulse"]["rating"]
    assert "CUSTOMER_PULSE__C" in aliases, (
        "R49-A2 / F-DV-CONTRACT-DRIFT-R49: customer_pulse rating slot "
        "must accept the live Snowflake column 'CUSTOMER_PULSE__C'."
    )
    assert "CUSTOMER_PULSE" in aliases, (
        "R49-A2 / F-DV-CONTRACT-DRIFT-R49: customer_pulse rating slot "
        "must also accept the suffix-less 'CUSTOMER_PULSE' variant for "
        "post-fetch friendly renames."
    )


def test_customer_pulse_c_only_frame_does_not_escalate_schema_drift():
    """Build25 regression: a frame whose only rating column is
    ``CUSTOMER_PULSE__C`` was getting ``fetch_error_kind='schema_drift'``
    stamped on it because the alias list was missing the variant.
    """
    df = pd.DataFrame(
        {
            "ID": [1, 2, 3],
            "BU_NAME": ["Acme", "Foo", "Bar"],
            "CUSTOMER_PULSE__C": ["Green", "Yellow", "Red"],
        }
    )
    annotated = annotate_with_contract(df, dataset="customer_pulse")
    assert "fetch_error" not in annotated.attrs, (
        "R49-A2: a frame with CUSTOMER_PULSE__C must satisfy the "
        f"customer_pulse contract; got fetch_error="
        f"{annotated.attrs.get('fetch_error')!r}"
    )
    assert "fetch_error_kind" not in annotated.attrs


def test_truly_missing_rating_still_escalates_drift_on_non_empty_frame():
    """Sanity: the R49 alias expansion must not weaken the contract.
    A non-empty frame with NO recognizable rating column must still
    be escalated to schema_drift -- otherwise we silently mask the
    real upstream regression the contract is designed to catch.
    """
    df = pd.DataFrame(
        {
            "ID": [1, 2],
            "BU_NAME": ["Acme", "Foo"],
            "RANDOM_OPAQUE_COL": ["x", "y"],
        }
    )
    annotated = annotate_with_contract(df, dataset="customer_pulse")
    assert "fetch_error" in annotated.attrs
    assert annotated.attrs.get("fetch_error_kind") == "schema_drift"
    assert "rating" in annotated.attrs.get("fetch_error", "")
