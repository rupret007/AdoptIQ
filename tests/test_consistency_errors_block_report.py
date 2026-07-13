"""Phase 5 / Phase 1.2 regression test.

Compact and comprehensive reports must REFUSE to write a docx when
``validate_report_consistency`` reports errors, unless the operator
explicitly opts out via ``ADOPTIQ_NONSTRICT_COMPACT=1`` /
``ADOPTIQ_NONSTRICT_CONSISTENCY=1``.  This test verifies the
fail-loud default by patching the validator and asserting the
compact report path raises ``ValueError`` rather than silently
writing a misleading file.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

import compact_report_formatter as crf


@pytest.fixture
def small_inputs():
    ab_df = pd.DataFrame({
        "customer_name": ["AcmeCorp"],
        "BU_NAME": ["AcmeCorp"],
        "SUBJECT_C": ["Issue"],
        "SEVERITY_C": ["High"],
        "AB_STATUS_C": ["Open"],
        "ID": ["AB001"],
    })
    csone_df = pd.DataFrame({
        "customer_name": ["AcmeCorp"],
        "Customer Name": ["AcmeCorp"],
        "Severity": ["P2"],
        "SR Number": ["TAC001"],
        "Case Status": ["Open"],
        "Date/Time Opened": ["2026-01-01"],
        "Date/Time Closed": [""],
    })
    return ab_df, csone_df


def test_compact_report_blocks_on_consistency_errors(monkeypatch, small_inputs, tmp_path):
    """Phase 1.2: by default, an inconsistent compact report must
    raise; it must not silently write a misleading docx."""
    monkeypatch.delenv("ADOPTIQ_NONSTRICT_COMPACT", raising=False)
    ab_df, csone_df = small_inputs

    fake_result = {
        "is_valid": False,
        "errors": ["Total cases (10) does not match P1+P2+P3+P4 (8)."],
        "warnings": [],
        "metrics": {},
    }
    out_path = tmp_path / "compact.docx"

    with patch.object(crf, "validate_report_consistency", return_value=fake_result):
        with pytest.raises(ValueError) as exc:
            crf.create_compact_executive_report(
                analysis_id="cons-block-test",
                manager="QA Manager",
                technology="WebexCC",
                days=30,
                ab_data=ab_df,
                csone_data=csone_df,
                ai_insights={},
                output_path=str(out_path),
            )
    assert "consistency" in str(exc.value).lower(), (
        "The raised ValueError must clearly mention consistency so the "
        "operator knows why the report was blocked."
    )
    assert not out_path.exists(), (
        "No docx must be written when consistency checks fail in strict mode."
    )


def test_compact_report_can_be_forced_through_with_env_opt_out(
    monkeypatch, small_inputs, tmp_path
):
    """Phase 1.2 opt-out: with ADOPTIQ_NONSTRICT_COMPACT=1 the
    operator accepts the risk and the report writes anyway."""
    monkeypatch.setenv("ADOPTIQ_NONSTRICT_COMPACT", "1")
    ab_df, csone_df = small_inputs
    fake_result = {
        "is_valid": False,
        "errors": ["totals disagree"],
        "warnings": [],
        "metrics": {},
    }
    out_path = tmp_path / "compact_opt_out.docx"

    with patch.object(crf, "validate_report_consistency", return_value=fake_result):
        try:
            crf.create_compact_executive_report(
                analysis_id="cons-opt-out-test",
                manager="QA Manager",
                technology="WebexCC",
                days=30,
                ab_data=ab_df,
                csone_data=csone_df,
                ai_insights={},
                output_path=str(out_path),
            )
        except ValueError as exc:
            if "consistency" in str(exc).lower():
                pytest.fail(
                    "ADOPTIQ_NONSTRICT_COMPACT=1 must allow the report to "
                    f"render despite consistency errors, got: {exc}"
                )
            # Some other (unrelated) ValueError is acceptable for this
            # test's intent; we only check the consistency-block path.
