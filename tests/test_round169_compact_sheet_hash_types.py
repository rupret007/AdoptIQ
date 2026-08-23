"""Round 169 Compact sheet hashes preserve exact OOXML scalar types."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import decision_report_delivery as delivery
import report_source_parity as source_parity
from canonical_report_adapter import canonicalize_legacy_artifacts
from tests.test_canonical_report_adapter import (
    AS_OF,
    _fake_chart_renderer,
    _write_legacy_pair,
)


def test_compact_mixed_boolean_family_facts_pass_actual_workbook_parity_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boolean family facts must not be retyped as numeric 0/1 while hashing."""

    monkeypatch.setattr(delivery, "_render_chart_image", _fake_chart_renderer)
    word_path, workbook_path, _names = _write_legacy_pair(
        tmp_path,
        family="compact",
        marker="MIXED-BOOL-HASH",
    )
    presentation = {
        # Real Compact workbooks mix booleans and numbers in the same family-
        # fact value column. pandas' Excel inference previously retyped these
        # cells and falsely raised sheet_sha256_mismatch after a valid write.
        "Executive_Dashboard": pd.DataFrame(
            [
                {"Metric": "Customer Flag", "Value": True},
                {"Metric": "Open Accounts", "Value": 0},
            ]
        ),
        "High_Risk_Customers": pd.DataFrame(
            [{"Customer": "Acme Corporation", "Risk": "Low"}]
        ),
        "Critical_Adoption_Barriers": pd.DataFrame(
            [{"ID": "AB-MIXED-BOOL-HASH", "Severity": "P1"}]
        ),
        "Escalated_Cases": pd.DataFrame(
            [{"SR Number": "CASE-MIXED-BOOL-HASH", "Severity": "P1"}]
        ),
    }
    with pd.ExcelWriter(
        workbook_path,
        engine="openpyxl",
        mode="a",
        if_sheet_exists="replace",
    ) as writer:
        for sheet_name, frame in presentation.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)

    result = canonicalize_legacy_artifacts(
        word_path,
        workbook_path,
        report_type="Compact",
        manager_name="Local Fixture Manager",
        technology="All",
        scope_type="team",
        scope_value="Entire team",
        days=90,
        as_of=AS_OF,
        data_as_of_utc=AS_OF,
    )

    parity_signature = source_parity.build_workbook_parity_signature(
        Path(result["source_data_path"]),
        allowed_root=tmp_path,
    )

    assert parity_signature["metadata"]["report_family"] == "compact"
    assert parity_signature["required_sheets"] == list(
        source_parity.PARITY_SHEET_NAMES
    )
