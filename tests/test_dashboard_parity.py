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

