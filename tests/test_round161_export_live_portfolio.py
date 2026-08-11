"""Round 161 — export_live_portfolio_for_backtest shape contract (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.export_live_portfolio_for_backtest import _serialize_team_data
from scripts.backtest_escalation_forecast import _customers_from_fixture


def test_round161_export_serialize_team_data_shape() -> None:
    team_data = {
        "Alex Rivera": {
            "tac_cases": pd.DataFrame([
                {"SR Number": "1", "BU_NAME": "Acme", "Date/Time Opened": "2026-07-30"},
            ]),
            "adoption_barriers": pd.DataFrame([
                {"ID": "AB1", "BU_NAME": "Acme", "OPEN_DATE_C": "2026-05-15"},
            ]),
            "customer_pulse": pd.DataFrame([
                {"ID": "P1", "BU_NAME": "Acme", "SCORE__C": 4.0, "PULSE_DATE_C": "2026-05-10"},
            ]),
        }
    }
    serialized = _serialize_team_data(team_data)
    assert "Alex Rivera" in serialized
    assert serialized["Alex Rivera"]["tac_cases"][0]["BU_NAME"] == "Acme"
    payload = {
        "derived_from": "live_cisco_sources",
        "data_end": "2026-08-11T12:00:00Z",
        "manager_name": "Brian Frazier",
        "days": 365,
        "team_data": serialized,
    }
    customers = _customers_from_fixture(payload)
    assert "Acme" in customers
    assert not customers["Acme"]["tac_cases"].empty


def test_round161_export_script_module_exists() -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "export_live_portfolio_for_backtest.py"
    body = path.read_text(encoding="utf-8")
    assert 'derived_from": "live_cisco_sources"' in body or '"live_cisco_sources"' in body
    assert "Round 161" in body
