"""Round 49 / F-DV-CONTRACT-DRIFT-R49 -- tac_cases CSOne literal alias.

Build25 re-audit (~/.adoptiq/adoptiq.27265.log lines 447-448) showed
the tac_cases contract still failing because the live CSOne XLSX
export delivers the customer column as the literal Salesforce
relationship-path label::

    Customer Name: Customer Name

R48 added the bare ``Customer Name`` alias but ``_normalize_column_name``
collapses ``Customer Name: Customer Name`` to ``customername:customername``
(the colon and second occurrence survive normalization), which does
NOT match the normalized form of any R48 alias
(``customer`` / ``customername`` / ``buname`` / ``accountname``).

R49-A2 fix: add the exact literal ``Customer Name: Customer Name``
alias so the contract passes against real CSOne fixtures.  This pins:

1. The contract validator passes when the only customer column
   present is the literal ``Customer Name: Customer Name``.
2. ``annotate_with_contract`` does not escalate ``schema_drift`` for
   such a frame.
3. The R48 aliases keep working (no regression).
4. A frame whose only customer column is misspelled / opaque still
   escalates the contract violation.
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
    "customer_col",
    [
        # R49 addition: literal CSOne XLSX label
        "Customer Name: Customer Name",
        # R48 baseline must still pass (no regression)
        "Customer Name",
        "Customer",
        "BU_NAME",
        "customer_name",
        "Account Name",
    ],
)
def test_tac_cases_customer_slot_accepts_csone_literal_and_r48_baseline(customer_col):
    df = pd.DataFrame(
        {
            "Case #": ["C-1", "C-2", "C-3"],
            customer_col: ["Acme", "Foo", "Bar"],
            "Severity": ["P1", "P2", "P3"],
            "Status": ["Open", "Closed", "Open"],
        }
    )
    result = validate_row_contract(df, dataset="tac_cases")
    assert result["is_valid"], (
        f"R49-A2: tac_cases customer slot rejected the {customer_col!r} "
        f"column; missing={result['missing_slots']}"
    )
    assert result["matched_columns"]["customer"] == customer_col


def test_tac_cases_alias_list_documents_csone_literal_form():
    aliases = ROW_CONTRACT_ALIASES["tac_cases"]["customer"]
    assert "Customer Name: Customer Name" in aliases, (
        "R49-A2 / F-DV-CONTRACT-DRIFT-R49: tac_cases customer slot "
        "must accept the literal CSOne XLSX label "
        "'Customer Name: Customer Name' (Salesforce relationship-path "
        "form)."
    )


def test_csone_literal_only_frame_does_not_escalate_schema_drift():
    """Build25 regression: a CSOne-fixture frame whose only customer
    column was the literal ``Customer Name: Customer Name`` was
    getting ``fetch_error_kind='schema_drift'`` stamped on it.
    """
    df = pd.DataFrame(
        {
            "Case #": ["C-1", "C-2"],
            "Customer Name: Customer Name": ["Acme", "Foo"],
            "Severity": ["P1", "P2"],
            "Status": ["Open", "Closed"],
        }
    )
    annotated = annotate_with_contract(df, dataset="tac_cases")
    assert "fetch_error" not in annotated.attrs, (
        "R49-A2: a CSOne-style frame with 'Customer Name: Customer Name' "
        f"must satisfy the tac_cases contract; got fetch_error="
        f"{annotated.attrs.get('fetch_error')!r}"
    )
    assert "fetch_error_kind" not in annotated.attrs


def test_opaque_customer_column_still_escalates_drift():
    """Sanity: the R49 alias expansion must not weaken the contract.
    A non-empty frame with NO recognizable customer column must still
    be escalated to schema_drift.
    """
    df = pd.DataFrame(
        {
            "Case #": ["C-1", "C-2"],
            "RANDOM_OPAQUE_COL": ["x", "y"],
            "Severity": ["P1", "P2"],
            "Status": ["Open", "Closed"],
        }
    )
    annotated = annotate_with_contract(df, dataset="tac_cases")
    assert "fetch_error" in annotated.attrs
    assert annotated.attrs.get("fetch_error_kind") == "schema_drift"
    assert "customer" in annotated.attrs.get("fetch_error", "")
