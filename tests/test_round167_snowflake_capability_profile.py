"""Round 167 metadata-only Snowflake capability exploration."""

from __future__ import annotations

import json
import logging
import sys

import pytest

from local_acceptance_lab import build_scenario_bundle
from local_snowflake_simulator import FixtureSnowflakeConnection
from scripts import profile_snowflake_capabilities as profiler


def test_local_profile_describes_all_allowed_tables_without_row_queries() -> None:
    connection = FixtureSnowflakeConnection(build_scenario_bundle("multi_manager"))

    payload = profiler.profile_connection(connection, mode="local")

    assert payload["all_passed"] is True
    assert payload["row_values_queried"] is False
    assert payload["accessible_table_count"] == len(profiler.ALLOWED_CAPABILITY_TABLES)
    assert all(
        row["access_state"] == "simulated_available" for row in payload["tables"]
    )
    assert connection.traces
    assert {trace.family for trace in connection.traces} == {"metadata_probe"}
    assert all(not row["probe_attempted"] for row in payload["policy_blocked_tables"])


def test_profile_surfaces_decision_expansion_candidates_without_values() -> None:
    connection = FixtureSnowflakeConnection(build_scenario_bundle("healthy"))

    payload = profiler.profile_connection(connection, mode="local")
    serialized = json.dumps(payload, sort_keys=True)

    capabilities = {
        opportunity["capability"]
        for table in payload["tables"]
        for opportunity in table["decision_opportunities"]
    }
    assert {
        "scope_and_attribution",
        "renewal_timing",
        "commercial_value",
        "risk_and_health",
        "action_execution",
        "customer_sentiment",
        "record_traceability",
    } <= capabilities
    assert "Acme Corporation" not in serialized
    assert "fixture.owner" not in serialized
    assert "SELECT " not in serialized
    assert "DESC TABLE" not in serialized

    by_name = {row["table"].rsplit(".", 1)[-1]: row for row in payload["tables"]}
    assert "PRODUCT_NAME" in by_name["DSM_ASSIGNMENT_DATA"]["decision_mapped_columns"]
    assert {
        "DESCRIPTION_C",
        "NEXT_ACTION_C",
        "SEVERITY_C",
    } <= set(by_name["C360_CS_TASK_C_VW"]["decision_mapped_columns"])
    assert {
        "COMMENTS__C",
        "PULSE_DATE_C",
        "PULSE_RATING__C",
    } <= set(
        by_name["ESA_C360_CUSTOMER_PULSE__C"]["decision_mapped_columns"]
    )


def test_live_cli_requires_separate_authorization_confirmation() -> None:
    with pytest.raises(RuntimeError, match="explicit authorization"):
        profiler.main(["--live-metadata"])


def test_live_connection_failure_writes_only_sanitized_evidence(
    tmp_path, monkeypatch, capsys
) -> None:
    import adoptiq_backend as backend

    marker = "private-provider-detail-must-not-escape"

    def fail_connection():
        print(marker)
        print(marker, file=sys.stderr)
        logging.getLogger("adoptiq_backend").error(marker)
        raise RuntimeError(f"Failed to connect to Snowflake: {marker}")

    monkeypatch.setattr(backend, "_connect_with_keeper", fail_connection)
    summary = tmp_path / "live-capabilities.json"

    result = profiler.main(
        [
            "--live-metadata",
            "--confirm-authorized-live-metadata",
            "--summary",
            str(summary),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(summary.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, sort_keys=True)
    assert result == 5
    assert payload["all_passed"] is False
    assert payload["live_validation_attempted"] is True
    assert payload["live_validation_performed"] is False
    assert payload["row_values_queried"] is False
    assert payload["accessible_table_count"] == 0
    assert payload["error_kind"] == "connection_unavailable"
    assert marker not in serialized
    assert marker not in captured.out
    assert marker not in captured.err
    assert "Traceback" not in captured.err


def test_live_connection_close_output_is_suppressed(tmp_path, monkeypatch, capsys) -> None:
    import adoptiq_backend as backend

    marker = "private-close-detail-must-not-escape"

    class Connection:
        def close(self) -> None:
            print(marker)
            print(marker, file=sys.stderr)
            logging.getLogger("snowflake.connector").critical(marker)

    monkeypatch.setattr(backend, "_connect_with_keeper", Connection)
    monkeypatch.setattr(
        profiler,
        "profile_connection",
        lambda _connection, *, mode: {
            "all_passed": True,
            "accessible_table_count": 7,
            "row_values_queried": False,
            "mode": mode,
        },
    )
    summary = tmp_path / "live-capabilities.json"

    result = profiler.main(
        [
            "--live-metadata",
            "--confirm-authorized-live-metadata",
            "--summary",
            str(summary),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert marker not in captured.out
    assert marker not in captured.err
    assert marker not in summary.read_text(encoding="utf-8")
