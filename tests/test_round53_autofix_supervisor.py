"""Round 53 tests for the guarded report quality/autofix supervisor."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.run_report_accuracy_autofix_loop import (
    _agent_command,
    _load_summary,
    _results_needing_repair,
    _runner_failure_result,
    _write_repair_bundle,
    parse_args,
)


def test_round53_supervisor_defaults_repair_on_recommendations(tmp_path: Path):
    args = parse_args(
        [
            "--downloads-dir",
            str(tmp_path),
            "--bundle-dir",
            str(tmp_path / "bundles"),
            "--repair-agent",
            "none",
        ]
    )

    assert args.repair_on_recommendations is True
    assert _agent_command(args) == []


def test_round53_supervisor_selects_failed_and_recommended_results():
    summary = {
        "results": [
            {"scenario": "compact", "all_passed": False},
            {
                "scenario": "leader",
                "all_passed": True,
                "quality": {"details": {"recommendation_count": 1}},
            },
            {
                "scenario": "renewal",
                "all_passed": True,
                "quality": {"details": {"recommendation_count": 0}},
            },
        ]
    }

    selected = _results_needing_repair(summary, repair_on_recommendations=True)

    assert [item["scenario"] for item in selected] == ["compact", "leader"]


def test_round53_repair_bundle_records_guardrails(tmp_path: Path):
    args = parse_args(
        [
            "--downloads-dir",
            str(tmp_path),
            "--bundle-dir",
            str(tmp_path / "bundles"),
            "--run-id",
            "round53-test",
            "--repair-agent",
            "none",
        ]
    )

    bundle_path = _write_repair_bundle(
        summary={"summary_path": "/tmp/summary.json"},
        result={
            "scenario": "compact",
            "all_passed": False,
            "quality": {"details": {"errors": ["missing source"]}},
        },
        args=args,
        attempt=1,
    )

    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert payload["scenario"] == "compact"
    assert payload["attempt"] == 1
    assert any("Do not commit" in item for item in payload["guardrails"])
    assert "source-cited" in payload["quality_goal"]


def test_round53_runner_failure_without_summary_is_repairable():
    """Round 53.2: missing runner summaries fail closed instead of disappearing."""

    result = _runner_failure_result(
        "compact",
        {"returncode": 2, "stderr": "boom"},
        None,
    )
    selected = _results_needing_repair(
        {"results": [result]},
        repair_on_recommendations=False,
    )

    assert selected == [result]
    assert result["all_passed"] is False
    assert result["operational"]["details"]["reason"] == "runner_failed_or_missing_summary"


def test_round53_runner_failure_carries_custom_reason():
    """Round 53.3: callers can stamp a load-failure reason on the failure."""

    result = _runner_failure_result(
        "leader",
        {"returncode": 0, "stderr": ""},
        Path("/tmp/missing.json"),
        reason="invalid_summary_json: Expecting value: line 1 column 1",
    )

    assert result["operational"]["details"]["reason"].startswith("invalid_summary_json")
    assert result["all_passed"] is False
    assert "invalid_summary_json" in result["final_status"]["error"]


def test_round53_load_summary_returns_error_for_invalid_json(tmp_path: Path):
    """Round 53.3: a corrupt summary file must not crash the supervisor."""

    bad = tmp_path / "summary.json"
    bad.write_text("{not json", encoding="utf-8")

    loaded = _load_summary(bad)

    assert isinstance(loaded, dict)
    assert loaded.get("_load_error", "").startswith("invalid_summary_json")


def test_round53_load_summary_returns_error_for_non_object(tmp_path: Path):
    """Round 53.3: a top-level non-object summary must be rejected."""

    bad = tmp_path / "summary.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")

    loaded = _load_summary(bad)

    assert loaded == {"_load_error": "summary_not_object"}


def test_round53_default_agent_command_no_unsafe_flags(tmp_path: Path):
    """Round 53.3: defaults must not auto-authorize broad edits."""

    args = parse_args(
        [
            "--downloads-dir",
            str(tmp_path),
            "--bundle-dir",
            str(tmp_path / "bundles"),
        ]
    )
    cmd = _agent_command(args)

    assert "--trust" not in cmd
    assert "--force" not in cmd
    assert "--permission-mode" not in cmd

    claude_args = parse_args(
        [
            "--downloads-dir",
            str(tmp_path),
            "--bundle-dir",
            str(tmp_path / "bundles2"),
            "--repair-agent",
            "claude",
        ]
    )
    claude_cmd = _agent_command(claude_args)
    assert claude_cmd == ["claude", "--print"]
