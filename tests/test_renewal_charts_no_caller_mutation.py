"""Round 3 / Phase 1.2 regression test.

`create_renewal_charts` must not mutate the caller's ``customer_csone``
DataFrame. The pre-fix code rewrote ``Date/Time Opened`` to datetime
in place via ``pd.to_datetime(..., errors='coerce')`` then rebound a
local ``customer_csone = customer_csone.dropna(...)`` — leaving the
caller with a converted dtype and NaT rows.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_create_renewal_charts_does_not_mutate_caller(tmp_path, monkeypatch):
    try:
        import matplotlib

        matplotlib.use("Agg")
    except Exception:
        pytest.skip("matplotlib unavailable")

    monkeypatch.chdir(tmp_path)
    os.makedirs("outputs", exist_ok=True)

    from app_simple import create_renewal_charts

    open_dates = [
        (datetime.utcnow() - timedelta(days=5)).isoformat(),
        "not-a-date",
        (datetime.utcnow() - timedelta(days=10)).isoformat(),
    ]
    customer_csone = pd.DataFrame(
        {
            "customer_name": ["Acme", "Acme", "Acme"],
            "Date/Time Opened": open_dates,
            "Severity": ["P1", "P2", "P3"],
        }
    )
    customer_ab = pd.DataFrame(
        {
            "customer_name": ["Acme"],
            "SUBJECT_C": ["x"],
            "SEVERITY_C": ["High"],
            "AB_STATUS_C": ["Open"],
        }
    )

    snapshot = customer_csone.copy(deep=True)
    snapshot_dtype = customer_csone["Date/Time Opened"].dtype

    create_renewal_charts(
        customer_ab=customer_ab,
        customer_csone=customer_csone,
        renewal_analysis={"renewal_risk_score": 30, "renewal_risk_category": "LOW"},
        ext_incidents=[],
        days=90,
    )

    assert customer_csone["Date/Time Opened"].dtype == snapshot_dtype, (
        "create_renewal_charts mutated the caller's column dtype"
    )
    assert list(customer_csone["Date/Time Opened"]) == list(
        snapshot["Date/Time Opened"]
    ), "create_renewal_charts mutated the caller's row contents"
    assert len(customer_csone) == len(snapshot), (
        "create_renewal_charts dropped rows from the caller's frame"
    )
