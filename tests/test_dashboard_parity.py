import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from compact_report_formatter import CompactReportFormatter


def test_compact_dashboard_uses_canonical_metric_counts():
    formatter = CompactReportFormatter()
    ab_data = pd.DataFrame([{"customer_name": "Acme Corp"}])
    csone_data = pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "Severity": "P1", "Transaction ID": "BEMS12345"},
            {"customer_name": "Beta Inc", "Severity": "P2", "Transaction ID": ""},
            {"customer_name": "Beta Inc", "Severity": "P3", "Transaction ID": ""},
        ]
    )

    formatter.add_at_a_glance_dashboard(ab_data, csone_data)

    table = formatter.doc.tables[0]
    values = [table.rows[1].cells[i].text for i in range(5)]
    assert values == ["2", "3", "1", "1", "1"]


def test_compact_dashboard_priority_counts_use_exact_p1_p2_labels():
    formatter = CompactReportFormatter()
    ab_data = pd.DataFrame([{"BU_NAME": "Acme Corp"}])
    csone_data = pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "Severity": "P1", "Transaction ID": ""},
            {"customer_name": "Acme Corp", "Severity": "P10", "Transaction ID": ""},
            {"customer_name": "Acme Corp", "Severity": "P2", "Transaction ID": ""},
        ]
    )

    formatter.add_at_a_glance_dashboard(ab_data, csone_data, total_customers_override=1)

    table = formatter.doc.tables[0]
    values = [table.rows[1].cells[i].text for i in range(5)]
    assert values == ["1", "3", "1", "1", "0"]


def test_compact_data_citations_priority_count_uses_exact_p1_or_critical():
    formatter = CompactReportFormatter()
    ab_data = pd.DataFrame([{"customer_name": "Acme Corp"}])
    csone_data = pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "Severity": "P1"},
            {"customer_name": "Acme Corp", "Severity": "P10"},
            {"customer_name": "Acme Corp", "Severity": "Critical"},
        ]
    )

    formatter.add_data_citations_section(ab_data, csone_data)

    table = formatter.doc.tables[-1]
    p1_row = next((row for row in table.rows[1:] if row.cells[0].text == "P1/Critical Cases"), None)
    assert p1_row is not None
    assert p1_row.cells[1].text == "2"


def test_compact_data_citations_supports_severity_and_bu_name_columns():
    formatter = CompactReportFormatter()
    ab_data = pd.DataFrame([{"BU_NAME": "Acme Corp"}])
    csone_data = pd.DataFrame(
        [
            {"BU_NAME": "Acme Corp", "SEVERITY": "Critical"},
            {"BU_NAME": "Beta Inc", "SEVERITY": "P2"},
        ]
    )

    formatter.add_data_citations_section(ab_data, csone_data)

    table = formatter.doc.tables[-1]
    rows = {row.cells[0].text: row.cells[1].text for row in table.rows[1:]}
    assert rows["Unique Customers with Cases"] == "2"
    assert rows["P1/Critical Cases"] == "1"
    assert rows["Customers with Adoption Barriers"] == "1"


def test_common_problems_customers_are_theme_scoped():
    formatter = CompactReportFormatter()
    ab_data = pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "SUBJECT_C": "BEMS escalation issue", "DESCRIPTION_C": ""},
            {"customer_name": "Beta Inc", "SUBJECT_C": "General note without keyword", "DESCRIPTION_C": ""},
        ]
    )

    formatter.add_common_problems_section(ab_data, pd.DataFrame())

    text = "\n".join(p.text for p in formatter.doc.paragraphs)
    assert "- Customers Affected: Acme Corp" in text
    assert "- Customers Affected: Acme Corp, Beta Inc" not in text


def test_key_concerns_uses_normalized_case_priority():
    formatter = CompactReportFormatter()
    concerns = formatter._generate_key_concerns(
        pd.DataFrame(),
        pd.DataFrame(
            [
                {"BU_NAME": "Acme Corp", "SEVERITY": "Critical"},
                {"BU_NAME": "Acme Corp", "SEVERITY": "P2"},
            ]
        ),
    )
    assert any("1 P1 support cases" in concern for concern in concerns)


def test_immediate_actions_uses_normalized_case_priority():
    formatter = CompactReportFormatter()
    actions = formatter._generate_immediate_actions(
        pd.DataFrame(),
        pd.DataFrame(
            [
                {"BU_NAME": "Acme Corp", "SEVERITY": "Critical"},
                {"BU_NAME": "Acme Corp", "SEVERITY": "P2"},
            ]
        ),
        {},
    )
    assert any("1 P1 support cases" in action for action in actions)


def test_high_risk_customer_section_supports_bu_name_fallback():
    formatter = CompactReportFormatter()
    risk_data = {
        "Acme Corp": {
            "score": 7.0,
            "color": "Red",
            "category": "High Risk - Urgent Attention Needed",
            "risk_factors": [],
        }
    }
    ab_data = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme Corp",
                "SUBJECT_C": "Barrier from BU_NAME path",
                "AB_STATUS_C": "Open",
                "SEVERITY_C": "High",
            }
        ]
    )
    csone_data = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme Corp",
                "Transaction ID": "BEMS12345",
                "Severity": "P1",
                "Title": "Escalated issue",
                "Problem Description": "Break fix issue",
            }
        ]
    )

    formatter.add_high_risk_customers(ab_data, csone_data, risk_data)
    text = "\n".join(p.text for p in formatter.doc.paragraphs)
    assert "Barrier from BU_NAME path" in text
    assert "1 adoption barriers" in text


def test_critical_adoption_barriers_supports_severity_column_fallback():
    formatter = CompactReportFormatter()
    ab_data = pd.DataFrame(
        [
            {
                "customer_name": "Acme Corp",
                "SUBJECT_C": "Critical fallback severity",
                "AB_STATUS_C": "Open",
                "Severity": "Critical",
            }
        ]
    )

    formatter.add_critical_adoption_barriers(ab_data)
    text = "\n".join(p.text for p in formatter.doc.paragraphs)
    assert "Critical fallback severity" in text
