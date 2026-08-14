"""Round 167 production-fetch contract simulation without live Snowflake."""

from __future__ import annotations

import json

import pytest

import adoptiq_backend as backend
import canonical_metrics as canonical
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
        "policy_blocked_sources_not_zero": True,
        "unknown_query_rejected": True,
        "parameter_binding_and_family_coverage": True,
    }
    assert payload["policy_states"] == {
        "success_priorities": {
            "state": "unavailable",
            "error_kind": "table_policy_violation",
        },
        "support_cases": {
            "state": "failed",
            "error_kind": "table_policy_violation",
        },
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


def test_policy_blocked_success_priorities_are_unavailable_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend, "is_table_blocked", lambda _table: True)

    class NoQueryConnection:
        def cursor(self):
            raise AssertionError("a policy-blocked source must not open a cursor")

    frame = backend.fetch_csconsole_success_priorities(
        NoQueryConnection(),
        ["authorized-customer"],
        90,
    )

    assert frame.empty
    assert frame.attrs["fetch_error_dataset"] == "csconsole_success_priorities"
    assert frame.attrs["fetch_error_kind"] == "table_policy_violation"
    assert frame.attrs["source_state"] == "unavailable"
    assert frame.attrs["source_unavailable"] is True
    assert canonical.source_data_state(frame)["state"] == "unavailable"


def test_explicit_simulated_table_access_never_widens_other_policy() -> None:
    allowed_for_mapping = "EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C"
    unrelated_blocked = "CX_DB.CX_SWSSBST_BR.USER_DATA"

    assert backend.is_table_blocked(allowed_for_mapping) is True
    assert backend.is_table_blocked(unrelated_blocked) is True
    with runner._simulate_explicit_table_access(allowed_for_mapping):  # noqa: SLF001
        assert backend.is_table_blocked(allowed_for_mapping) is False
        assert backend.is_table_blocked(unrelated_blocked) is True
        with pytest.raises(ValueError):
            backend.guard_table(unrelated_blocked)
    assert backend.is_table_blocked(allowed_for_mapping) is True


def test_cli_requires_explicit_fixture_activation(tmp_path, capsys) -> None:
    with pytest.raises(RuntimeError):
        runner.main(["--summary", str(tmp_path / "summary.json")])

    assert not capsys.readouterr().out
