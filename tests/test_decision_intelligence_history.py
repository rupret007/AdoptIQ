"""Focused persistence coverage for Decision Intelligence V2 history links."""

from __future__ import annotations

import inspect
import sqlite3

import pytest

import enhanced_admin_dashboard_v2 as admin


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    database_path = tmp_path / "history.db"
    monkeypatch.setattr(admin, "DB_PATH", str(database_path))
    monkeypatch.setattr(admin, "_db_initialized_for_path", None)
    monkeypatch.setenv(
        "ADOPTIQ_AUDIT_MIRROR_PATH",
        str(tmp_path / "history.audit.jsonl"),
    )
    return database_path


def test_legacy_database_migrates_in_place_without_losing_rows(
    isolated_history,
):
    with sqlite3.connect(isolated_history) as connection:
        connection.execute(
            """
            CREATE TABLE report_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                created_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO report_history (request_id, created_at) VALUES (?, ?)",
            ("legacy-request", "2025-01-01T00:00:00Z"),
        )

    admin.init_database()

    with admin.db_connection() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(report_history)")
        }
        preserved = connection.execute(
            "SELECT request_id FROM report_history"
        ).fetchall()

    assert {
        "analysis_schema_version",
        "analysis_fingerprint",
        "analysis_request_fingerprint",
        "analysis_comparison_scope_fingerprint",
        "analysis_snapshot_path",
    } <= columns
    assert preserved == [("legacy-request",)]


def test_record_completion_keeps_legacy_calls_and_binds_v2_snapshot(
    isolated_history,
):
    admin.init_database()

    # The pre-V2 positional contract remains valid and produces an explicitly
    # unversioned history row rather than inventing comparison metadata.
    admin.record_report_completion(
        "legacy",
        "compact",
        "manager",
        "webex",
        "Acme",
        "completed",
        "2026-07-13T01:00:00Z",
        "2026-07-13T01:01:00Z",
    )
    admin.record_report_completion(
        "v2",
        "compact",
        "manager",
        "webex",
        "Acme",
        "completed",
        "2026-07-13T02:00:00Z",
        "2026-07-13T02:01:00Z",
        analysis_schema_version="2.0.0",
        analysis_fingerprint="analysis:abc123",
        analysis_request_fingerprint="request:def456",
        analysis_comparison_scope_fingerprint="scope:ghi789",
        analysis_snapshot_path="/safe/local/snapshots/v2.json",
    )

    with admin.db_connection() as connection:
        stored = connection.execute(
            """
            SELECT analysis_schema_version, analysis_fingerprint,
                   analysis_request_fingerprint,
                   analysis_comparison_scope_fingerprint,
                   analysis_snapshot_path
            FROM report_history
            WHERE request_id = ?
            """,
            ("v2",),
        ).fetchone()

    assert stored == (
        "2.0.0",
        "analysis:abc123",
        "request:def456",
        "scope:ghi789",
        "/safe/local/snapshots/v2.json",
    )

    history = {
        row["request_id"]: row
        for row in admin.get_report_history()
        if not row.get("_placeholder")
    }
    assert history["legacy"]["analysis_metadata"] == {
        "schema_version": "",
        "schema_major": None,
        "schema_compatibility": "legacy_unversioned",
        "analysis_fingerprint": "",
        "request_fingerprint": "",
        "comparison_scope_fingerprint": "",
        "snapshot_path": "",
        "snapshot_bound": False,
        "comparison_eligible": False,
    }
    assert history["v2"]["analysis_metadata"]["schema_compatibility"] == (
        "compatible"
    )
    assert history["v2"]["analysis_metadata"]["schema_major"] == 2
    assert history["v2"]["analysis_metadata"]["comparison_eligible"] is True


def test_history_metadata_is_bounded_and_version_aware():
    compatible = admin._safe_analysis_history_metadata(
        "2.7.4-beta.1",
        "analysis:abc",
        "request:def",
        "scope:ghi",
        "/tmp/snapshot.json",
    )
    incompatible = admin._safe_analysis_history_metadata(
        "3.0.0",
        "analysis:abc",
        "request:def",
        "scope:ghi",
        "/tmp/snapshot.json",
    )
    invalid = admin._safe_analysis_history_metadata(
        "not-a-version",
        "analysis:abc\nwith-control",
        "r" * 400,
        "scope:ghi",
        "/tmp/snapshot.json\x00ignored",
    )

    assert compatible["schema_compatibility"] == "compatible"
    assert compatible["comparison_eligible"] is True
    assert incompatible["schema_compatibility"] == "incompatible_major"
    assert incompatible["comparison_eligible"] is False
    assert invalid["schema_compatibility"] == "invalid_version"
    assert invalid["comparison_eligible"] is False
    assert "\n" not in invalid["analysis_fingerprint"]
    assert "\x00" not in invalid["snapshot_path"]
    assert len(invalid["request_fingerprint"]) == 160


def test_record_completion_exposes_optional_v2_parameters():
    signature = inspect.signature(admin.record_report_completion)
    assert {
        "analysis_schema_version",
        "analysis_fingerprint",
        "analysis_request_fingerprint",
        "analysis_comparison_scope_fingerprint",
        "analysis_snapshot_path",
    } <= set(signature.parameters)
