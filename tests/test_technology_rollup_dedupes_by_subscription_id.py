"""Round 3 / Phase 3.4 regression test.

Leader report's ``_add_technology_breakdown`` must dedupe by
``SUBSCRIPTION_ID`` before counting per-CSSM technology rollups.
DSM emits multiple rows per subscription (line items, status
history); without dedup, a subscription with two DSM rows shows
twice in a CSSM's product totals and in the portfolio rollup.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_technology_breakdown_dedupes_by_subscription_id(tmp_path):
    try:
        from leader_report_generator import LeaderReportGenerator
        from docx import Document
    except Exception:
        pytest.skip("leader_report_generator or python-docx unavailable")

    # Use a stub ctx (the function under test never touches Snowflake).
    gen = LeaderReportGenerator(ctx="mock_connection", team_roster=[])
    # Reset doc to a fresh Document so we only see what
    # _add_technology_breakdown writes.
    gen.doc = Document()

    subs = pd.DataFrame(
        [
            # Two DSM rows for the same subscription should only count once.
            {"SUBSCRIPTION_ID": "SUB001", "PRODUCT_NAME": "Webex Calling"},
            {"SUBSCRIPTION_ID": "SUB001", "PRODUCT_NAME": "Webex Calling"},
            {"SUBSCRIPTION_ID": "SUB002", "PRODUCT_NAME": "Webex Calling"},
            {"SUBSCRIPTION_ID": "SUB003", "PRODUCT_NAME": "Webex Meetings"},
        ]
    )
    team_data = {
        "Alice CSSM": {
            "subscriptions": subs,
            "adoption_barriers": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
        }
    }

    gen._add_technology_breakdown(team_data)

    # Inspect the generated table for the per-tech counts.
    tables = gen.doc.tables
    assert tables, "expected a technology breakdown table to be generated"
    table = tables[0]
    rows = [[c.text.strip() for c in row.cells] for row in table.rows]
    # First row is the header (Team Member | tech1 | tech2 | ... | Total?)
    header = rows[0]
    alice_row = next((r for r in rows if r and r[0] == "Alice CSSM"), None)
    assert alice_row is not None, f"Alice CSSM row missing; got {rows}"

    # Walk header columns and assert per-tech counts. Skip the leading
    # "Team Member" cell and any trailing Total cell.
    counts = {}
    for col_name, value in zip(header[1:], alice_row[1:]):
        if col_name.lower() == "total":
            continue
        try:
            counts[col_name] = int(value)
        except ValueError:
            counts[col_name] = value

    # Webex Calling should be 2 (SUB001 deduped + SUB002), NOT 3.
    calling_count = next(
        (v for k, v in counts.items() if "Calling" in k or "calling" in k.lower()),
        None,
    )
    assert calling_count == 2, (
        f"Webex Calling per-CSSM count should be 2 unique subscriptions "
        f"(SUB001 deduped + SUB002), got {calling_count!r} in {counts}"
    )
    meetings_count = next(
        (v for k, v in counts.items() if "Meeting" in k or "meeting" in k.lower()),
        None,
    )
    assert meetings_count == 1, (
        f"Webex Meetings per-CSSM count should be 1, got {meetings_count!r} "
        f"in {counts}"
    )
