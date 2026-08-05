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
                "event: done\ndata: {\"ok\": true}",
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


def test_child_summary_projection_drops_scope_names_paths_and_answers() -> None:
    projected = acceptance._project_ai(  # noqa: SLF001
        {
            "all_automated_checks_passed": True,
            "validation_mode": "live",
            "live_validation_performed": True,
            "repeatability": {"ok": True},
            "passes": [{"ok": True}, {"ok": True}],
            "manager": "Sensitive Manager",
            "customer_name": "Sensitive Customer",
            "answer": "Sensitive generated answer",
            "sensitive_evidence_path": "C:/Sensitive/customer-evidence.json",
        }
    )
    rendered = json.dumps(projected, sort_keys=True)

    assert projected["projected_ok"] is True
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
            "passes": [
                {"scopes": {"team": {"ok": True}}},
                {"scopes": {"team": {"ok": True}}},
            ],
        }
    )

    assert projection["projected_ok"] is False
    assert projection["scope_count"] == 1
    assert projection["passing_scope_count"] == 1


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
            "degraded_http",
            "decision_reports",
            "report_matrix",
            "ai_features",
            "manager_workspace",
            "ask_ai_replay",
        }
        if profile == "local"
        else {
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


def test_work_machine_parser_requires_scope_without_offering_credentials() -> None:
    parser = acceptance.build_parser()
    args = parser.parse_args(
        [
            "work-machine",
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
    projection = acceptance._project_matrix(  # noqa: SLF001
        {
            "all_passed": True,
            "scenario_keys_requested": scenario_keys,
            "scenarios_completed": len(scenario_keys),
            "results": [
                {"all_passed": True, "debug_path": "/sensitive/one.docx"},
                {"all_passed": True, "debug_path": "/sensitive/two.xlsx"},
                {"all_passed": True, "customer": "Sensitive Customer"},
                {"all_passed": True},
                {"all_passed": True},
                {"all_passed": True},
                {"all_passed": True},
            ],
        }
    )

    assert projection["projected_ok"] is True
    assert projection["scenario_count"] == 7
    assert projection["passed_count"] == 7
    assert projection["all_report_blocks_requested"] is True
    assert "/sensitive" not in json.dumps(projection)


def test_partial_report_matrix_cannot_pass_full_acceptance() -> None:
    projection = acceptance._project_matrix(  # noqa: SLF001
        {
            "all_passed": True,
            "scenario_keys_requested": ["a_leader", "g_subscription"],
            "scenarios_completed": 2,
            "results": [
                {"all_passed": True},
                {"all_passed": True},
            ],
        }
    )

    assert projection["projected_ok"] is False
    assert projection["all_report_blocks_requested"] is False


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
