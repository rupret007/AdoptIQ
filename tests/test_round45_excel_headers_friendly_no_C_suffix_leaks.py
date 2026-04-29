"""Round 45 / Phase 5 regression: ``apply_export_schema`` MUST drop raw
Snowflake ``_C`` / ``__C`` / ``BU_NAME`` / ``customer_name`` headers
from every visible Excel column header across all 3 reports
(Comprehensive, Renewal, Leader).

The 2026-04-28 Build-20 audit found:
    * Comprehensive ``AB_Detail_All`` / ``CSConsole_Customer_Pulse``: 9 raw headers
    * Renewal ``Customer_Adoption_Barriers`` / ``Customer_Action_Plans``: 16 raw headers
    * Leader ``Action_Plans`` / ``Adoption_Barriers`` / ``Customer_Pulse`` / ``Subscriptions``: 21 raw headers

Round 45 / Phase 5 introduces ``_FRIENDLY_HEADER_LABELS`` -- the
single SSoT cross-sheet rename map -- and applies it as the LAST step
of ``apply_export_schema`` so:
    * the curated allowlists keep working unchanged (they still use raw names),
    * new sheets automatically inherit the friendly labels,
    * the visible Excel column header drops the raw ``_C`` suffix.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from report_export_schema import (  # noqa: E402
    apply_export_schema,
    friendly_header,
    _FRIENDLY_HEADER_LABELS,
)


_RAW_LEAK_NAMES = (
    "BU_NAME",
    "customer_name",
    "ACCOUNT_ID_C",
    "AB_STATUS_C",
    "STATUS_C",
    "SEVERITY_C",
    "OPEN_DATE_C",
    "DUE_DATE_C",
    "CLOSED_DATE_C",
    "COMMENTS_C",
    "COMMENTS__C",
    "PULSE_RATING__C",
)


def test_friendly_header_helper_exists() -> None:
    """The Phase 5 helper must be exposed for callers that need to map
    one header at a time (e.g. cell comments, legends)."""
    assert callable(friendly_header)
    assert friendly_header("BU_NAME") == "Customer Name"
    assert friendly_header("ACCOUNT_ID_C") == "Account ID"
    assert friendly_header("SEVERITY_C") == "Severity"
    assert friendly_header("unknown_col") == "unknown_col"
    assert friendly_header(None) == ""


def test_friendly_header_map_covers_audit_leak_set() -> None:
    """Every raw header from the 2026-04-28 audit MUST be in the
    friendly-label SSoT.  If a new leak appears the test fails so
    Round 45 doesn't ship a partial fix."""
    for raw in _RAW_LEAK_NAMES:
        assert raw in _FRIENDLY_HEADER_LABELS, (
            f"Round 45 / Phase 5 regression: raw header {raw!r} from "
            f"the Build-20 audit is not mapped to a friendly label."
        )


@pytest.mark.parametrize(
    "sheet_name",
    [
        "Adoption_Barriers",
        "Action_Plans",
        "Customer_Adoption_Barriers",
        "Customer_Action_Plans",
        "Customer_Pulse",
        "Subscriptions",
        "AB_Detail_All",
        "CSConsole_Customer_Pulse",
    ],
)
def test_apply_export_schema_drops_raw_C_headers(sheet_name: str) -> None:
    """For each sheet the audit found leaks in, ``apply_export_schema``
    MUST replace every raw header in ``_RAW_LEAK_NAMES`` with the
    friendly label."""
    df = pd.DataFrame(
        {
            "BU_NAME": ["Acme"],
            "customer_name": ["Acme Inc"],
            "ACCOUNT_ID_C": ["001"],
            "AB_STATUS_C": ["Open"],
            "STATUS_C": ["Open"],
            "SEVERITY_C": ["P1"],
            "OPEN_DATE_C": ["2026-01-01"],
            "DUE_DATE_C": ["2026-02-01"],
            "CLOSED_DATE_C": [None],
            "COMMENTS_C": ["hello"],
            "COMMENTS__C": ["world"],
            "PULSE_RATING__C": [3],
        }
    )

    out = apply_export_schema(df, sheet_name=sheet_name)
    cols = list(out.columns)

    for raw in _RAW_LEAK_NAMES:
        assert raw not in cols, (
            f"Round 45 / Phase 5 regression: sheet={sheet_name!r} still "
            f"emits raw header {raw!r}.  Friendly label expected: "
            f"{friendly_header(raw)!r}."
        )


def test_friendly_rename_does_not_overwrite_existing_friendly_column() -> None:
    """If the upstream DataFrame already has both ``BU_NAME`` and
    ``Customer Name``, the rename must drop ``BU_NAME`` (or skip the
    rename) WITHOUT clobbering the existing friendly column.  This
    pins the collision-detection branch of the rename map."""
    df = pd.DataFrame(
        {
            "BU_NAME": ["A"],
            "Customer Name": ["B"],
            "OPEN_DATE_C": ["2026-01-01"],
        }
    )
    out = apply_export_schema(df, sheet_name="Subscriptions")
    cols = list(out.columns)
    # Must not have duplicate ``Customer Name`` columns.
    assert cols.count("Customer Name") <= 1, (
        "Round 45 / Phase 5 regression: friendly rename must skip a "
        "rename that would collide with an existing friendly column."
    )


def test_apply_export_schema_smoke_renewal_subscriptions() -> None:
    """End-to-end smoke for the renewal Subscriptions sheet -- the
    most-leaked sheet in the Build-20 audit (BU_NAME, ACCOUNT_ID_C,
    plus ARR / status columns)."""
    df = pd.DataFrame(
        {
            "BU_NAME": ["Acme", "Beta"],
            "ACCOUNT_ID_C": ["001", "002"],
            "PRODUCT_ARR_C": [100000, 250000],
            "SERVICE_ARR_C": [50000, 75000],
            "STATUS_C": ["Active", "Renewing"],
        }
    )
    out = apply_export_schema(df, sheet_name="Subscriptions")
    cols = list(out.columns)

    assert "Customer Name" in cols
    assert "Account ID" in cols
    assert "Product ARR" in cols
    assert "Service ARR" in cols
    assert "Status" in cols

    # And no raw leaks.
    for raw in ("BU_NAME", "ACCOUNT_ID_C", "PRODUCT_ARR_C", "SERVICE_ARR_C", "STATUS_C"):
        assert raw not in cols
