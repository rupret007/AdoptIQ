"""Round 32 / Phase 1.A regression: ``annotate_with_contract`` must
auto-remap aliased column names to their canonical slot names AND
escalate non-empty schema-drift cases to ``df.attrs['fetch_error']``.

Build6 ran to completion with four datasets (``adoption_barriers``,
``customer_pulse``, ``tac_cases``, ``bems_rows``) silently dropped on
the row-contract violation path.  See the round-32 plan for the full
diagnosis from ``~/.adoptiq/adoptiq.46198.log``.
"""
from __future__ import annotations

import pandas as pd
import pytest

from data_contracts import (
    annotate_with_contract,
    validate_row_contract,
    _normalize_column_name,
)


def test_normalize_strips_snowflake_prefixes_and_case() -> None:
    assert _normalize_column_name("AB_C__BU_NAME") == "bu_name"
    assert _normalize_column_name("PULSE_C__PULSE_RATING__C") == "pulse_rating"
    assert _normalize_column_name("\ufeff Severity ") == "severity"
    assert _normalize_column_name(None) == ""
    assert _normalize_column_name("") == ""


def test_alias_matched_via_case_fold_rename() -> None:
    df = pd.DataFrame({"severity": ["P1"], "BU_NAME": ["Acme"], "id": ["1"],
                       "subject": ["x"], "status": ["Open"]})
    result = validate_row_contract(df, dataset="adoption_barriers")
    assert result["is_valid"] is True
    matched = result["matched_columns"]
    assert matched["severity"] == "severity"
    assert matched["customer"] == "BU_NAME"


def test_prefix_stripped_match_satisfies_customer_slot() -> None:
    df = pd.DataFrame({
        "AB_C__BU_NAME": ["Acme"],
        "AB_C__SUBJECT_C": ["barrier"],
        "AB_C__AB_STATUS_C": ["Open"],
        "AB_C__SEVERITY_C": ["P2"],
        "ID": ["row-1"],
    })
    result = validate_row_contract(df, dataset="adoption_barriers")
    assert result["is_valid"] is True, result
    matched = result["matched_columns"]
    assert matched["customer"] == "AB_C__BU_NAME"
    assert matched["subject"] == "AB_C__SUBJECT_C"
    assert matched["status"] == "AB_C__AB_STATUS_C"
    assert matched["severity"] == "AB_C__SEVERITY_C"


def test_annotate_copies_canonical_slot_name_into_dataframe() -> None:
    df = pd.DataFrame({
        "AB_C__BU_NAME": ["Acme", "Foo"],
        "AB_C__SUBJECT_C": ["barrier-1", "barrier-2"],
        "AB_C__AB_STATUS_C": ["Open", "Closed"],
        "AB_C__SEVERITY_C": ["P2", "P3"],
        "ID": ["a", "b"],
    })
    annotated = annotate_with_contract(df, dataset="adoption_barriers")
    assert "customer" in annotated.columns
    assert list(annotated["customer"]) == ["Acme", "Foo"]
    assert "subject" in annotated.columns
    assert list(annotated["subject"]) == ["barrier-1", "barrier-2"]
    assert "id" in annotated.columns
    contract = annotated.attrs.get("row_contract")
    assert contract and contract["is_valid"] is True
    assert contract["matched_columns"]["customer"] == "AB_C__BU_NAME"


def test_annotate_does_not_clobber_existing_canonical_column() -> None:
    df = pd.DataFrame({
        "BU_NAME": ["Acme"],
        "customer": ["preserved"],
        "SUBJECT_C": ["x"],
        "AB_STATUS_C": ["Open"],
        "SEVERITY_C": ["P2"],
        "ID": ["1"],
    })
    annotated = annotate_with_contract(df, dataset="adoption_barriers")
    assert list(annotated["customer"]) == ["preserved"]


def test_non_empty_unmatched_frame_sets_fetch_error() -> None:
    df = pd.DataFrame({
        "totally_renamed_col_a": ["x"],
        "totally_renamed_col_b": ["y"],
    })
    annotated = annotate_with_contract(df, dataset="adoption_barriers")
    err = annotated.attrs.get("fetch_error")
    assert err and "schema_drift" in err and "adoption_barriers" in err
    assert annotated.attrs.get("fetch_error_kind") == "schema_drift"
    contract = annotated.attrs.get("row_contract")
    assert contract["is_valid"] is False
    assert contract["soft_pass"] is False
    assert contract["row_count"] == 1


def test_empty_unmatched_frame_soft_passes_without_fetch_error() -> None:
    df = pd.DataFrame(columns=["totally_renamed_col_a"])
    annotated = annotate_with_contract(df, dataset="adoption_barriers")
    assert annotated.attrs.get("fetch_error") is None, (
        "empty frame must not be escalated to fetch_error"
    )
    contract = annotated.attrs.get("row_contract")
    assert contract["is_valid"] is False
    assert contract["soft_pass"] is True
    assert contract["row_count"] == 0


def test_existing_fetch_error_is_not_overwritten() -> None:
    df = pd.DataFrame({"totally_renamed_col_a": ["x"]})
    df.attrs["fetch_error"] = "ECONNRESET upstream timeout"
    df.attrs["fetch_error_kind"] = "runtime"
    annotated = annotate_with_contract(df, dataset="adoption_barriers")
    assert annotated.attrs.get("fetch_error") == "ECONNRESET upstream timeout"
    assert annotated.attrs.get("fetch_error_kind") == "runtime"


def test_annotate_with_contract_returns_for_chaining_with_none() -> None:
    assert annotate_with_contract(None, dataset="adoption_barriers") is None


def test_validate_returns_matched_columns_and_row_count() -> None:
    df = pd.DataFrame({
        "BU_NAME": ["A", "B"],
        "PULSE_RATING__C": [4, 5],
    })
    result = validate_row_contract(df, dataset="customer_pulse")
    assert result["row_count"] == 2
    assert result["matched_columns"] == {
        "customer": "BU_NAME",
        "rating": "PULSE_RATING__C",
    }


def test_raise_on_missing_still_raises() -> None:
    df = pd.DataFrame({"unmatched": [1]})
    with pytest.raises(ValueError):
        validate_row_contract(df, dataset="adoption_barriers", raise_on_missing=True)
