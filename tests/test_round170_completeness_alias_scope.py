"""Round 170: customer-scoped completeness audit uses alias SSoT.

The audit previously compared normalized customer strings with ``!=``.
That treated bundled alias siblings as an out-of-scope leak even when
they name the same organization. Route through
``data_normalization.customer_names_match`` so the completeness gate
matches the Round 132 join contract.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from data_normalization import invalidate_customer_alias_registry_cache
from report_completeness_audit import audit_source_data_frames
from source_shape_utils import assert_in_source


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_alias_registry():
    invalidate_customer_alias_registry_cache()
    yield
    invalidate_customer_alias_registry_cache()


def _customer_scope_sheets(scope_value: str, customer_name: str) -> dict[str, pd.DataFrame]:
    return {
        "Report_Info": pd.DataFrame(
            [
                {"Item": "Scope_Type", "Value": "customer"},
                {"Item": "Scope_Value", "Value": scope_value},
            ]
        ),
        "Action_Plans": pd.DataFrame(
            [
                {
                    "ID": "AP-SCOPE-1",
                    "Customer Name": customer_name,
                    "AdoptIQ_Status_Bucket": "Open",
                    "AdoptIQ_Data_Quality": "OK",
                }
            ]
        ),
    }


def test_completeness_audit_uses_customer_names_match() -> None:
    source = PROJECT_ROOT.joinpath("report_completeness_audit.py").read_text(encoding="utf-8")
    assert_in_source(source, "customer_names_match(raw_customer, raw_scope_value)", label="audit")
    assert_in_source(source, "# Round 170:", label="audit")
    assert "outside = customer != scope_value" not in source


def test_completeness_audit_accepts_alias_sibling_customer_name() -> None:
    audit = audit_source_data_frames(
        _customer_scope_sheets("NYU LANGONE HEALTH SYSTEMS", "NYU MEDICAL CENTER")
    )
    assert audit["customer_scope_mismatch_rows"] == 0
    assert not any("out-of-scope" in error for error in audit["errors"])


def test_completeness_audit_still_rejects_unrelated_customer_name() -> None:
    audit = audit_source_data_frames(
        _customer_scope_sheets("NYU LANGONE HEALTH SYSTEMS", "CISCO SYSTEMS")
    )
    assert audit["ok"] is False
    assert audit["customer_scope_mismatch_rows"] >= 1
    assert audit["customer_scope_mismatch_by_sheet"]["Action_Plans"] >= 1


def test_completeness_audit_same_normalized_name_still_matches() -> None:
    audit = audit_source_data_frames(
        _customer_scope_sheets("Acme Corporation", "ACME CORPORATION")
    )
    assert audit["customer_scope_mismatch_rows"] == 0
