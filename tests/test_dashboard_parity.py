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
