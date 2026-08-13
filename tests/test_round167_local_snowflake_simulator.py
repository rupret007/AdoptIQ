"""Round 167 production-fetch contract simulation without live Snowflake."""

from __future__ import annotations

import json

import pytest

from local_acceptance_lab import build_scenario_bundle
from local_snowflake_simulator import (
    FixtureSnowflakeConnection,
    UnexpectedSimulatedQuery,
)
from scripts import run_local_source_contracts as runner


def test_unknown_sql_fails_closed_without_returning_plausible_rows() -> None:
    connection = FixtureSnowflakeConnection(build_scenario_bundle("healthy"))

    with pytest.raises(UnexpectedSimulatedQuery):
        connection.cursor().execute("SELECT * FROM SOMETHING_NEW")


def test_source_contract_runner_exercises_real_fetchers_and_secondary_owner() -> None:
    payload = runner.run_contracts(
        scenario="healthy",
        manifest=runner.DEFAULT_MANIFEST_PATH,
    )

    assert payload["all_passed"] is True
    assert payload["checks"] == {
        "count_contract": True,
        "enhanced_account_contract": True,
        "secondary_attribution": True,
        "customer_search_attribution": True,
        "failure_state_not_zero": True,
        "unknown_query_rejected": True,
        "parameter_binding_and_family_coverage": True,
    }
    assert payload["secondary_attribution"] == {
        "primary_rows": 2,
        "secondary_rows": 5,
        "merged_rows": 7,
        "duplicate_rows_dropped": 0,
    }
    assert payload["query_trace"]["all_data_queries_parameterized"] is True
    serialized = json.dumps(payload, sort_keys=True)
    assert "Acme Corporation" not in serialized
    assert "fixture.owner" not in serialized
    assert "SELECT " not in serialized


def test_cli_requires_explicit_fixture_activation(tmp_path, capsys) -> None:
    with pytest.raises(RuntimeError):
        runner.main(["--summary", str(tmp_path / "summary.json")])

    assert not capsys.readouterr().out
