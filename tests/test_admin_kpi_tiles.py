"""
Admin dashboard KPI tile tests.

Phase 2 fix: KPI tiles must report `SELECT COUNT(*)` from the
underlying tables, not `len(LIMIT 50 page)`. This test seeds 60
report_history rows + 60 ip_connections rows and asserts the
canonical counters return 60, not 50.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def admin_module(tmp_path, monkeypatch):
    """Import enhanced_admin_dashboard_v2 with a sandboxed DB path."""
    db_file = tmp_path / "admin_monitoring_test.db"
    # Force the module to use our temp DB file before init_database().
    import enhanced_admin_dashboard_v2 as ead

    monkeypatch.setattr(ead, "DB_PATH", str(db_file))
    monkeypatch.setattr(ead, "_db_initialized_for_path", None)
    ead.init_database()
    return ead


def _seed_report_history(ead, n: int) -> None:
    with ead.db_connection() as conn:
        cur = conn.cursor()
        for i in range(n):
            cur.execute(
                """
                INSERT INTO report_history
                  (request_id, report_type, manager, technology, customer_name,
                   status, start_time, end_time, ip_address, user_agent,
                   error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"req-{i:04d}",
                    "compact",
                    "tester",
                    "webex",
                    f"Customer {i}",
                    "completed",
                    "",
                    "",
                    "127.0.0.1",
                    "pytest",
                    "",
                    "",
                ),
            )


def _seed_ip_connections(ead, n: int, requests_each: int = 3) -> None:
    with ead.db_connection() as conn:
        cur = conn.cursor()
        for i in range(n):
            cur.execute(
                """
                INSERT OR REPLACE INTO ip_connections
                  (ip_address, first_seen, last_seen, request_count,
                   user_agent, country, city, isp, risk_level, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"10.0.0.{i}",
                    "",
                    "",
                    requests_each,
                    "pytest",
                    "US",
                    "Anywhere",
                    "TestNet",
                    "low",
                    "",
                ),
            )


def test_get_total_count_returns_full_table_not_page_size(admin_module):
    """If we insert 60 rows, get_total_count must return 60 (not 50)."""
    _seed_report_history(admin_module, 60)
    assert admin_module.get_total_count("report_history") == 60


def test_get_total_count_rejects_unknown_table(admin_module):
    """Allow-list defends against arbitrary identifier injection."""
    assert admin_module.get_total_count("sqlite_master") == 0
    assert admin_module.get_total_count("'; DROP TABLE x;--") == 0


def test_get_total_request_count_sums_all_ips(admin_module):
    _seed_ip_connections(admin_module, 60, requests_each=4)
    assert admin_module.get_total_request_count() == 60 * 4


def test_get_total_count_zero_for_empty_table(admin_module):
    assert admin_module.get_total_count("error_logs") == 0
    assert admin_module.get_total_count("security_events") == 0
