"""Round 168 admin debug metrics must distinguish zero from unavailable."""

from __future__ import annotations

import json

import requests

import enhanced_admin_dashboard_v2 as admin


def test_debug_metric_parser_accepts_only_an_explicit_nonnegative_count() -> None:
    assert admin._admin_debug_metrics(  # noqa: SLF001
        {"verbose_debug": True, "snowflake_query_count": 0}
    ) == (True, 0, False)
    assert admin._admin_debug_metrics(  # noqa: SLF001
        {"verbose_debug": False, "snowflake_query_count": "7"}
    ) == (False, 7, False)

    for payload in (
        None,
        [],
        {},
        {"verbose_debug": False},
        {"snowflake_query_count": None},
        {"snowflake_query_count": True},
        {"snowflake_query_count": "not-a-count"},
        {"snowflake_query_count": -1},
    ):
        _verbose, count, failed = admin._admin_debug_metrics(payload)  # noqa: SLF001
        assert count == 0
        assert failed is True


def test_dashboard_renders_debug_query_count_as_unavailable_on_network_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        admin,
        "get_server_status",
        lambda: {
            "running": False,
            "pid": None,
            "host": "127.0.0.1",
            "port": 15152,
            "port_open": False,
            "data_path_ok": True,
            "data_path_detail": "test",
            "last_check": "2026-08-13T12:00:00Z",
        },
    )
    monkeypatch.setattr(
        admin,
        "get_system_info",
        lambda: {
            "cpu_percent": 0,
            "memory_percent": 0,
            "disk_percent": 0,
            "processes": 0,
            "state": "test",
        },
    )
    monkeypatch.setattr(admin, "get_report_history", lambda: [])
    monkeypatch.setattr(admin, "get_ip_connections", lambda: [])
    monkeypatch.setattr(admin, "get_error_logs", lambda: [])
    monkeypatch.setattr(admin, "get_audit_history", lambda limit=20: {"audits": []})
    monkeypatch.setattr(admin, "get_audit_summary", lambda: {})
    monkeypatch.setattr(admin, "get_total_count", lambda _table: 0)
    monkeypatch.setattr(admin, "get_total_request_count", lambda: 0)

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def fake_get(url, *_args, **_kwargs):
        if url.endswith("/api/status/all"):
            return Response([])
        if url.endswith("/api/debug/verbose"):
            raise requests.ConnectionError("synthetic unreachable endpoint")
        if url.endswith("/api/corpus/status"):
            return Response({"ok": False, "available": False})
        return Response({})

    monkeypatch.setattr("enhanced_admin_dashboard_v2.requests.get", fake_get)

    response = admin.admin_app.test_client().get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "n/a (debug metric unavailable)" in body
    assert "Snowflake Queries (since reset):</strong> 0" not in body


def test_artifact_only_audit_can_never_claim_full_accuracy_pass() -> None:
    checks = [
        {"check": "file_exists", "status": "pass"},
        {"check": "artifact_hash_word", "status": "pass"},
        {"check": "data_sources", "status": "skipped"},
        {"check": "bems_detection", "status": "skipped"},
        {"check": "references", "status": "skipped"},
    ]

    status, truth_complete, skipped = admin._artifact_audit_completion(  # noqa: SLF001
        checks,
        70,
        70,
    )

    assert status == "integrity_only"
    assert truth_complete is False
    assert skipped == 3


def test_admin_template_uses_real_denominator_and_na_for_skipped_truth_checks() -> None:
    template = admin.ENHANCED_ADMIN_TEMPLATE_V2

    assert "{{ audit.score }}/{{ audit.max_score }}" in template
    assert "audit.score }}/100" not in template
    assert "Artifact Integrity History" in template
    assert "Full Truth-Check Pass Rate" in template
    assert "N/A' if _bems_status == 'skipped'" in template


def test_legacy_green_audit_with_skipped_truth_checks_is_reprojected_incomplete(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(admin, "DB_PATH", str(tmp_path / "round168-admin.db"))
    monkeypatch.setattr(admin, "_db_initialized_for_path", None)
    admin.init_database()
    checks = [
        {"check": "file_exists", "status": "pass"},
        {"check": "data_sources", "status": "skipped"},
        {"check": "bems_detection", "status": "skipped"},
        {"check": "references", "status": "skipped"},
    ]
    with admin.db_connection() as conn:
        conn.execute(
            """
            INSERT INTO audit_results
            (analysis_id, audit_timestamp, status, score, max_score, checks_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-green",
                "2026-08-01T12:00:00Z",
                "good",
                70,
                70,
                json.dumps(checks),
                "2026-08-01T12:00:00Z",
            ),
        )

    history = admin.get_audit_history()
    summary = admin.get_audit_summary()

    assert history["audits"][0]["stored_status"] == "good"
    assert history["audits"][0]["status"] == "integrity_only"
    assert history["audits"][0]["truth_checks_complete"] is False
    assert summary["audits_passed"] == 0
    assert summary["audits_failed"] == 0
    assert summary["audits_incomplete"] == 1
    assert summary["average_audit_score"] == 100.0
