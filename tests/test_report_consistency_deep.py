import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_normalization import add_case_lifecycle_fields
from report_consistency import validate_report_consistency


def test_dashboard_metric_parity_detects_customer_and_priority_mismatches():
    csone = add_case_lifecycle_fields(
        pd.DataFrame(
            [
                {"customer_name": "Acme", "Severity": "P1", "Transaction ID": "BEMS12345"},
                {"customer_name": "Beta", "Severity": "P2", "Transaction ID": ""},
            ]
        )
    )
    ab = pd.DataFrame([{"customer_name": "Acme", "sub_technology": "Contact Center"}])
    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone,
        portfolio_metrics={
            "total_barriers": 1,
            "total_cases": 2,
            "bems_count": 1,
            "total_customers": 99,
            "critical_p1": 0,
            "high_p2": 0,
        },
    )
    assert result["is_valid"] is False
    assert any("total_customers" in err for err in result["errors"])
    assert any("critical_p1" in err for err in result["errors"])
    assert any("high_p2" in err for err in result["errors"])


def test_other_unknown_ratio_and_defect_linkage_warnings():
    ab = pd.DataFrame(
        [
            {"customer_name": "Acme", "sub_technology": "Other/Unknown"},
            {"customer_name": "Acme", "sub_technology": "Unknown"},
            {"customer_name": "Acme", "sub_technology": "Other/Unknown"},
        ]
    )
    csone = add_case_lifecycle_fields(pd.DataFrame([{"customer_name": "Acme"}]))
    defects = {
        "csc_ids": ["CSCAAA111", "CSCBBB222"],
        "bems_ids": ["BEMS777777"],
        "defect_by_customer": {
            "Unmapped Customer": ["CSCAAA111"],
        },
    }
    result = validate_report_consistency(ab_df=ab, csone_df=csone, defects=defects)
    assert result["is_valid"] is True
    assert any("Other/Unknown ratio is high" in w for w in result["warnings"])
    assert any("not in normalized customer set" in w for w in result["warnings"])
    assert any("missing customer linkage" in w for w in result["warnings"])
    assert "CSCBBB222" in result["metrics"]["unlinked_defects"]
    assert "BEMS777777" in result["metrics"]["unlinked_defects"]


def test_priority_metrics_use_normalized_exact_p1_p2():
    csone = pd.DataFrame(
        [
            {"customer_name": "Acme", "Severity": "P1"},
            {"customer_name": "Acme", "Severity": "P10"},
            {"customer_name": "Acme", "Severity": "Sev-2"},
        ]
    )
    result = validate_report_consistency(
        ab_df=pd.DataFrame([{"customer_name": "Acme"}]),
        csone_df=csone,
    )
    assert result["metrics"]["critical_p1"] == 1
    assert result["metrics"]["high_p2"] == 1


def test_customer_universe_does_not_override_narrow_total_customers():
    """Round 49 / F-COMP-CONSIST-WIDTH-MISMATCH: ``customer_universe``
    is telemetry-only.  The validator's headline ``total_customers``
    parity gate ALWAYS uses the narrow ``count_customers(ab, csone,
    pulse)`` shape so it cannot disagree with the Word headline /
    Excel Summary row, which both derive from the same narrow shape.

    Pre-R49 contract: passing ``customer_universe={Acme, Beta, Gamma}``
    overrode ``metrics['total_customers']`` to 3, which in the
    comprehensive report path silently widened the parity check to
    52 while Word + Excel reported 38, blocking Build25 with a
    false-positive mismatch.

    Post-R49 contract: ``customer_universe`` is surfaced as the
    diagnostic ``metrics['customer_universe_total']`` while the
    parity headline stays narrow.
    """
    csone = add_case_lifecycle_fields(
        pd.DataFrame(
            [
                {"customer_name": "Acme", "Severity": "P2"},
            ]
        )
    )
    ab = pd.DataFrame([{"customer_name": "Acme", "sub_technology": "Cisco UCCE"}])
    provided_universe = {"Acme", "Beta", "Gamma"}
    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone,
        portfolio_metrics={
            "total_barriers": 1,
            "total_cases": 1,
            "bems_count": 0,
            "total_customers": 1,
            "critical_p1": 0,
            "high_p2": 1,
        },
        customer_universe=provided_universe,
    )
    assert result["is_valid"] is True, result["errors"]
    assert result["metrics"]["total_customers"] == 1, (
        "R49 contract: total_customers parity headline is the narrow "
        "count_customers(ab, csone, pulse) shape, not customer_universe."
    )
    assert result["metrics"]["customer_universe_total"] == 3, (
        "R49 contract: customer_universe is surfaced as a diagnostic "
        "metric so callers can still see the wider iteration roster."
    )
    assert not any("total_customers" in err for err in result["errors"])


def test_other_unknown_warning_suppressed_when_tech_signal_sparse():
    ab = pd.DataFrame(
        [
            {
                "customer_name": "Acme",
                "sub_technology": "Other/Unknown",
                "SUB_TECHNOLOGY_C": "",
                "TECHNOLOGY_C": "",
                "CSS_PRE_UNLINK_TECHNOLOGY_NAME_C": "",
                "PRODUCT_NAME_C": "",
                "PRODUCT_C": "",
            },
            {
                "customer_name": "Beta",
                "sub_technology": "Unknown",
                "SUB_TECHNOLOGY_C": "",
                "TECHNOLOGY_C": "",
                "CSS_PRE_UNLINK_TECHNOLOGY_NAME_C": "",
                "PRODUCT_NAME_C": "",
                "PRODUCT_C": "",
            },
        ]
    )
    csone = add_case_lifecycle_fields(pd.DataFrame([{"customer_name": "Acme"}]))
    result = validate_report_consistency(ab_df=ab, csone_df=csone)
    assert result["is_valid"] is True
    assert not any("Other/Unknown ratio is high" in w for w in result["warnings"])
    assert result["metrics"]["technology_signal_ratio"] == 0.0


def test_legacy_priority_keys_are_checked_for_parity():
    csone = add_case_lifecycle_fields(
        pd.DataFrame(
            [
                {"customer_name": "Acme", "Severity": "P1"},
                {"customer_name": "Acme", "Severity": "P2"},
            ]
        )
    )
    result = validate_report_consistency(
        ab_df=pd.DataFrame([{"customer_name": "Acme"}]),
        csone_df=csone,
        portfolio_metrics={
            "total_barriers": 1,
            "total_cases": 2,
            "bems_count": 0,
            "p1_cases": 0,
            "p2_cases": 0,
        },
    )
    assert result["is_valid"] is False
    assert any("critical_p1" in err for err in result["errors"])
    assert any("high_p2" in err for err in result["errors"])


def test_strict_mode_raises_on_consistency_errors():
    csone = add_case_lifecycle_fields(pd.DataFrame([{"customer_name": "Acme", "Severity": "P1"}]))
    ab = pd.DataFrame([{"customer_name": "Acme", "sub_technology": "Cisco UCCE"}])
    try:
        validate_report_consistency(
            ab_df=ab,
            csone_df=csone,
            portfolio_metrics={"total_barriers": 0, "total_cases": 1, "bems_count": 0},
            strict_mode=True,
        )
        assert False, "strict_mode should raise when errors exist"
    except ValueError as exc:
        assert "total_barriers" in str(exc)

