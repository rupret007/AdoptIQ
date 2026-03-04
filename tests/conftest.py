"""
Shared fixtures for AdoptIQ test suite.
"""
import os
import sys
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def app():
    """Create a Flask test application."""
    from app_simple import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    yield flask_app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def ab_df():
    """Sample adoption barrier DataFrame."""
    return pd.DataFrame([
        {"customer_name": "Acme Corp", "SUBJECT_C": "Integration issue", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open", "ID": "AB001"},
        {"customer_name": "Acme Corp", "SUBJECT_C": "Login problem", "SEVERITY_C": "High", "AB_STATUS_C": "Open", "ID": "AB002"},
        {"customer_name": "Beta Inc", "SUBJECT_C": "API timeout", "SEVERITY_C": "Medium", "AB_STATUS_C": "Closed", "ID": "AB003"},
    ])


@pytest.fixture
def csone_df():
    """Sample CSOne/TAC case DataFrame."""
    return pd.DataFrame([
        {"customer_name": "Acme Corp", "SR Number": "TAC001", "Title": "Login fails", "Severity": "P1", "Transaction ID": "BEMS01916938", "Date/Time Opened": (datetime.now() - timedelta(days=5)).isoformat()},
        {"customer_name": "Acme Corp", "SR Number": "TAC002", "Title": "API slow", "Severity": "P2", "Transaction ID": "", "Date/Time Opened": (datetime.now() - timedelta(days=10)).isoformat()},
        {"customer_name": "Beta Inc", "SR Number": "TAC003", "Title": "Config issue", "Severity": "P3", "Transaction ID": "", "Date/Time Opened": (datetime.now() - timedelta(days=20)).isoformat()},
    ])


@pytest.fixture
def team_subs_df():
    """Sample team subscriptions DataFrame."""
    return pd.DataFrame({
        "BU_NAME": ["Acme Corp", "Beta Inc"],
        "ACCOUNT_ID_C": ["ACC001", "ACC002"],
        "SUBSCRIPTION_ID": ["SUB001", "SUB002"],
        "RENEWAL_RISK_CATEGORY": ["High", "Low"],
    })


@pytest.fixture
def tmp_outputs(tmp_path):
    """Temporary outputs directory for download tests."""
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    return outputs


@pytest.fixture
def mock_analysis_status(tmp_path):
    """Pre-populated analysis_status for status management tests.
    Returns (status_dict, status_file_path).
    """
    status = {
        "test-id-001": {
            "status": "completed",
            "progress": 100,
            "message": "Analysis completed",
            "start_time": "2026-02-01T10:00:00",
            "completion_time": "2026-02-01T10:05:00",
            "word_report": "/tmp/test_report.docx",
            "excel_report": "/tmp/test_report.xlsx",
        }
    }
    status_file = tmp_path / "analysis_status.json"
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    return status, str(status_file)
