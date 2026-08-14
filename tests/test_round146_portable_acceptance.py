"""Round 146 portable acceptance and evidence-safety contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import run_round146_acceptance as acceptance


_MISSING = object()
_INVALID_JSON_COUNTS = (
    False,
    True,
    "1",
    1.0,
    None,
    _MISSING,
    -1,
    1 << 70,
)


class _Response:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class _FixtureWorkspaceSession:
    def get(
        self,
        url: str,
        *,
        timeout: float,
        params: dict[str, Any] | None = None,
    ) -> _Response:
        del timeout
        if url.endswith("/"):
            return _Response(
                200,
                text='<meta name="csrf-token" content="safe-fixture-token">',
            )
        if url.endswith("/api/diag/connectivity"):
            return _Response(
                200,
                {
                    "ok": True,
                    "mode": acceptance.SOURCE_MODE,
                    "live_validation_performed": False,
                },
            )
        if url.endswith("/api/decision-workspace/scope-preview"):
            assert params is not None
            return _Response(
                200,
                {
                    "ok": True,
                    "preview": {
                        "schema": acceptance.WORKSPACE_SCHEMA,
                        "report_type": params["report_type"],
                        "scope_type": params["scope_type"],
                        "source_mode": "local_fixture_guarded",
                        "live_validation_performed": False,
                        "expected_sources": ["Subscriptions", "Action Plans"],
                        "limitations": [
                            "Controlled local fixture only; no live validation was performed."
                        ],
                    },
                },
            )
        if url.endswith("/api/decision-workspace/history"):
            scope_type = str((params or {}).get("scope_type") or "")
            if scope_type:
                return _Response(
                    200,
                    {
                        "ok": True,
                        "reports": [
                            {
                                "analysis_id": f"Fixture_{scope_type.title()}",
                                "excel_available": True,
                                "report_type": "leader",
                                "scope_type": scope_type,
                                "manager": "Local Fixture Manager",
                            }
                        ],
                    },
                )
            return _Response(
                200,
                {
                    "ok": True,
                    "reports": [
                        {"analysis_id": "Fixture_After", "excel_available": True},
                        {"analysis_id": "Fixture_Before", "excel_available": True},
                    ],
                },
            )
        if "/api/decision-workspace/report/" in url:
            analysis_id = url.rsplit("/", 1)[-1]
            return _Response(
                200,
                {
                    "ok": True,
                    "report": {
                        "schema": acceptance.WORKSPACE_SCHEMA,
                        "analysis_id": analysis_id,
                        "workbook_loaded": True,
                        "canonical_snapshot": True,
                        "decision_metrics": [
                            {"metric_key": "kpi.customers", "value": 3}
                        ],
                        "fact_fingerprint": "fixture-fingerprint",
                        "ask_ai_binding": {
                            "analysis_id": analysis_id,
                            "manager": "Local Fixture Manager",
                            "technology": "All",
                            "days": 90,
                            "scope_type": "team",
                            "scope_value": "",
                            "report_type": "leader",
                            "data_as_of_utc": "2026-08-03T21:00:00Z",
                            "fact_fingerprint": "fixture-fingerprint",
                        },
                    },
                },
            )
        raise AssertionError(f"unexpected GET {url}")

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> _Response:
        del timeout
        assert headers["X-CSRFToken"] == "safe-fixture-token"
        if url.endswith("/api/decision-workspace/compare"):
            assert json == {
                "before_analysis_id": "Fixture_Before",
                "after_analysis_id": "Fixture_After",
            }
            return _Response(200, {"ok": True, "comparison": {"same_scope": True}})

        assert json["report_analysis_id"] == "Fixture_After"
        scope_context = {
            "manager": "Local Fixture Manager",
            "technology": "All",
            "days": 90,
            "scope_type": "team",
            "scope_value": "",
            "report_analysis_id": "Fixture_After",
            "report_type": "leader",
            "data_as_of_utc": "2026-08-03T21:00:00Z",
            "fact_fingerprint": "fixture-fingerprint",
        }
        answer = "Prioritize AP-001. [Source: Action_Plans → AP-001]"
        if url.endswith("/api/ask-ai-portfolio"):
            return _Response(
                200,
                {
                    "ok": True,
                    "mode": "grounded",
                    "answer": answer,
                    "scope_context": scope_context,
                    "fallback_available": False,
                },
            )
        assert url.endswith("/api/ask-ai-portfolio/stream")
        stream_text = "\n\n".join(
            [
                "event: meta\ndata: "
                + __import__("json").dumps({"scope_context": scope_context}),
                "event: data\ndata: " + __import__("json").dumps({"chunk": answer}),
                'event: done\ndata: {"follow_up_suggestions": []}',
            ]
        )
        return _Response(
            200,
            text=stream_text,
            headers={"Content-Type": "text/event-stream"},
        )


class _LegacyOnlyWorkspaceSession(_FixtureWorkspaceSession):
    def get(
        self,
        url: str,
        *,
        timeout: float,
        params: dict[str, Any] | None = None,
    ) -> _Response:
        response = super().get(url, timeout=timeout, params=params)
        if "/api/decision-workspace/report/" in url:
            response._payload["report"]["canonical_snapshot"] = False
        return response

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> _Response:
        del headers, timeout
        assert url.endswith("/api/decision-workspace/compare")
        assert json["before_analysis_id"] == json["after_analysis_id"]
        return _Response(400, {"ok": False, "error": "Select two reports."})


class _MixedScopeWorkspaceSession(_FixtureWorkspaceSession):
    """History where the two newest canonical reports are not comparable."""

    def get(
        self,
        url: str,
        *,
        timeout: float,
        params: dict[str, Any] | None = None,
    ) -> _Response:
        if url.endswith("/api/decision-workspace/history") and not (params or {}).get("scope_type"):
            return _Response(
                200,
                {
                    "ok": True,
                    "reports": [
                        {"analysis_id": "Fixture_After", "excel_available": True},
                        {"analysis_id": "Fixture_Customer", "excel_available": True},
                        {"analysis_id": "Fixture_Before", "excel_available": True},
                    ],
                },
            )
        response = super().get(url, timeout=timeout, params=params)
        if "/api/decision-workspace/report/" in url:
            report = response._payload["report"]
            analysis_id = report["analysis_id"]
            if analysis_id == "Fixture_Customer":
                report["scope_type"] = "customer"
                report["scope_value"] = "Acme Corporation"
                report["ask_ai_binding"]["scope_type"] = "customer"
                report["ask_ai_binding"]["scope_value"] = "Acme Corporation"
            else:
                report["scope_type"] = "team"
                report["scope_value"] = ""
        return response

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> _Response:
        if url.endswith("/api/decision-workspace/compare"):
            assert headers["X-CSRFToken"] == "safe-fixture-token"
            assert json == {
                "before_analysis_id": "Fixture_Before",
                "after_analysis_id": "Fixture_After",
            }
            return _Response(200, {"ok": True, "comparison": {"same_scope": True}})
        return super().post(
            url,
            json=json,
            headers=headers,
            timeout=timeout,
        )


def _tamper_mapping_value(
    mapping: dict[str, Any],
    field: str,
    value: object,
) -> None:
    if value is _MISSING:
        mapping.pop(field, None)
    else:
        mapping[field] = value


class _BooleanTamperWorkspaceSession(_FixtureWorkspaceSession):
    """Return one otherwise-valid workspace flow with one malformed boolean."""

    def __init__(
        self,
        target: str,
        value: object,
        *,
        live: bool = False,
        production_connectivity_shape: bool = False,
    ) -> None:
        self.target = target
        self.value = value
        self.live = live
        self.production_connectivity_shape = production_connectivity_shape

    def get(
        self,
        url: str,
        *,
        timeout: float,
        params: dict[str, Any] | None = None,
    ) -> _Response:
        response = super().get(url, timeout=timeout, params=params)
        payload = response._payload
        if url.endswith("/api/diag/connectivity"):
            if self.live:
                if self.production_connectivity_shape:
                    payload.clear()
                    payload.update({"ok": True, "checks": []})
                else:
                    payload["mode"] = "live"
                    payload["live_validation_performed"] = True
            if self.target == "connectivity_ok":
                _tamper_mapping_value(payload, "ok", self.value)
            elif self.target == "connectivity_live_flag":
                _tamper_mapping_value(
                    payload,
                    "live_validation_performed",
                    self.value,
                )
        elif url.endswith("/api/decision-workspace/scope-preview"):
            preview = payload["preview"]
            if self.live:
                preview["source_mode"] = "live"
                preview["live_validation_performed"] = True
            if self.target == "preview_ok":
                _tamper_mapping_value(payload, "ok", self.value)
            elif self.target == "preview_live_flag":
                _tamper_mapping_value(
                    preview,
                    "live_validation_performed",
                    self.value,
                )
        elif url.endswith("/api/decision-workspace/history"):
            scoped = bool((params or {}).get("scope_type"))
            if self.target == "scoped_history_ok" and scoped:
                _tamper_mapping_value(payload, "ok", self.value)
            elif self.target == "history_ok" and not scoped:
                _tamper_mapping_value(payload, "ok", self.value)
            elif self.target == "excel_available" and not scoped:
                for item in payload.get("reports") or []:
                    _tamper_mapping_value(item, "excel_available", self.value)
        elif "/api/decision-workspace/report/" in url and self.target == "report_ok":
            _tamper_mapping_value(payload, "ok", self.value)
        return response

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> _Response:
        if (
            url.endswith("/api/decision-workspace/compare")
            and self.target in {"excel_available", "report_ok"}
        ):
            return _Response(400, {"ok": False, "error": "No comparable reports"})
        response = super().post(
            url,
            json=json,
            headers=headers,
            timeout=timeout,
        )
        if url.endswith("/api/decision-workspace/compare") and self.target == "compare_ok":
            _tamper_mapping_value(response._payload, "ok", self.value)
        elif url.endswith("/api/ask-ai-portfolio"):
            if self.target == "ask_sync_ok":
                _tamper_mapping_value(response._payload, "ok", self.value)
            elif self.target == "ask_sync_fallback":
                _tamper_mapping_value(
                    response._payload,
                    "fallback_available",
                    self.value,
                )
        elif (
            url.endswith("/api/ask-ai-portfolio/stream")
            and self.target == "ask_stream_ok"
        ):
            done_payload = {"ok": self.value}
            response.text = response.text.replace(
                'event: done\ndata: {"follow_up_suggestions": []}',
                "event: done\ndata: " + __import__("json").dumps(done_payload),
            )
        return response


def test_loopback_url_rejects_remote_credentials_and_query() -> None:
    assert (
        acceptance._loopback_base_url("http://127.0.0.1:5153/")  # noqa: SLF001
        == "http://127.0.0.1:5153"
    )
    with pytest.raises(ValueError, match="loopback"):
        acceptance._loopback_base_url("https://example.com")  # noqa: SLF001
    with pytest.raises(ValueError, match="credentials"):
        acceptance._loopback_base_url(  # noqa: SLF001
            "http://operator:password@127.0.0.1:5153"
        )
    with pytest.raises(ValueError, match="query"):
        acceptance._loopback_base_url(  # noqa: SLF001
            "http://127.0.0.1:5153?token=forbidden"
        )


def test_live_candidate_environment_cannot_inherit_fixture_or_test_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poisoned = {
        "ADOPTIQ_ALLOW_TEST_CORPUS_REFRESH": "1",
        "ADOPTIQ_ALLOW_TEST_RUNTIME_VECTORS": "1",
        "ADOPTIQ_BAKED_CORPUS_DIR": "/tmp/external-fixture-corpus",
        "ADOPTIQ_LOCAL_ACCEPTANCE_ACTIVE": "1",
        "ADOPTIQ_LOCAL_ACCEPTANCE_STATE_DIR": "/tmp/fixture-state",
        "ADOPTIQ_TESTING": "1",
        "ADOPTIQ_TEST_MODE": "1",
        "PYTEST_CURRENT_TEST": "poisoned::test",
        "TESTING": "true",
    }
    for key, value in poisoned.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "preserved-authorized-config")

    environment = acceptance._live_candidate_environment(  # noqa: SLF001
        port=5153,
        admin_port=6153,
    )

    assert not poisoned.keys() & environment.keys()
    assert environment["SNOWFLAKE_ACCOUNT"] == "preserved-authorized-config"
    assert environment["ADOPTIQ_PRODUCTION_READY"] == "1"
    assert environment["ADOPTIQ_AUTO_UPDATE_MODE"] == "off"
    assert environment["ADOPTIQ_MAIN_URL"] == "http://127.0.0.1:5153"


def test_candidate_identity_records_fail_closed_corpus_environment() -> None:
    class _Manifest:
        schema_version = "adoptiq-release-candidate/v1"
        release_status = "eligible"
        platform = "macos"
        version = "1.0.4"
        build = 115
        source_commit_sha = "a" * 40
        built_at_utc = "2026-08-13T12:00:00Z"

        class artifact:
            name = "AdoptIQ-v1.0.4-build115.dmg"
            sha256 = "b" * 64
            size_bytes = 123

    gate = acceptance._candidate_identity_gate(  # noqa: SLF001
        _Manifest(),
        launch_controlled=True,
    )
    assert gate["candidate_environment_sanitized"] is True
    assert gate["external_baked_corpus_override_allowed"] is False


def test_fixture_app_confines_mutable_state_to_explicit_directory(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "fixture-state"
    env = dict(os.environ)
    env.update(
        {
            "ADOPTIQ_LOCAL_ACCEPTANCE_STATE_DIR": str(state_dir),
            "ADOPTIQ_OUTPUTS_DIR": str(state_dir / "reports"),
        }
    )
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(acceptance.REPO_ROOT / "scripts" / "run_local_acceptance_app.py"),
            "--enable-local-fixtures",
            "--scenario",
            "healthy",
            "--validate-only",
        ],
        cwd=acceptance.REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr[-1_000:]
    payload = json.loads(completed.stdout)
    assert payload["runtime_adapters_installed"] is True
    assert payload["live_validation_performed"] is False
    assert (state_dir / "admin_monitoring_v2.db").is_file()
    assert (state_dir / "analysis_status.json").is_file()
    assert (state_dir / "adoptiq.log").is_file()


def test_in_repo_output_is_confined_and_sensitive_evidence_is_external() -> None:
    conventional = acceptance._safe_output_dir(  # noqa: SLF001
        acceptance.REPO_ROOT / ".adoptiq-acceptance" / "round146"
    )
    assert conventional.name == "round146"
    with pytest.raises(ValueError, match=".adoptiq-acceptance"):
        acceptance._safe_output_dir(  # noqa: SLF001
            acceptance.REPO_ROOT / "unsafe-round146-evidence"
        )
    with pytest.raises(ValueError, match="outside"):
        acceptance._safe_sensitive_dir(  # noqa: SLF001
            acceptance.REPO_ROOT / ".adoptiq-acceptance" / "sensitive"
        )
    with pytest.raises(ValueError, match="dedicated"):
        acceptance._safe_output_dir(Path(Path.cwd().anchor))  # noqa: SLF001


def _passing_ai_pass() -> dict[str, object]:
    return {
        "ok": True,
        "scenarios": {
            key: {"ok": True} for key in acceptance.REQUIRED_AI_SCENARIOS
        },
    }


def _passing_decision_pass() -> dict[str, object]:
    return {
        "scopes": {
            key: {"ok": True} for key in acceptance.REQUIRED_DECISION_SCOPES
        }
    }


def test_portable_ai_inventory_matches_the_fixed_child_runner_contract() -> None:
    from scripts.run_ai_feature_acceptance import build_question_cases

    assert acceptance.REQUIRED_AI_SCENARIOS == {
        case.key for case in build_question_cases("fixture customer")
    }


def test_child_summary_projection_drops_scope_names_paths_and_answers() -> None:
    projected = acceptance._project_ai(  # noqa: SLF001
        {
            "all_automated_checks_passed": True,
            "validation_mode": "live",
            "live_validation_performed": True,
            "repeatability": {"ok": True},
            "failures_requiring_review": [],
            "passes": [_passing_ai_pass(), _passing_ai_pass()],
            "manager": "Sensitive Manager",
            "customer_name": "Sensitive Customer",
            "answer": "Sensitive generated answer",
            "sensitive_evidence_path": "C:/Sensitive/customer-evidence.json",
        }
    )
    rendered = json.dumps(projected, sort_keys=True)

    assert projected["projected_ok"] is True
    assert projected["scenario_inventory_exact"] is True
    assert projected["scenario_count_per_pass"] == [11, 11]
    assert projected["passing_scenario_count_per_pass"] == [11, 11]
    assert projected["live_validation_performed"] is True
    assert "Sensitive" not in rendered
    assert "answer" not in rendered
    assert "evidence_path" not in rendered
    assert projected["release_ready"] is False


def test_partial_local_http_inventory_cannot_pass_the_full_gate() -> None:
    payload = {
        "all_passed": True,
        "source_mode": acceptance.SOURCE_MODE,
        "live_validation_performed": False,
        "sanitized": True,
        "scenario_count": 1,
        "scenario_inventory_complete": False,
        "report_probes_enabled": True,
        "results": [
            {
                "route_checks": {
                    "manager_decision_workspace_preview": {"ok": True}
                }
            }
        ],
    }

    projection = acceptance._project_local_http(payload)  # noqa: SLF001

    assert projection["projected_ok"] is False
    assert projection["live_validation_performed"] is False


def test_partial_decision_scope_set_cannot_pass_full_acceptance() -> None:
    projection = acceptance._project_decision_reports(  # noqa: SLF001
        {
            "all_passed": True,
            "mode_executed": "offline",
            "repeatability": {"ok": True},
            "failures_requiring_review": [],
            "passes": [
                {"scopes": {"team": {"ok": True}}},
                {"scopes": {"team": {"ok": True}}},
            ],
        }
    )

    assert projection["projected_ok"] is False
    assert projection["scope_count"] == 1
    assert projection["passing_scope_count"] == 1


def test_decision_scope_union_cannot_hide_incomplete_individual_passes() -> None:
    projection = acceptance._project_decision_reports(  # noqa: SLF001
        {
            "all_passed": True,
            "mode_executed": "live",
            "live_validation_performed": True,
            "repeatability": {"ok": True},
            "failures_requiring_review": [],
            "passes": [
                {
                    "scopes": {
                        "team": {"ok": True},
                        "member": {"ok": True},
                    }
                },
                {
                    "scopes": {
                        "customer": {"ok": True},
                        "comprehensive": {"ok": True},
                    }
                },
            ],
        }
    )

    assert projection["scope_count"] == 4
    assert projection["passing_scope_count"] == 4
    assert projection["scope_inventory_exact"] is False
    assert projection["scope_count_per_pass"] == [2, 2]
    assert projection["projected_ok"] is False


@pytest.mark.parametrize("tamper", ["missing", "extra", "failed"])
def test_ai_projection_requires_exact_passing_inventory_in_each_pass(
    tamper: str,
) -> None:
    first = _passing_ai_pass()
    second = _passing_ai_pass()
    scenarios = second["scenarios"]
    assert isinstance(scenarios, dict)
    if tamper == "missing":
        scenarios.pop("external_intelligence")
    elif tamper == "extra":
        scenarios["unreviewed_new_path"] = {"ok": True}
    else:
        scenarios["external_intelligence"] = {"ok": False}

    projection = acceptance._project_ai(  # noqa: SLF001
        {
            "all_automated_checks_passed": True,
            "validation_mode": "live",
            "live_validation_performed": True,
            "repeatability": {"ok": True},
            "failures_requiring_review": [],
            "passes": [first, second],
        }
    )

    assert projection["scenario_inventory_exact"] is False
    assert projection["projected_ok"] is False


def test_ai_projection_rejects_two_empty_passes_despite_green_top_level_flags() -> None:
    projection = acceptance._project_ai(  # noqa: SLF001
        {
            "all_automated_checks_passed": True,
            "validation_mode": "live",
            "live_validation_performed": True,
            "repeatability": {"ok": True},
            "failures_requiring_review": [],
            "passes": [
                {"ok": True, "scenarios": {}},
                {"ok": True, "scenarios": {}},
            ],
        }
    )

    assert projection["pass_count"] == 2
    assert projection["scenario_count_per_pass"] == [0, 0]
    assert projection["scenario_inventory_exact"] is False
    assert projection["projected_ok"] is False


def test_ai_exact_inventory_passes_under_guarded_local_mode_without_live_claim() -> None:
    projection = acceptance._project_ai(  # noqa: SLF001
        {
            "all_automated_checks_passed": True,
            "validation_mode": "local_acceptance",
            "local_validation_performed": True,
            "repeatability": {"ok": True},
            "failures_requiring_review": [],
            "passes": [_passing_ai_pass(), _passing_ai_pass()],
        }
    )

    assert projection["projected_ok"] is True
    assert projection["scenario_inventory_exact"] is True
    assert projection["fixture_validation_performed"] is True
    assert projection["live_validation_performed"] is False


def test_complete_decision_inventory_is_required_in_both_passes() -> None:
    projection = acceptance._project_decision_reports(  # noqa: SLF001
        {
            "all_passed": True,
            "mode_executed": "offline",
            "repeatability": {"ok": True},
            "failures_requiring_review": [],
            "passes": [_passing_decision_pass(), _passing_decision_pass()],
        }
    )

    assert projection["scope_inventory_exact"] is True
    assert projection["scope_count_per_pass"] == [4, 4]
    assert projection["passing_scope_count_per_pass"] == [4, 4]
    assert projection["projected_ok"] is True


@pytest.mark.parametrize(
    ("target", "value"),
    [
        (target, value)
        for target in ("scope", "all_passed", "repeatability")
        for value in ("false", 1, "", _MISSING)
    ],
)
def test_decision_projection_rejects_non_boolean_green_values(
    target: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "all_passed": True,
        "mode_executed": "offline",
        "repeatability": {"ok": True},
        "failures_requiring_review": [],
        "passes": [_passing_decision_pass(), _passing_decision_pass()],
    }
    if target == "scope":
        passes = payload["passes"]
        assert isinstance(passes, list)
        scopes = passes[1]["scopes"]
        assert isinstance(scopes, dict)
        if value is _MISSING:
            scopes["team"] = {}
        else:
            scopes["team"] = {"ok": value}
    elif target == "all_passed":
        if value is _MISSING:
            payload.pop("all_passed")
        else:
            payload["all_passed"] = value
    else:
        payload["repeatability"] = {} if value is _MISSING else {"ok": value}

    projection = acceptance._project_decision_reports(payload)  # noqa: SLF001

    assert projection["projected_ok"] is False
    if target == "repeatability":
        assert projection["repeatability_ok"] is False


@pytest.mark.parametrize(
    ("target", "value"),
    [
        (target, value)
        for target in ("all_automated_checks_passed", "repeatability")
        for value in ("false", 1, "", _MISSING)
    ],
)
def test_ai_projection_rejects_non_boolean_green_values(
    target: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "all_automated_checks_passed": True,
        "validation_mode": "local_acceptance",
        "repeatability": {"ok": True},
        "failures_requiring_review": [],
        "passes": [_passing_ai_pass(), _passing_ai_pass()],
    }
    if target == "repeatability":
        payload["repeatability"] = {} if value is _MISSING else {"ok": value}
    elif value is _MISSING:
        payload.pop(target)
    else:
        payload[target] = value

    projection = acceptance._project_ai(payload)  # noqa: SLF001

    assert projection["projected_ok"] is False
    if target == "repeatability":
        assert projection["repeatability_ok"] is False


def _passing_csone_replay_payload() -> dict[str, object]:
    return {
        "all_passed": True,
        "sanitized": True,
        "source_rows_exported": False,
        "source_values_exported": False,
        "raw_values_retained": False,
        "loader_contract": {
            "representative_workbook_count": 4,
            "all_nonempty": True,
            "no_footer_rows_remaining": True,
            "consistent_schema": True,
            "breadth_ok": True,
            "results": [
                {
                    "row_count": 150,
                    "column_count": 39,
                    "excluded_non_record_rows": 6,
                    "footer_like_rows_remaining": 0,
                }
                for _index in range(4)
            ],
        },
        "replay": {
            "row_count": 600,
            "source_row_count": 6959,
            "excluded_non_record_rows": 24,
            "pseudonym_contract_ok": True,
            "corpus_coverage": {"breadth_ok": True},
            "privacy_contract": {
                "validated": True,
                "raw_values_retained": False,
                "row_count": 600,
                "column_count": 39,
            },
        },
    }


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("all_passed", "true"),
        ("loader_all_nonempty", 1),
        ("loader_no_footer_rows", "true"),
        ("loader_consistent_schema", 1),
        ("loader_breadth", "true"),
        ("replay_breadth", 1),
        ("pseudonym_contract", "true"),
    ],
)
def test_csone_replay_projection_rejects_truthy_non_boolean_gates(
    target: str,
    value: object,
) -> None:
    payload = _passing_csone_replay_payload()
    loader = payload["loader_contract"]
    replay = payload["replay"]
    assert isinstance(loader, dict)
    assert isinstance(replay, dict)
    if target == "all_passed":
        payload["all_passed"] = value
    elif target == "loader_all_nonempty":
        loader["all_nonempty"] = value
    elif target == "loader_no_footer_rows":
        loader["no_footer_rows_remaining"] = value
    elif target == "loader_consistent_schema":
        loader["consistent_schema"] = value
    elif target == "loader_breadth":
        loader["breadth_ok"] = value
    elif target == "replay_breadth":
        coverage = replay["corpus_coverage"]
        assert isinstance(coverage, dict)
        coverage["breadth_ok"] = value
    else:
        replay["pseudonym_contract_ok"] = value

    projection = acceptance._project_csone_replay(payload)  # noqa: SLF001

    assert projection["projected_ok"] is False


def test_csone_replay_projection_requires_both_breadth_contracts() -> None:
    payload = _passing_csone_replay_payload()

    projection = acceptance._project_csone_replay(payload)  # noqa: SLF001

    assert projection["projected_ok"] is True
    assert projection["loader_breadth_ok"] is True
    assert projection["replay_breadth_ok"] is True


def _passing_csone_corpus_payload() -> dict[str, object]:
    return {
        "sanitized": True,
        "source_rows_exported": False,
        "source_values_exported": False,
        "live_snowflake_validation_performed": False,
        "workbook_count": 4,
        "total_profiled_rows": 600,
        "distinct_schema_count": 2,
        "dominant_schema_workbook_count": 3,
        "schema_fingerprint_counts": {"schema-a": 3, "schema-b": 1},
    }


def _tamper_nested_count(
    payload: dict[str, object],
    path: tuple[str, ...],
    value: object,
) -> None:
    target: dict[str, object] = payload
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    _tamper_mapping_value(target, path[-1], value)


@pytest.mark.parametrize(
    "path",
    (
        ("scenario_count_expected",),
        ("scenario_count_completed",),
        ("cross_report_source_consistency", "comparisons"),
        ("cross_report_source_consistency", "comparisons_expected"),
        (
            "cross_report_source_consistency",
            "required_family_set_group_count",
        ),
        ("r114_audit_scenario_count_expected",),
        ("r114_audit_scenario_count_completed",),
    ),
)
@pytest.mark.parametrize("invalid", _INVALID_JSON_COUNTS)
def test_matrix_projection_rejects_noninteger_gate_counts(
    path: tuple[str, ...],
    invalid: object,
) -> None:
    payload = _complete_matrix_payload(
        [
            "a_leader",
            "b_comprehensive",
            "c_comprehensive",
            "d_leader",
            "e_compact",
            "f_renewal",
            "g_subscription",
        ]
    )
    assert acceptance._project_matrix(payload)["projected_ok"] is True  # noqa: SLF001
    _tamper_nested_count(payload, path, invalid)

    assert acceptance._project_matrix(payload)["projected_ok"] is False  # noqa: SLF001


@pytest.mark.parametrize("invalid", _INVALID_JSON_COUNTS)
def test_multi_manager_projection_rejects_noninteger_completed_count(
    invalid: object,
) -> None:
    projector, payload, _field = _passing_literal_boolean_projector_payload(
        "multi_manager"
    )
    assert projector(payload)["projected_ok"] is True
    _tamper_mapping_value(payload, "scenarios_completed", invalid)

    assert projector(payload)["projected_ok"] is False


@pytest.mark.parametrize("field", ("allowed_table_count", "accessible_table_count"))
@pytest.mark.parametrize("invalid", _INVALID_JSON_COUNTS)
def test_snowflake_projection_rejects_noninteger_table_counts(
    field: str,
    invalid: object,
) -> None:
    projector, payload, _green_field = _passing_literal_boolean_projector_payload(
        "snowflake_capabilities"
    )
    assert projector(payload)["projected_ok"] is True
    _tamper_mapping_value(payload, field, invalid)

    assert projector(payload)["projected_ok"] is False


@pytest.mark.parametrize(
    "field",
    (
        "workbook_count",
        "total_profiled_rows",
        "distinct_schema_count",
        "dominant_schema_workbook_count",
    ),
)
@pytest.mark.parametrize("invalid", _INVALID_JSON_COUNTS)
def test_csone_corpus_projection_rejects_noninteger_counts(
    field: str,
    invalid: object,
) -> None:
    payload = _passing_csone_corpus_payload()
    assert acceptance._project_csone_corpus(payload)["projected_ok"] is True  # noqa: SLF001
    _tamper_mapping_value(payload, field, invalid)

    assert acceptance._project_csone_corpus(payload)["projected_ok"] is False  # noqa: SLF001


@pytest.mark.parametrize(
    "path",
    (
        ("loader_contract", "representative_workbook_count"),
        ("replay", "row_count"),
        ("replay", "source_row_count"),
        ("replay", "excluded_non_record_rows"),
        ("replay", "privacy_contract", "row_count"),
        ("replay", "privacy_contract", "column_count"),
    ),
)
@pytest.mark.parametrize("invalid", _INVALID_JSON_COUNTS)
def test_csone_replay_projection_rejects_noninteger_counts(
    path: tuple[str, ...],
    invalid: object,
) -> None:
    payload = _passing_csone_replay_payload()
    assert acceptance._project_csone_replay(payload)["projected_ok"] is True  # noqa: SLF001
    _tamper_nested_count(payload, path, invalid)

    assert acceptance._project_csone_replay(payload)["projected_ok"] is False  # noqa: SLF001


@pytest.mark.parametrize("projector_name", ("decision", "ai"))
@pytest.mark.parametrize("invalid", ({}, "", None, _MISSING))
def test_decision_and_ai_projectors_require_exact_empty_failure_inventory(
    projector_name: str,
    invalid: object,
) -> None:
    if projector_name == "decision":
        projector = acceptance._project_decision_reports  # noqa: SLF001
        payload: dict[str, object] = {
            "all_passed": True,
            "mode_executed": "offline",
            "repeatability": {"ok": True},
            "failures_requiring_review": [],
            "passes": [_passing_decision_pass(), _passing_decision_pass()],
        }
    else:
        projector = acceptance._project_ai  # noqa: SLF001
        payload = {
            "all_automated_checks_passed": True,
            "validation_mode": "local_acceptance",
            "local_validation_performed": True,
            "repeatability": {"ok": True},
            "failures_requiring_review": [],
            "passes": [_passing_ai_pass(), _passing_ai_pass()],
        }
    assert projector(payload)["projected_ok"] is True
    _tamper_mapping_value(payload, "failures_requiring_review", invalid)

    assert projector(payload)["projected_ok"] is False


def test_fixture_startup_timeout_expands_only_for_real_corpus_metadata(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for index in range(3):
        (corpus / f"export-{index}.xlsx").write_bytes(b"xlsx-shape")
    (corpus / "~$temporary.xlsx").write_bytes(b"ignored")
    (corpus / "not-a-workbook.csv").write_bytes(b"ignored")

    assert acceptance._fixture_startup_timeout(None) == 60.0  # noqa: SLF001
    corpus_timeout = acceptance._fixture_startup_timeout(corpus)  # noqa: SLF001
    assert 120.0 <= corpus_timeout <= 900.0


def test_fixture_runtime_passes_corpus_aware_timeout_to_readiness_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "representative.xlsx").write_bytes(b"xlsx-shape")
    observed: dict[str, float] = {}

    class _ExitedProcess:
        def poll(self) -> int:
            return 0

    monkeypatch.setattr(acceptance, "_free_port", lambda: 5153)
    monkeypatch.setattr(
        acceptance.subprocess,
        "Popen",
        lambda *args, **kwargs: _ExitedProcess(),
    )

    def _capture_wait(
        base_url: str,
        process: object,
        *,
        timeout: float,
    ) -> None:
        del base_url, process
        observed["timeout"] = timeout

    monkeypatch.setattr(acceptance, "_wait_for_fixture_runtime", _capture_wait)

    with acceptance._fixture_runtime(  # noqa: SLF001
        tmp_path,
        csone_corpus_dir=corpus,
    ):
        pass

    assert observed["timeout"] >= 120.0


def test_fixture_workspace_probe_covers_every_family_and_never_claims_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acceptance.requests,
        "Session",
        lambda: _FixtureWorkspaceSession(),
    )

    result = acceptance.probe_manager_workspace(
        base_url="http://127.0.0.1:5153",
        expect_fixture=True,
        manager="Local Fixture Manager",
        technology="All",
        days=90,
        member_email="fixture.owner1@example.invalid",
        customer_name="Acme Corporation",
        subscription_id="SUB-001",
    )

    assert result["ok"] is True
    assert result["fixture_runtime_confirmed"] is True
    assert result["live_validation_performed"] is False
    assert result["production_accuracy_claimed"] is False
    assert result["preview_count"] == 8
    assert result["preview_coverage_complete"] is True
    assert result["history_count"] == 2
    assert result["scoped_history"]["member"]["ok"] is True
    assert result["scoped_history"]["customer"]["ok"] is True
    assert result["report_view_ok"] is True
    assert result["canonical_report_count"] == 2
    assert result["comparison_performed"] is True
    assert result["comparison_ok"] is True
    assert result["canonical_ai_attempted"] is True
    assert result["canonical_ai_sync_ok"] is True
    assert result["canonical_ai_stream_ok"] is True
    assert result["canonical_ai_citation_count"] == 1
    assert result["canonical_ai_answers_match"] is True


def test_workspace_probe_selects_like_for_like_comparison_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acceptance.requests,
        "Session",
        lambda: _MixedScopeWorkspaceSession(),
    )

    result = acceptance.probe_manager_workspace(
        base_url="http://127.0.0.1:5153",
        expect_fixture=True,
        manager="Local Fixture Manager",
        technology="All",
        days=90,
        member_email="fixture.owner1@example.invalid",
        customer_name="Acme Corporation",
        subscription_id="SUB-001",
    )

    assert result["ok"] is True
    assert result["canonical_report_count"] == 3
    assert result["comparison_performed"] is True
    assert result["comparison_ok"] is True


def test_work_machine_probe_rejects_fixture_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acceptance.requests,
        "Session",
        lambda: _FixtureWorkspaceSession(),
    )

    result = acceptance.probe_manager_workspace(
        base_url="http://127.0.0.1:5153",
        expect_fixture=False,
        manager="Local Fixture Manager",
        technology="All",
        days=90,
        member_email="fixture.owner1@example.invalid",
        customer_name="Acme Corporation",
        subscription_id="SUB-001",
    )

    assert result["ok"] is False
    assert result["live_validation_attempted"] is True
    assert result["live_validation_performed"] is False
    assert result["production_accuracy_claimed"] is False


@pytest.mark.parametrize(
    "target",
    (
        "connectivity_ok",
        "preview_ok",
        "history_ok",
        "scoped_history_ok",
        "excel_available",
        "report_ok",
        "compare_ok",
        "ask_sync_ok",
        "ask_sync_fallback",
    ),
)
@pytest.mark.parametrize("tampered_value", ("false", 1, "", _MISSING))
def test_workspace_probe_rejects_nonliteral_endpoint_booleans(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    tampered_value: object,
) -> None:
    monkeypatch.setattr(
        acceptance.requests,
        "Session",
        lambda: _BooleanTamperWorkspaceSession(target, tampered_value),
    )

    result = acceptance.probe_manager_workspace(
        base_url="http://127.0.0.1:5153",
        expect_fixture=True,
        manager="Local Fixture Manager",
        technology="All",
        days=90,
        member_email="fixture.owner1@example.invalid",
        customer_name="Acme Corporation",
        subscription_id="SUB-001",
    )

    assert result["ok"] is False
    assert result["error_count"] > 0


@pytest.mark.parametrize("tampered_value", ("false", 1, "", False, None))
def test_workspace_probe_rejects_nonliteral_explicit_stream_ok(
    monkeypatch: pytest.MonkeyPatch,
    tampered_value: object,
) -> None:
    monkeypatch.setattr(
        acceptance.requests,
        "Session",
        lambda: _BooleanTamperWorkspaceSession("ask_stream_ok", tampered_value),
    )

    result = acceptance.probe_manager_workspace(
        base_url="http://127.0.0.1:5153",
        expect_fixture=True,
        manager="Local Fixture Manager",
        technology="All",
        days=90,
        member_email="fixture.owner1@example.invalid",
        customer_name="Acme Corporation",
        subscription_id="SUB-001",
    )

    assert result["ok"] is False
    assert result["canonical_ai_stream_ok"] is False


def test_workspace_probe_accepts_real_stream_done_without_optional_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acceptance.requests,
        "Session",
        lambda: _FixtureWorkspaceSession(),
    )

    result = acceptance.probe_manager_workspace(
        base_url="http://127.0.0.1:5153",
        expect_fixture=True,
        manager="Local Fixture Manager",
        technology="All",
        days=90,
        member_email="fixture.owner1@example.invalid",
        customer_name="Acme Corporation",
        subscription_id="SUB-001",
    )

    assert result["canonical_ai_stream_ok"] is True


@pytest.mark.parametrize(
    ("target", "tampered_values"),
    (
        ("connectivity_live_flag", ("false", 1, "", False)),
        ("preview_live_flag", ("false", 1, "", _MISSING)),
    ),
)
def test_work_machine_probe_rejects_nonliteral_explicit_live_markers(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    tampered_values: tuple[object, ...],
) -> None:
    for tampered_value in tampered_values:
        monkeypatch.setattr(
            acceptance.requests,
            "Session",
            lambda value=tampered_value: _BooleanTamperWorkspaceSession(
                target,
                value,
                live=True,
            ),
        )
        result = acceptance.probe_manager_workspace(
            base_url="http://127.0.0.1:5153",
            expect_fixture=False,
            manager="Local Fixture Manager",
            technology="All",
            days=90,
            member_email="fixture.owner1@example.invalid",
            customer_name="Acme Corporation",
            subscription_id="SUB-001",
        )

        assert result["ok"] is False
        assert result["error_count"] > 0


def test_work_machine_probe_accepts_real_connectivity_payload_without_fixture_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acceptance.requests,
        "Session",
        lambda: _BooleanTamperWorkspaceSession(
            "",
            None,
            live=True,
            production_connectivity_shape=True,
        ),
    )

    result = acceptance.probe_manager_workspace(
        base_url="http://127.0.0.1:5153",
        expect_fixture=False,
        manager="Local Fixture Manager",
        technology="All",
        days=90,
        member_email="fixture.owner1@example.invalid",
        customer_name="Acme Corporation",
        subscription_id="SUB-001",
    )

    assert result["ok"] is True
    assert result["live_validation_performed"] is True


def test_fixture_readiness_rejects_truthy_nonboolean_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RunningProcess:
        @staticmethod
        def poll() -> None:
            return None

    times = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(
        acceptance.time,
        "monotonic",
        lambda: next(times, 2.0),
    )
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        acceptance.requests,
        "get",
        lambda *_args, **_kwargs: _Response(
            200,
            {
                "ok": "false",
                "mode": acceptance.SOURCE_MODE,
                "live_validation_performed": False,
            },
        ),
    )

    with pytest.raises(TimeoutError, match="did not become ready"):
        acceptance._wait_for_fixture_runtime(  # noqa: SLF001
            "http://127.0.0.1:5153",
            _RunningProcess(),
            timeout=1.0,
        )


def test_workspace_probe_does_not_treat_legacy_workbooks_as_comparison_grade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acceptance.requests,
        "Session",
        lambda: _LegacyOnlyWorkspaceSession(),
    )

    result = acceptance.probe_manager_workspace(
        base_url="http://127.0.0.1:5153",
        expect_fixture=True,
        manager="Local Fixture Manager",
        technology="All",
        days=90,
        member_email="fixture.owner1@example.invalid",
        customer_name="Acme Corporation",
        subscription_id="SUB-001",
    )

    assert result["report_view_ok"] is True
    assert result["canonical_report_count"] == 0
    assert result["comparison_performed"] is False
    assert result["comparison_ok"] is False
    assert result["ok"] is False


def _passing_gates(profile: str) -> dict[str, dict[str, Any]]:
    names = (
        {
            "fixture_manifest",
            "source_contracts",
            "snowflake_capabilities",
            "degraded_http",
            "decision_reports",
            "report_matrix",
            "multi_manager_reports",
            "multi_manager_isolation",
            "ai_features",
            "manager_workspace",
            "ask_ai_replay",
        }
        if profile == "local"
        else {
            "candidate_identity",
            "runtime_identity",
            "decision_reports",
            "report_matrix",
            "ai_features",
            "manager_workspace",
            "ask_ai_replay",
        }
    )
    return {
        name: {
            "ok": True,
            "status": "passed",
            "live_validation_performed": profile == "work-machine",
        }
        for name in names
    }


def test_local_summary_can_never_be_reclassified_as_live() -> None:
    summary = acceptance._acceptance_summary(  # noqa: SLF001
        profile="local",
        started_at="2026-08-03T12:00:00Z",
        gates=_passing_gates("local"),
        sensitive_artifacts_retained=False,
        sensitive_dir=None,
    )

    assert summary["all_passed"] is True
    assert summary["fixture_validation_performed"] is True
    assert summary["fixture_validation_passed"] is True
    assert summary["live_validation_attempted"] is False
    assert summary["live_validation_performed"] is False
    assert summary["live_validation_passed"] is False
    assert summary["production_accuracy_claimed"] is False
    assert summary["release_ready"] is False


def test_skipped_gate_prevents_complete_or_live_claim() -> None:
    gates = _passing_gates("work-machine")
    gates["report_matrix"] = acceptance._skipped_gate(  # noqa: SLF001
        "operator requested skip"
    )
    summary = acceptance._acceptance_summary(  # noqa: SLF001
        profile="work-machine",
        started_at="2026-08-03T12:00:00Z",
        gates=gates,
        sensitive_artifacts_retained=False,
        sensitive_dir=None,
    )

    assert summary["acceptance_complete"] is False
    assert summary["all_passed"] is False
    assert summary["live_validation_performed"] is False
    assert summary["live_validation_passed"] is False
    assert summary["skipped_gates"] == ["report_matrix"]


@pytest.mark.parametrize("tampered_value", ["true", 1])
def test_summary_rejects_truthy_non_boolean_gate_results(tampered_value: Any) -> None:
    gates = _passing_gates("local")
    gates["source_contracts"]["ok"] = tampered_value

    summary = acceptance._acceptance_summary(  # noqa: SLF001
        profile="local",
        started_at="2026-08-03T12:00:00Z",
        gates=gates,
        sensitive_artifacts_retained=False,
        sensitive_dir=None,
    )

    assert summary["acceptance_complete"] is False
    assert summary["all_passed"] is False


@pytest.mark.parametrize("tampered_value", ["true", 1])
def test_summary_rejects_truthy_non_boolean_live_evidence(tampered_value: Any) -> None:
    gates = _passing_gates("work-machine")
    gates["runtime_identity"]["live_validation_performed"] = tampered_value

    summary = acceptance._acceptance_summary(  # noqa: SLF001
        profile="work-machine",
        started_at="2026-08-03T12:00:00Z",
        gates=gates,
        sensitive_artifacts_retained=False,
        sensitive_dir=None,
    )

    assert summary["acceptance_complete"] is True
    assert summary["live_validation_performed"] is False
    assert summary["live_validation_passed"] is False


@pytest.mark.parametrize("target", ("question", "canonical_predicate"))
@pytest.mark.parametrize("tampered_value", ("false", 1, "", None))
def test_replay_gate_requires_literal_boolean_results(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    tampered_value: object,
) -> None:
    from tests.ask_ai_eval import runner

    results = [
        runner.QuestionResult(
            question_id=f"Q-{index:03d}",
            portfolio="fixture",
            category="fixture",
            passed=True,
            predicate_results=(
                [{"type": "must_match_canonical_metric", "passed": True}]
                if index < acceptance.EXPECTED_REPLAY_CANONICAL_CHECKS
                else []
            ),
        )
        for index in range(acceptance.EXPECTED_REPLAY_QUESTIONS)
    ]
    if target == "question":
        results[0].passed = tampered_value
    else:
        results[0].predicate_results[0]["passed"] = tampered_value
    monkeypatch.setattr(runner, "run_all", lambda **_kwargs: results)

    gate = acceptance.run_replay_gate()

    assert gate["ok"] is False


def test_work_machine_parser_requires_scope_without_offering_credentials() -> None:
    parser = acceptance.build_parser()
    args = parser.parse_args(
        [
            "work-machine",
            "--candidate-dmg",
            "/approved/AdoptIQ-v1.0.4-build114.dmg",
            "--candidate-manifest",
            "/approved/candidate.json",
            "--manager",
            "Manager One",
            "--member-email",
            "member@example.invalid",
            "--customer-name",
            "Customer One",
            "--subscription-id",
            "SUB-001",
            "--as-of",
            "2026-08-03T12:00:00Z",
        ]
    )

    assert args.profile == "work-machine"
    assert args.subscription_id == "SUB-001"
    help_text = parser.format_help().casefold()
    assert "github token" not in help_text
    assert "password" not in help_text
    assert "secret" not in help_text


def _complete_matrix_payload(scenario_keys: list[str]) -> dict[str, object]:
    return {
        "all_passed": True,
        "scenario_keys_requested": scenario_keys,
        "scenario_keys_expected": scenario_keys,
        "scenario_count_expected": len(scenario_keys),
        "scenario_keys_completed": scenario_keys,
        "scenario_count_completed": len(scenario_keys),
        "scenario_keys_missing": [],
        "scenario_keys_unexpected": [],
        "scenario_keys_completed_duplicate": [],
        "scenario_inventory_exact": True,
        "scenarios_completed": len(scenario_keys),
        "results": [
            {"scenario": key, "all_passed": True} for key in scenario_keys
        ],
        "cross_report_source_consistency": {
            "ok": True,
            "comparison_requirement_met": True,
            "comparisons": 6,
            "comparisons_expected": 6,
            "required_report_families": [
                "compact", "comprehensive", "leader", "renewal",
            ],
            "projected_fields": [
                "count", "identity_sha256", "attribution_sha256",
                "attributed_record_count", "source_state",
            ],
            "required_family_set_group_count": 1,
            "report_family_sets_compared": [[
                "compact", "comprehensive", "leader", "renewal",
            ]],
            "mismatches": [],
            "freshness_mismatches": [],
            "read_errors": [],
        },
        "r114_audit_inventory_exact": True,
        "r114_audit_scenario_count_expected": len(scenario_keys),
        "r114_audit_scenario_count_completed": len(scenario_keys),
        "r114_audit_skipped": False,
        "r114_critical_scenarios": [],
    }


def test_matrix_projection_accepts_two_scope_groups_with_one_unique_family_set() -> None:
    payload = _complete_matrix_payload(
        [
            "a_leader",
            "b_comprehensive",
            "c_comprehensive",
            "d_leader",
            "e_compact",
            "f_renewal",
            "g_subscription",
        ]
    )
    consistency = payload["cross_report_source_consistency"]
    assert isinstance(consistency, dict)
    consistency["required_family_set_group_count"] = 2
    consistency["comparisons"] = 12
    consistency["comparisons_expected"] = 12

    projection = acceptance._project_matrix(payload)  # noqa: SLF001

    assert projection["projected_ok"] is True
    assert projection["source_consistency_required_family_set_group_count"] == 2
    assert projection["source_consistency_comparison_count"] == 12


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("comparisons", 6),
        ("comparisons_expected", 6),
        ("required_family_set_group_count", 1),
        (
            "report_family_sets_compared",
            [
                ["compact", "comprehensive", "leader", "renewal"],
                ["compact", "comprehensive", "leader", "renewal"],
            ],
        ),
    ),
)
def test_matrix_projection_rejects_inconsistent_group_comparison_inventory(
    field: str,
    value: object,
) -> None:
    payload = _complete_matrix_payload(
        [
            "a_leader",
            "b_comprehensive",
            "c_comprehensive",
            "d_leader",
            "e_compact",
            "f_renewal",
            "g_subscription",
        ]
    )
    consistency = payload["cross_report_source_consistency"]
    assert isinstance(consistency, dict)
    consistency.update(
        {
            "required_family_set_group_count": 2,
            "comparisons": 12,
            "comparisons_expected": 12,
        }
    )
    consistency[field] = value

    assert acceptance._project_matrix(payload)["projected_ok"] is False  # noqa: SLF001


def _passing_literal_boolean_projector_payload(
    projector_name: str,
) -> tuple[Any, dict[str, Any], str]:
    if projector_name == "lab":
        return (
            acceptance._project_lab,  # noqa: SLF001
            {"all_reconciled": True, "sanitized": True},
            "all_reconciled",
        )
    if projector_name == "local_http":
        return (
            acceptance._project_local_http,  # noqa: SLF001
            {
                "all_passed": True,
                "source_mode": acceptance.SOURCE_MODE,
                "live_validation_performed": False,
                "scenario_inventory_complete": True,
                "report_probes_enabled": True,
                "sanitized": True,
                "results": [
                    {
                        "route_checks": {
                            "manager_decision_workspace_preview": {"ok": True}
                        }
                    }
                ],
            },
            "all_passed",
        )
    if projector_name == "matrix":
        return (
            acceptance._project_matrix,  # noqa: SLF001
            _complete_matrix_payload(
                [
                    "a_leader",
                    "b_comprehensive",
                    "c_comprehensive",
                    "d_leader",
                    "e_compact",
                    "f_renewal",
                    "g_subscription",
                ]
            ),
            "all_passed",
        )
    if projector_name == "multi_manager":
        from report_iteration_loop import build_local_acceptance_multi_manager_matrix

        keys = sorted(build_local_acceptance_multi_manager_matrix())
        return (
            acceptance._project_multi_manager_matrix,  # noqa: SLF001
            {
                "all_passed": True,
                "scenario_keys_requested": keys,
                "scenarios_completed": len(keys),
                "results": [
                    {"scenario": key, "all_passed": True} for key in keys
                ],
            },
            "all_passed",
        )
    if projector_name == "source_contracts":
        return (
            acceptance._project_source_contracts,  # noqa: SLF001
            {
                "all_passed": True,
                "sanitized": True,
                "source_mode": acceptance.SOURCE_MODE,
                "live_validation_performed": False,
                "checks": {
                    check: True
                    for check in acceptance.REQUIRED_SOURCE_CONTRACT_CHECKS
                },
                "query_trace": {"query_count": 2},
            },
            "all_passed",
        )
    if projector_name == "snowflake_capabilities":
        return (
            acceptance._project_snowflake_capabilities,  # noqa: SLF001
            {
                "all_passed": True,
                "mode": "local",
                "row_values_queried": False,
                "live_validation_performed": False,
                "allowed_table_count": 1,
                "accessible_table_count": 1,
                "all_allowed_tables_accessible": True,
                "tables": [
                    {
                        "table": "FIXTURE_DB.REPORTING.ALLOWED",
                        "access_state": "simulated_available",
                    }
                ],
                "policy_blocked_tables": [
                    {
                        "table": "FIXTURE_DB.REPORTING.BLOCKED",
                        "probe_attempted": False,
                        "state": "blocked_by_policy",
                    }
                ],
            },
            "all_passed",
        )
    raise AssertionError(f"unknown projector case: {projector_name}")


@pytest.mark.parametrize(
    "projector_name",
    (
        "lab",
        "local_http",
        "matrix",
        "multi_manager",
        "source_contracts",
        "snowflake_capabilities",
    ),
)
@pytest.mark.parametrize("tampered_value", ("false", 1, "", _MISSING))
def test_projectors_reject_nonliteral_top_level_green_booleans(
    projector_name: str,
    tampered_value: object,
) -> None:
    projector, payload, field = _passing_literal_boolean_projector_payload(
        projector_name
    )
    assert projector(payload)["projected_ok"] is True
    if tampered_value is _MISSING:
        payload.pop(field)
    else:
        payload[field] = tampered_value

    assert projector(payload)["projected_ok"] is False


@pytest.mark.parametrize(
    "nested_case",
    (
        "lab_sanitized",
        "local_http_sanitized",
        "local_http_workspace",
        "matrix_result",
        "multi_manager_result",
        "source_contracts_sanitized",
        "source_contract_check",
    ),
)
@pytest.mark.parametrize("tampered_value", ("false", 1, "", _MISSING))
def test_projectors_reject_nonliteral_nested_green_booleans(
    nested_case: str,
    tampered_value: object,
) -> None:
    projector_name = {
        "lab_sanitized": "lab",
        "local_http_sanitized": "local_http",
        "local_http_workspace": "local_http",
        "matrix_result": "matrix",
        "multi_manager_result": "multi_manager",
        "source_contracts_sanitized": "source_contracts",
        "source_contract_check": "source_contracts",
    }[nested_case]
    projector, payload, _ = _passing_literal_boolean_projector_payload(
        projector_name
    )

    if nested_case.endswith("sanitized"):
        target = payload
        field = "sanitized"
    elif nested_case == "local_http_workspace":
        target = payload["results"][0]["route_checks"][
            "manager_decision_workspace_preview"
        ]
        field = "ok"
    elif nested_case in {"matrix_result", "multi_manager_result"}:
        target = payload["results"][0]
        field = "all_passed"
    else:
        target = payload["checks"]
        field = "secondary_attribution"
    assert isinstance(target, dict)
    if tampered_value is _MISSING:
        target.pop(field)
    else:
        target[field] = tampered_value

    assert projector(payload)["projected_ok"] is False


@pytest.mark.parametrize("tampered_value", ("false", 1, "", _MISSING))
def test_run_command_requires_literal_projected_ok(
    monkeypatch: pytest.MonkeyPatch,
    tampered_value: object,
) -> None:
    monkeypatch.setattr(
        acceptance.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["fixture-command"],
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    def projector(_payload: object) -> dict[str, object]:
        if tampered_value is _MISSING:
            return {}
        return {"projected_ok": tampered_value}

    gate = acceptance._run_command(  # noqa: SLF001
        ["fixture-command"],
        projector=projector,
    )

    assert gate["ok"] is False


@pytest.mark.parametrize("tampered_value", ("false", 1, "", _MISSING))
def test_decision_and_ai_live_flags_do_not_coerce_nonliteral_booleans(
    tampered_value: object,
) -> None:
    decision_payload: dict[str, object] = {
        "all_passed": True,
        "mode_executed": "live",
        "live_validation_performed": True,
        "repeatability": {"ok": True},
        "failures_requiring_review": [],
        "passes": [_passing_decision_pass(), _passing_decision_pass()],
    }
    ai_payload: dict[str, object] = {
        "all_automated_checks_passed": True,
        "validation_mode": "live",
        "live_validation_performed": True,
        "repeatability": {"ok": True},
        "failures_requiring_review": [],
        "passes": [_passing_ai_pass(), _passing_ai_pass()],
    }
    for payload in (decision_payload, ai_payload):
        if tampered_value is _MISSING:
            payload.pop("live_validation_performed")
        else:
            payload["live_validation_performed"] = tampered_value

    decision = acceptance._project_decision_reports(  # noqa: SLF001
        decision_payload
    )
    ai = acceptance._project_ai(ai_payload)  # noqa: SLF001

    assert decision["projected_ok"] is True
    assert decision["live_validation_performed"] is False
    assert ai["projected_ok"] is True
    assert ai["live_validation_performed"] is False


@pytest.mark.parametrize("tampered_value", ("false", 1, "", _MISSING))
def test_ai_local_flag_does_not_coerce_nonliteral_booleans(
    tampered_value: object,
) -> None:
    payload: dict[str, object] = {
        "all_automated_checks_passed": True,
        "validation_mode": "local_acceptance",
        "local_validation_performed": True,
        "repeatability": {"ok": True},
        "failures_requiring_review": [],
        "passes": [_passing_ai_pass(), _passing_ai_pass()],
    }
    if tampered_value is _MISSING:
        payload.pop("local_validation_performed")
    else:
        payload["local_validation_performed"] = tampered_value

    projection = acceptance._project_ai(payload)  # noqa: SLF001

    assert projection["projected_ok"] is True
    assert projection["fixture_validation_performed"] is False


@pytest.mark.parametrize("projector_name", ("decision", "ai"))
def test_projector_output_cannot_false_green_summary_live_evidence(
    projector_name: str,
) -> None:
    gates = _passing_gates("work-machine")
    if projector_name == "decision":
        projection = acceptance._project_decision_reports(  # noqa: SLF001
            {
                "all_passed": True,
                "mode_executed": "live",
                "live_validation_performed": "false",
                "repeatability": {"ok": True},
                "failures_requiring_review": [],
                "passes": [_passing_decision_pass(), _passing_decision_pass()],
            }
        )
        gate_name = "decision_reports"
    else:
        projection = acceptance._project_ai(  # noqa: SLF001
            {
                "all_automated_checks_passed": True,
                "validation_mode": "live",
                "live_validation_performed": "false",
                "repeatability": {"ok": True},
                "failures_requiring_review": [],
                "passes": [_passing_ai_pass(), _passing_ai_pass()],
            }
        )
        gate_name = "ai_features"
    projected_ok = projection.pop("projected_ok")
    gates[gate_name] = {
        **projection,
        "ok": projected_ok,
        "status": "passed" if projected_ok is True else "failed",
    }

    summary = acceptance._acceptance_summary(  # noqa: SLF001
        profile="work-machine",
        started_at="2026-08-03T12:00:00Z",
        gates=gates,
        sensitive_artifacts_retained=False,
        sensitive_dir=None,
    )

    assert summary["acceptance_complete"] is True
    assert summary["live_validation_performed"] is False
    assert summary["live_validation_passed"] is False


def test_report_matrix_projection_counts_results_without_retaining_artifacts() -> None:
    scenario_keys = [
        "a_leader",
        "b_comprehensive",
        "c_comprehensive",
        "d_leader",
        "e_compact",
        "f_renewal",
        "g_subscription",
    ]
    payload = _complete_matrix_payload(scenario_keys)
    payload["results"][0]["debug_path"] = "/sensitive/one.docx"
    payload["results"][1]["debug_path"] = "/sensitive/two.xlsx"
    payload["results"][2]["customer"] = "Sensitive Customer"
    projection = acceptance._project_matrix(payload)  # noqa: SLF001

    assert projection["projected_ok"] is True
    assert projection["scenario_count"] == 7
    assert projection["passed_count"] == 7
    assert projection["all_report_blocks_requested"] is True
    assert projection["scenario_inventory_complete"] is True
    assert projection["source_consistency_ok"] is True
    assert projection["r114_audit_ok"] is True
    assert "/sensitive" not in json.dumps(projection)


def test_partial_report_matrix_cannot_pass_full_acceptance() -> None:
    projection = acceptance._project_matrix(  # noqa: SLF001
        _complete_matrix_payload(["a_leader", "g_subscription"])
    )

    assert projection["projected_ok"] is False
    assert projection["all_report_blocks_requested"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("scenario_inventory_exact",), False),
        (("cross_report_source_consistency", "comparison_requirement_met"), False),
        (("cross_report_source_consistency", "mismatches"), [{"kind": "drift"}]),
        (("r114_audit_inventory_exact",), False),
        (("r114_critical_scenarios",), ["a_leader"]),
    ],
)
def test_report_matrix_projection_fails_closed_on_accuracy_gap(
    path: tuple[str, ...], value: object
) -> None:
    payload = _complete_matrix_payload(
        [
            "a_leader",
            "b_comprehensive",
            "c_comprehensive",
            "d_leader",
            "e_compact",
            "f_renewal",
            "g_subscription",
        ]
    )
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    assert acceptance._project_matrix(payload)["projected_ok"] is False  # noqa: SLF001


@pytest.mark.parametrize(
    "path",
    (
        ("scenario_keys_missing",),
        ("scenario_keys_unexpected",),
        ("scenario_keys_completed_duplicate",),
        ("r114_critical_scenarios",),
        ("cross_report_source_consistency", "mismatches"),
        ("cross_report_source_consistency", "freshness_mismatches"),
        ("cross_report_source_consistency", "read_errors"),
    ),
)
@pytest.mark.parametrize("tampered_value", ("false", 1, "", False, _MISSING))
def test_report_matrix_requires_literal_empty_negative_inventories(
    path: tuple[str, ...],
    tampered_value: object,
) -> None:
    payload = _complete_matrix_payload(
        [
            "a_leader",
            "b_comprehensive",
            "c_comprehensive",
            "d_leader",
            "e_compact",
            "f_renewal",
            "g_subscription",
        ]
    )
    target = payload
    for part in path[:-1]:
        target = target[part]
        assert isinstance(target, dict)
    if tampered_value is _MISSING:
        target.pop(path[-1])
    else:
        target[path[-1]] = tampered_value

    assert acceptance._project_matrix(payload)["projected_ok"] is False  # noqa: SLF001


@pytest.mark.parametrize("tampered_value", ("false", 1, "", True, _MISSING))
def test_report_matrix_requires_explicit_literal_r114_not_skipped(
    tampered_value: object,
) -> None:
    payload = _complete_matrix_payload(
        [
            "a_leader",
            "b_comprehensive",
            "c_comprehensive",
            "d_leader",
            "e_compact",
            "f_renewal",
            "g_subscription",
        ]
    )
    if tampered_value is _MISSING:
        payload.pop("r114_audit_skipped")
    else:
        payload["r114_audit_skipped"] = tampered_value

    assert acceptance._project_matrix(payload)["projected_ok"] is False  # noqa: SLF001


def test_summary_writer_is_atomic_and_body_is_sanitized(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    payload = {
        "schema_version": acceptance.SUMMARY_SCHEMA,
        "sanitized": True,
        "live_validation_performed": False,
    }

    acceptance._write_json(path, payload)  # noqa: SLF001

    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert not list(tmp_path.glob(".*.tmp"))
