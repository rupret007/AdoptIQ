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

