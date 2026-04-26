"""Round 3 / Phase 4.4 regression test.

Leader Report's "Analysis Period: <start> - <end>" header must be
computed off the UTC calendar so it lines up with Snowflake's
``CURRENT_DATE()`` (which is UTC) and with the SQL window the report
actually queries. Using ``datetime.now()`` (local) could print a date
range that's off by one day from the data the report contains.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_analysis_period_header_uses_utc():
    src = (PROJECT_ROOT / "leader_report_generator.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    # The Analysis Period header block must use datetime.utcnow() for
    # the end date and label the Generated timestamp as UTC.
    idx = src.find("Analysis Period:")
    assert idx >= 0
    window = src[max(0, idx - 600) : idx + 800]
    assert "datetime.utcnow()" in window, (
        "Leader Analysis Period block must derive end_date from datetime.utcnow()"
    )
    assert "UTC" in window, (
        "Leader Generated timestamp must be explicitly labeled as UTC"
    )
