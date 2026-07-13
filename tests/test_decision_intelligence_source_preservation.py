"""Ownership variants must survive source acquisition until canonical analysis."""

from __future__ import annotations

import sys
import types
from unittest import mock

import pandas as pd
import pytest

# The ownership boundary is offline; connector imports are never exercised.
if "hvac" not in sys.modules:
    hvac_stub = types.ModuleType("hvac")
    hvac_stub.Client = mock.MagicMock
    sys.modules["hvac"] = hvac_stub
if "snowflake.connector" not in sys.modules:
    snowflake_stub = types.ModuleType("snowflake")
    snowflake_stub.__path__ = []
    connector_stub = types.ModuleType("snowflake.connector")
    connector_stub.DictCursor = object()
    connector_stub.connect = mock.MagicMock()
    snowflake_stub.connector = connector_stub
    sys.modules["snowflake"] = snowflake_stub
    sys.modules["snowflake.connector"] = connector_stub
if "feedparser" not in sys.modules:
    feedparser_stub = types.ModuleType("feedparser")
    feedparser_stub.parse = mock.MagicMock(return_value={})
    sys.modules["feedparser"] = feedparser_stub

import adoptiq_backend as backend


class _Cursor:
    def close(self) -> None:
        return None


class _Context:
    def cursor(self) -> _Cursor:
        return _Cursor()


@pytest.mark.parametrize(
    ("fetcher_name", "owner_column"),
    [
        ("fetch_csconsole_action_plans", "BU_NAME"),
        ("fetch_csconsole_customer_pulse", "BU_NAME"),
        ("fetch_csconsole_success_priorities", "RELATED_CUSTOMER__C"),
        ("fetch_csconsole_adoption_barriers", "CUSTOMER_NAME"),
    ],
)
def test_csconsole_fetchers_preserve_same_id_under_two_customers(
    monkeypatch: pytest.MonkeyPatch,
    fetcher_name: str,
    owner_column: str,
) -> None:
    """Transport adapters cannot choose one customer before quarantine."""

    variants = [
        ("SHARED-1", "Alpha Customer"),
        ("SHARED-1", "Beta Customer"),
    ]
    description = [("ID",), (owner_column,)]
    monkeypatch.setattr(backend, "is_table_blocked", lambda _table: False)
    fetcher = getattr(backend, fetcher_name)

    observed = []
    for returned_rows in (variants, list(reversed(variants))):
        monkeypatch.setattr(
            backend,
            "_execute_in_chunks",
            lambda *_args, _rows=returned_rows, **_kwargs: (_rows, description),
        )
        frame = fetcher(_Context(), ["scope-1"], 90)
        assert list(frame.columns) == ["ID", owner_column]
        assert len(frame) == 2
        observed.append(
            sorted(zip(frame["ID"].tolist(), frame[owner_column].tolist()))
        )

    assert observed == [sorted(variants), sorted(variants)]


def _ab_source() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ID": "AB-EXPLICIT",
                "ACCOUNT_ID_C": "ACCOUNT-EXPLICIT",
                "CUSTOMER_NAME": "Source Customer",
                "SUBJECT_C": "Explicit owner",
            },
            {
                "ID": "AB-FILL",
                "ACCOUNT_ID_C": "ACCOUNT-FILL",
                "CUSTOMER_NAME": None,
                "SUBJECT_C": "Safe fill",
            },
            {
                "ID": "AB-AMBIGUOUS",
                "ACCOUNT_ID_C": "ACCOUNT-AMBIGUOUS",
                "CUSTOMER_NAME": None,
                "SUBJECT_C": "Must fail closed",
            },
        ]
    )


def _account_mapping() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ACCOUNT_ID_C": "ACCOUNT-EXPLICIT",
                "SUBSCRIPTION_ID": "SUB-0",
                "BU_NAME": "Lookup Alpha",
            },
            {
                "ACCOUNT_ID_C": "ACCOUNT-EXPLICIT",
                "SUBSCRIPTION_ID": "SUB-0B",
                "BU_NAME": "Lookup Beta",
            },
            {
                "ACCOUNT_ID_C": "ACCOUNT-FILL",
                "SUBSCRIPTION_ID": "SUB-1",
                "BU_NAME": "Filled Customer",
            },
            {
                "ACCOUNT_ID_C": "ACCOUNT-FILL",
                "SUBSCRIPTION_ID": "SUB-1B",
                "BU_NAME": "Filled Customer",
            },
            {
                "ACCOUNT_ID_C": "ACCOUNT-AMBIGUOUS",
                "SUBSCRIPTION_ID": "SUB-2",
                "BU_NAME": "Alpha Customer",
            },
            {
                "ACCOUNT_ID_C": "ACCOUNT-AMBIGUOUS",
                "SUBSCRIPTION_ID": "SUB-3",
                "BU_NAME": "Beta Customer",
            },
        ]
    )


def test_prepare_ab_preserves_explicit_fills_unambiguous_and_fails_closed() -> None:
    prepared = backend._prepare_ab(_ab_source(), _account_mapping())
    owners = prepared.set_index("ID")["customer_name"].to_dict()

    assert owners == {
        "AB-EXPLICIT": "Source Customer",
        "AB-FILL": "Filled Customer",
        "AB-AMBIGUOUS": "Unknown",
    }
    assert "CUSTOMER_NAME" not in prepared.columns
    assert not any(column.startswith("_ownership") for column in prepared.columns)

    diagnostics = prepared.attrs["customer_ownership_resolution"]
    assert diagnostics["explicit_owner_rows"] == 1
    assert diagnostics["lookup_filled_rows"] == 1
    assert diagnostics["ambiguous_mapping_rows"] == 2
    assert diagnostics["unresolved_ambiguous_rows"] == 1
    assert diagnostics["source_lookup_disagreement_rows"] == 1
    assert diagnostics["ambiguous_mapping_keys"] == [
        "ACCOUNT-AMBIGUOUS",
        "ACCOUNT-EXPLICIT",
    ]
    assert prepared.attrs["partial_data_warnings"]


def test_prepare_ab_mapping_resolution_is_reorder_deterministic() -> None:
    forward = backend._prepare_ab(_ab_source(), _account_mapping())
    reversed_mapping = backend._prepare_ab(
        _ab_source().iloc[::-1].reset_index(drop=True),
        _account_mapping().iloc[::-1].reset_index(drop=True),
    )

    def _owners(frame: pd.DataFrame) -> dict[str, str]:
        return frame.set_index("ID")["customer_name"].sort_index().to_dict()

    assert _owners(forward) == _owners(reversed_mapping)
    assert (
        forward.attrs["customer_ownership_resolution"]
        == reversed_mapping.attrs["customer_ownership_resolution"]
    )


def _csone_source() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SR Number": "CASE-EXPLICIT",
                "Subscription ID": "SUB-EXPLICIT",
                "Customer Name": "Source Customer",
                "Title": "Explicit owner",
                "Case Status": "Open",
                "Priority": "P2",
            },
            {
                "SR Number": "CASE-FILL",
                "Subscription ID": "SUB-FILL",
                "Customer Name": None,
                "Title": "Safe fill",
                "Case Status": "Open",
                "Priority": "P3",
            },
            {
                "SR Number": "CASE-AMBIGUOUS",
                "Subscription ID": "SUB-AMBIGUOUS",
                "Customer Name": None,
                "Title": "Must fail closed",
                "Case Status": "Open",
                "Priority": "P3",
            },
        ]
    )


def _subscription_mapping() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"SUBSCRIPTION_ID": "SUB-EXPLICIT", "BU_NAME": "Lookup Alpha"},
            {"SUBSCRIPTION_ID": "SUB-EXPLICIT", "BU_NAME": "Lookup Beta"},
            {"SUBSCRIPTION_ID": "SUB-FILL", "BU_NAME": "Filled Customer"},
            {"SUBSCRIPTION_ID": "SUB-FILL", "BU_NAME": "Filled Customer"},
            {"SUBSCRIPTION_ID": "SUB-AMBIGUOUS", "BU_NAME": "Alpha Customer"},
            {"SUBSCRIPTION_ID": "SUB-AMBIGUOUS", "BU_NAME": "Beta Customer"},
        ]
    )


def test_prepare_csone_preserves_explicit_fills_unambiguous_and_fails_closed() -> None:
    prepared = backend._prepare_csone(_csone_source(), _subscription_mapping())
    owners = prepared.set_index("SR Number")["customer_name"].to_dict()

    assert owners == {
        "CASE-EXPLICIT": "Source Customer",
        "CASE-FILL": "Filled Customer",
        "CASE-AMBIGUOUS": "Unknown",
    }
    assert "Customer Name" not in prepared.columns
    assert not any(column.startswith("_ownership") for column in prepared.columns)

    diagnostics = prepared.attrs["customer_ownership_resolution"]
    assert diagnostics["explicit_owner_rows"] == 1
    assert diagnostics["lookup_filled_rows"] == 1
    assert diagnostics["ambiguous_mapping_rows"] == 2
    assert diagnostics["unresolved_ambiguous_rows"] == 1
    assert diagnostics["source_lookup_disagreement_rows"] == 1
    assert diagnostics["ambiguous_mapping_keys"] == [
        "SUB-AMBIGUOUS",
        "SUB-EXPLICIT",
    ]
    assert prepared.attrs["partial_data_warnings"]


def test_prepare_csone_mapping_resolution_is_reorder_deterministic() -> None:
    forward = backend._prepare_csone(_csone_source(), _subscription_mapping())
    reversed_mapping = backend._prepare_csone(
        _csone_source().iloc[::-1].reset_index(drop=True),
        _subscription_mapping().iloc[::-1].reset_index(drop=True),
    )

    def _owners(frame: pd.DataFrame) -> dict[str, str]:
        return frame.set_index("SR Number")["customer_name"].sort_index().to_dict()

    assert _owners(forward) == _owners(reversed_mapping)
    assert (
        forward.attrs["customer_ownership_resolution"]
        == reversed_mapping.attrs["customer_ownership_resolution"]
    )


def test_prepare_csone_quarantines_same_case_id_with_two_explicit_owners() -> None:
    source = pd.DataFrame(
        [
            {
                "SR Number": "SHARED-CASE",
                "Subscription ID": "SUB-A",
                "Customer Name": "Alpha Customer",
                "Title": "Alpha view",
                "Case Status": "Open",
            },
            {
                "SR Number": "SHARED-CASE",
                "Subscription ID": "SUB-B",
                "Customer Name": "Beta Customer",
                "Title": "Beta view",
                "Case Status": "Open",
            },
        ]
    )

    prepared = backend._prepare_csone(source, pd.DataFrame())

    assert prepared.empty
    ownership = prepared.attrs["cross_customer_id_conflicts"]
    assert ownership["conflicting_ids"] == ["SHARED-CASE"]
    assert ownership["quarantined_rows"] == 2
    assert prepared.attrs["customer_ownership_resolution"]["explicit_owner_rows"] == 2
