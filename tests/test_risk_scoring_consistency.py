import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_normalization import add_case_lifecycle_fields, detect_bems_mask
from report_consistency import validate_report_consistency
from risk_scoring import compute_customer_risk_profile


def test_case_lifecycle_and_type_classification():
    now = datetime.utcnow()
    csone = pd.DataFrame(
        [
            {
                "customer_name": "Acme Corp",
                "Title": "Provisioning request for feature enablement",
                "Problem Description": "Please turn on feature and setup license",
                "Status": "Open",
                "Date/Time Opened": (now - timedelta(days=10)).isoformat(),
                "Transaction ID": "BEMS12345",
                "Severity": "P2",
            },
            {
                "customer_name": "Acme Corp",
                "Title": "Service outage and technical error",
                "Problem Description": "Break fix needed due to defect",
                "Status": "Open",
                "Date/Time Opened": (now - timedelta(days=20)).isoformat(),
                "Severity": "P1",
            },
        ]
    )
    normalized = add_case_lifecycle_fields(csone)
    assert "open_age_days" in normalized.columns
    assert int(normalized["is_bems"].sum()) == 1
    assert "provisioning_request" in normalized["case_type_class"].tolist()
    assert "break_fix_technical" in normalized["case_type_class"].tolist()


def test_weighted_risk_profile_is_deterministic_and_not_default_high():
    profile_no_severity = compute_customer_risk_profile(
        customer_name="NoSeverity",
        customer_ab=pd.DataFrame([{"customer_name": "NoSeverity", "title": "Issue", "AB_STATUS_C": "Open"}]),
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame(),
        ext_incidents=None,
    )
    profile_with_severity = compute_customer_risk_profile(
        customer_name="WithSeverity",
        customer_ab=pd.DataFrame([{"customer_name": "WithSeverity", "title": "Issue", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open"}]),
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame(),
        ext_incidents=None,
    )
    assert profile_with_severity["risk_score_0_100"] > profile_no_severity["risk_score_0_100"]
    assert profile_no_severity["risk_score_0_100"] < 75  # no automatic critical default


def test_report_consistency_detects_metric_mismatches():
    csone = pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "Transaction ID": "BEMS001", "Status": "Open", "Date/Time Opened": datetime.utcnow().isoformat()},
            {"customer_name": "Acme Corp", "Transaction ID": "", "Status": "Open", "Date/Time Opened": datetime.utcnow().isoformat()},
        ]
    )
    csone_norm = add_case_lifecycle_fields(csone)
    ab = pd.DataFrame([{"customer_name": "Acme Corp", "SEVERITY_C": "High", "AB_STATUS_C": "Open"}])
    result = validate_report_consistency(
        ab_df=ab,
        csone_df=csone_norm,
        portfolio_metrics={"total_barriers": 0, "total_cases": 1, "bems_count": 0},
    )
    assert result["is_valid"] is False
    assert len(result["errors"]) >= 1


def test_bems_mask_uses_multiple_columns():
    df = pd.DataFrame(
        [
            {"customer_name": "A", "Transaction ID": "BEMS-999"},
            {"customer_name": "B", "bemscsc_refs": "BEMS123"},
            {"customer_name": "C", "Title": "engineering escalation required"},
        ]
    )
    mask = detect_bems_mask(df)
    assert int(mask.sum()) == 3

