"""Round 3 / Phase 4.6 regression test.

``_correlate_incidents_with_cases`` must:
1) Successfully parse status.webex.com ISO 8601 ``Z`` timestamps
   (e.g. ``2024-03-04T14:23:00Z``).
2) Not silently pick between ``%m/%d`` and ``%d/%m`` for ambiguous
   strings — those should be skipped instead of misinterpreted.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_iso_z_timestamp_correlates_temporally():
    try:
        from adoptiq_backend import _correlate_incidents_with_cases
    except Exception:
        pytest.skip("adoptiq_backend not importable")

    incident_iso = "2024-03-04T14:23:00Z"
    incidents = [
        {
            "id": "INC-1",
            "title": "Webex Calling outage",
            "description": "Calls dropping in EMEA",
            "published": incident_iso,
        }
    ]
    csone = pd.DataFrame(
        [
            {
                "SR Number": "SR-001",
                "Customer Name": "Acme",
                "Title": "Random unrelated case",
                "Problem Description": "totally different content",
                # Within 7 days of incident date
                "Date/Time Opened": "2024-03-05 09:00:00",
            }
        ]
    )

    out = _correlate_incidents_with_cases(incidents, csone, pd.DataFrame())
    # Even without keyword overlap, temporal proximity (within 7d) should
    # trigger the correlation. If the ISO Z parse silently failed the
    # temporal_match would be False and SR-001 would not be linked.
    assert "INC-1" in out
    matches = out["INC-1"]
    assert any(m.get("match_type") == "temporal" for m in matches)


def test_correlator_does_not_blindly_swap_mdy_dmy():
    """Source must not contain a fallback that tries both ``%m/%d`` and
    ``%d/%m`` for the same string — that's how a March 4 incident
    silently becomes an April 3 incident in EMEA-formatted feeds."""
    src = (PROJECT_ROOT / "adoptiq_backend.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    idx = src.find("def _correlate_incidents_with_cases")
    assert idx >= 0
    body = src[idx : idx + 4000]
    # Allow the comments that reference the regression; only fail on
    # actual code lines that try the ambiguous formats.
    bad_code_lines = [
        line for line in body.splitlines()
        if ("%m/%d" in line or "%d/%m" in line)
        and not line.lstrip().startswith("#")
    ]
    assert not bad_code_lines, (
        "_correlate_incidents_with_cases must not silently choose between "
        "%m/%d and %d/%m parses for ambiguous incident dates. "
        f"Offending lines: {bad_code_lines}"
    )
