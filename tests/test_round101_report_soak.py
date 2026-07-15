"""Round 101 tests for the time-boxed live report soak supervisor."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOAK_PATH = PROJECT_ROOT / "scripts" / "run_report_soak.py"


def _load_soak_module():
    spec = importlib.util.spec_from_file_location("run_report_soak", SOAK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(tmp_path: Path, **overrides):
    manifest = tmp_path / "baseline_manifest.json"
    manifest.write_text('{"scenarios": {}}', encoding="utf-8")
    values = {
        "base_url": "http://127.0.0.1:5151",
        "downloads_dir": tmp_path / "soak",
        "duration_seconds": 250,
        "min_remaining_seconds": 60,
        "scenario_timeout": 1800,
        "request_timeout": 120,
        "download_timeout": 300,
        "runner_timeout": 2400,
        "poll_interval": 5.0,
        "status_timeout": 1.0,
        "max_failures": 1,
        "allow_existing_running": False,
        "min_free_gb": 0.0,
        "scenarios": ["compact", "leader"],
        "run_id": "round101-test",
        "baseline_mode": "manifest",
        "baseline_manifest": manifest,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_round101_runner_command_uses_strict_manifest_mode(tmp_path: Path) -> None:
    soak = _load_soak_module()
    args = _args(tmp_path)
    child_dir = tmp_path / "child"

    command = soak.build_runner_command(
        args,
        scenario="compact",
        run_id="round101-test-compact",
        child_downloads_dir=child_dir,
        scenario_timeout=123,
    )

    assert command[0].endswith("python") or "python" in command[0]
    assert str(PROJECT_ROOT / "scripts" / "run_report_iteration_loop.py") in command
    assert command[command.index("--scenarios") + 1] == "compact"
    assert command[command.index("--baseline-mode") + 1] == "manifest"
    assert command[command.index("--baseline-manifest") + 1] == str(args.baseline_manifest)
    assert "--strict" in command
    assert "--stop-on-failure" in command
    assert command[command.index("--timeout") + 1] == "123"
    assert command[command.index("--request-timeout") + 1] == "120"
    assert command[command.index("--download-timeout") + 1] == "300"


def test_round101_runner_command_can_disable_stale_baseline_manifest(tmp_path: Path) -> None:
    soak = _load_soak_module()
    args = _args(tmp_path, baseline_mode="off")

    command = soak.build_runner_command(
        args,
        scenario="leader",
        run_id="round101-test-leader",
        child_downloads_dir=tmp_path / "child",
    )

    assert command[command.index("--baseline-mode") + 1] == "off"
    assert "--baseline-manifest" not in command


def test_round101_iteration_harness_download_timeout_is_configurable() -> None:
    source = (PROJECT_ROOT / "report_iteration_loop.py").read_text(encoding="utf-8")
    assert "request_timeout_seconds" in source
    assert "--request-timeout" in source
    assert "timeout=self.config.request_timeout_seconds" in source
    assert "download_timeout_seconds" in source
    assert "--download-timeout" in source
    assert "timeout=self.config.download_timeout_seconds" in source
    assert "response = self.session.get(status_url, timeout=30)" not in source
    assert "response = self.session.get(url, timeout=30)" not in source
    assert "response = self.session.get(url, timeout=120)" not in source


def test_round101_soak_aborts_when_reports_already_running(tmp_path: Path) -> None:
    soak = _load_soak_module()
    args = _args(tmp_path)

    def command_runner(*_a, **_kw):  # pragma: no cover - should not run
        raise AssertionError("runner should not start when active reports exist")

    summary = soak.run_soak(
        args,
        monotonic=lambda: 0.0,
        command_runner=command_runner,
        running_probe=lambda *_a, **_kw: [{"analysis_id": "abc", "status": "running"}],
    )

    assert summary["passed"] is False
    assert summary["aborted"] is True
    assert summary["abort_reason"] == "active_reports_present"
    assert summary["events"] == []
    assert Path(summary["summary_path"]).exists()


def test_round101_status_url_rejects_non_local_targets() -> None:
    soak = _load_soak_module()

    assert soak._status_url("http://127.0.0.1:5151").endswith("/api/status/all")
    assert soak._status_url("http://localhost:5151").endswith("/api/status/all")

    for value in ("file:///tmp/adoptiq", "https://example.com"):
        try:
            soak._status_url(value)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"unsafe base URL accepted: {value}")


def test_round101_extract_running_reports_supports_statuses_envelope() -> None:
    soak = _load_soak_module()

    running = soak._extract_running_reports({
        "ok": True,
        "statuses": [
            {"analysis_id": "done", "status": "completed"},
            {
                "analysis_id": "active",
                "status": "running",
                "report_type": "comprehensive",
                "manager": "Brian Frazier",
                "progress": 89,
                "current_step": "AI Customer Analysis",
            },
        ],
    })

    assert running == [
        {
            "analysis_id": "active",
            "report_type": "comprehensive",
            "manager": "Brian Frazier",
            "progress": 89,
            "current_step": "AI Customer Analysis",
        }
    ]


def test_round101_soak_runs_until_remaining_time_is_too_small(tmp_path: Path) -> None:
    soak = _load_soak_module()
    args = _args(tmp_path)
    clock = {"now": 0.0}

    def monotonic() -> float:
        return clock["now"]

    def command_runner(command, *, cwd, timeout):
        del cwd, timeout
        scenario = command[command.index("--scenarios") + 1]
        child_dir = Path(command[command.index("--downloads-dir") + 1])
        summary_path = child_dir / f"{scenario}_summary.json"
        summary_path.write_text(
            json.dumps({"all_passed": True, "aborted": False, "results": [{"scenario": scenario}]}),
            encoding="utf-8",
        )
        clock["now"] += 100.0
        return {
            "command": command,
            "returncode": 0,
            "stdout": f"[summary] {summary_path}\n",
            "stderr": "",
            "started_at_utc": "2026-05-27T00:00:00Z",
            "completed_at_utc": "2026-05-27T00:01:00Z",
        }

    summary = soak.run_soak(
        args,
        monotonic=monotonic,
        command_runner=command_runner,
        running_probe=lambda *_a, **_kw: [],
    )

    assert summary["passed"] is True
    assert summary["abort_reason"] == "deadline_remaining_too_small"
    assert summary["events_completed"] == 2
    assert [event["scenario"] for event in summary["events"]] == ["compact", "leader"]
    assert all(event["passed"] for event in summary["events"])
    assert Path(summary["summary_path"]).name == "soak_summary.json"


def test_round101_soak_stops_on_first_failed_child(tmp_path: Path) -> None:
    soak = _load_soak_module()
    args = _args(tmp_path, scenarios=["compact", "renewal", "leader"], duration_seconds=1000)
    clock = {"now": 0.0}

    def command_runner(command, *, cwd, timeout):
        del cwd, timeout
        scenario = command[command.index("--scenarios") + 1]
        child_dir = Path(command[command.index("--downloads-dir") + 1])
        summary_path = child_dir / f"{scenario}_summary.json"
        summary_path.write_text(
            json.dumps({"all_passed": False, "aborted": False, "results": [{"scenario": scenario}]}),
            encoding="utf-8",
        )
        clock["now"] += 10.0
        return {
            "command": command,
            "returncode": 2,
            "stdout": f"[summary] {summary_path}\n",
            "stderr": "failed",
            "started_at_utc": "2026-05-27T00:00:00Z",
            "completed_at_utc": "2026-05-27T00:01:00Z",
        }

    summary = soak.run_soak(
        args,
        monotonic=lambda: clock["now"],
        command_runner=command_runner,
        running_probe=lambda *_a, **_kw: [],
    )

    assert summary["passed"] is False
    assert summary["aborted"] is True
    assert summary["abort_reason"] == "max_failures_reached"
    assert summary["events_completed"] == 1
    assert summary["events"][0]["scenario"] == "compact"


def test_round101_app_import_does_not_start_corpus_background_under_pytest() -> None:
    source = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "_r101_under_pytest" in source
    assert '"pytest" in sys.modules' in source
    assert "and not _r101_under_pytest" in source


def test_round101_main_app_run_is_explicitly_threaded() -> None:
    source = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "threaded=True" in source
    assert "cannot starve /status and /ping" in source


def test_round101_download_resolver_tries_direct_path_before_recursive_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app_simple

    output_root = tmp_path / "outputs"
    report_path = output_root / "Brian_Frazier" / "Comprehensive" / "AdoptIQ_Report_Test.docx"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("docx placeholder", encoding="utf-8")

    monkeypatch.setattr(app_simple, "_r92_candidate_output_roots", lambda create_current=True: (output_root,))

    def fail_rglob(self: Path, pattern: str):  # pragma: no cover - failure path
        raise AssertionError(f"rglob should not run for direct path: {self} {pattern}")

    monkeypatch.setattr(Path, "rglob", fail_rglob)

    assert app_simple._r92_resolve_output_artifact(str(report_path)) == str(report_path)


def test_round101_compact_summary_declares_high_risk_scale_for_consistency_gate() -> None:
    source = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    formatter_source = (PROJECT_ROOT / "executive_intelligence_formatter.py").read_text(encoding="utf-8")

    assert "Round 101: Compact's Word narrative" in source
    assert "'high_risk_scale': cm.RISK_SCALE_0_TO_10" in source
    assert "Round 101: preserve the report path's declared high-risk scale" in formatter_source
    assert 'portfolio_metrics["high_risk_scale"] = risk_summary.get("high_risk_scale")' in formatter_source


def test_round101_consistency_gate_honors_declared_high_risk_scale() -> None:
    import pandas as pd

    from report_consistency import validate_report_consistency

    risk_data = {
        "Band High": {"risk_score_0_100": 58.0, "risk_score_0_10": 5.8, "risk_band": "HIGH"},
        "Legacy Red": {"risk_score_0_100": 42.0, "risk_score_0_10": 4.2, "risk_band": "MEDIUM", "color": "Red"},
    }

    strict = validate_report_consistency(
        pd.DataFrame(),
        pd.DataFrame(),
        portfolio_metrics={"high_risk_customers": 2, "high_risk_scale": "0_to_10"},
        risk_data=risk_data,
    )
    assert strict["is_valid"]
    assert strict["metrics"]["canonical_high_risk_count"] == 2

    default_scale = validate_report_consistency(
        pd.DataFrame(),
        pd.DataFrame(),
        portfolio_metrics={"high_risk_customers": 2},
        risk_data=risk_data,
    )
    assert not default_scale["is_valid"]
    assert any("high_risk_customers invariant violated" in error for error in default_scale["errors"])
