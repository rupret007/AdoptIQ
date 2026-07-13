from pathlib import Path

import pandas as pd

from report_consistency import validate_report_consistency
from report_utils import format_inline_source, format_metric_with_source
from risk_scoring import compute_customer_risk_profile


def test_inline_source_formatter_includes_required_parts():
    citation = format_inline_source(
        "Support Cases (TAC)",
        fields=["Case #", "Severity"],
        record_id="SR123456",
    )
    assert citation.startswith("[Source:")
    assert "Field(s): Case #, Severity" in citation
    assert "Record: SR123456" in citation
    assert "Verification:" in citation


def test_metric_formatter_appends_inline_source():
    text = format_metric_with_source(
        "Support Cases",
        12,
        "Support Cases (TAC)",
        fields=["Case #"],
    )
    assert text.startswith("Support Cases: 12")
    assert "[Source:" in text


def test_risk_profile_outputs_source_backed_facts():
    profile = compute_customer_risk_profile(
        customer_name="Acme Corp",
        customer_ab=pd.DataFrame(
            [{"customer_name": "Acme Corp", "SEVERITY_C": "High", "AB_STATUS_C": "Open"}]
        ),
        customer_csone=pd.DataFrame(
            [{"customer_name": "Acme Corp", "Severity": "P1", "Status": "Open", "Transaction ID": "BEMS12345"}]
        ),
        customer_pulse=pd.DataFrame([{"customer_name": "Acme Corp", "PULSE_RATING__C": "Poor"}]),
        customer_action_plans=pd.DataFrame([{"customer_name": "Acme Corp", "STATUS_C": "Open"}]),
        customer_subs=pd.DataFrame(),
        ext_incidents=None,
    )
    assert profile["risk_factors"]
    assert all("[Source:" in factor for factor in profile["risk_factors"])
    assert all("[Source:" in finding for finding in profile["key_findings"])


def test_risk_profile_excludes_backfilled_pulse_from_scoring():
    profile = compute_customer_risk_profile(
        customer_name="Acme Corp",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(
            [
                {"PULSE_RATING__C": "Poor", "PULSE_BACKFILL": True},
            ]
        ),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame(),
        ext_incidents=None,
    )
    pulse_component = profile["components"]["customer_pulse"]
    engagement_component = profile["components"]["engagement"]
    assert pulse_component["details"]["count"] == 0
    assert pulse_component["details"]["backfill_excluded_count"] == 1
    assert engagement_component["details"]["total_activity"] == 0


def test_consistency_validator_flags_missing_inline_sources():
    result = validate_report_consistency(
        ab_df=pd.DataFrame(),
        csone_df=pd.DataFrame(),
        factual_claims=["Fact without source", "Another one"],
    )
    assert result["is_valid"] is False
    assert any("missing inline source attribution" in err.lower() for err in result["errors"])


def test_report_files_include_metric_source_backing_sections():
    root = Path(__file__).resolve().parent.parent
    compact_src = root.joinpath("compact_report_formatter.py").read_text(encoding="utf-8")
    exec_src = root.joinpath("executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert "Metric source backing" in compact_src
    assert "Metric source backing" in exec_src


def test_compact_formatter_enforces_inline_source_for_summary_claims():
    root = Path(__file__).resolve().parent.parent
    compact_src = root.joinpath("compact_report_formatter.py").read_text(encoding="utf-8")
    assert "_ensure_inline_source_claim(concern" in compact_src
    assert "_ensure_inline_source_claim(action" in compact_src


def test_app_and_executive_paths_wrap_factual_claims_with_sources():
    root = Path(__file__).resolve().parent.parent
    app_src = root.joinpath("app_simple.py").read_text(encoding="utf-8")
    exec_src = root.joinpath("executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert "_ensure_inline_source_claim(" in app_src
    assert "validate_report_consistency(" in exec_src
