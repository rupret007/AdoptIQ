"""Round 169.4: PR offline-sim-pr job lives on the last runner-assigned workflow."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".github" / "workflows" / "build.yml"
DISPATCH = ROOT / ".github" / "workflows" / "offline-sim.yml"


def test_build_yml_hosts_pull_request_offline_sim_pr() -> None:
    text = BUILD.read_text(encoding="utf-8")
    # PyYAML 1.1 treats the key `on` as boolean True; inspect jobs via `jobs`.
    parsed = yaml.safe_load(text)
    assert "\n  pull_request:\n" in text
    assert "offline-sim-pr" in parsed["jobs"]
    job = parsed["jobs"]["offline-sim-pr"]
    assert job["if"] == "github.event_name == 'pull_request'"
    assert job["runs-on"] == "ubuntu-latest"
    run_steps = [step.get("run", "") for step in job["steps"] if isinstance(step, dict)]
    assert any("make offline-sim-pr" in run for run in run_steps)
    assert any("check_offline_sim_ci_surface.py" in run for run in run_steps)


def test_packaging_and_quality_jobs_skip_on_pull_request() -> None:
    parsed = yaml.safe_load(BUILD.read_text(encoding="utf-8"))
    for name in (
        "developer-candidate-policy",
        "quality-checks",
        "build-mac",
        "build-windows",
    ):
        assert parsed["jobs"][name]["if"] == "github.event_name == 'workflow_dispatch'"


def test_dispatch_workflow_does_not_create_pull_request_checks() -> None:
    text = DISPATCH.read_text(encoding="utf-8")
    assert "pull_request:" not in text
    assert "workflow_dispatch:" in text
    assert "make offline-sim-pr" in text
    assert "secrets" not in text.casefold()
    assert "live_validation_performed=true" not in text
    assert "release_ready=true" not in text
