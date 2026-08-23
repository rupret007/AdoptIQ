"""Round 169.3: hosted runner-not-assigned vs local/fixture offline-sim proof.

A hosted empty-runner job is never a missing make target and is never a
billing/spend-limit diagnosis. Repo stays PRIVATE. No live Cisco.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLASSIFY = ROOT / "scripts" / "classify_hosted_actions_failure.py"
LOCAL_PROOF = ROOT / "scripts" / "run_offline_sim_local.py"
METRICS = ROOT / "scripts" / "run_synthetic_csone_metrics.py"
PLAYBOOK = ROOT / "OFFLINE_SIM_PLAYBOOK.md"
HANDOFF = ROOT / "WORK_MAC_CURSOR_HANDOFF.md"
SIM = ROOT / "scripts" / "run_offline_bob_sim.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "offline-sim.yml"
FIXTURES = ROOT / "testdata" / "hosted_actions"
EMPTY_RUNNER = FIXTURES / "empty_runner_billing.json"
STEP_FAIL = FIXTURES / "step_failed_after_runner.json"
MISSING_TARGET = FIXTURES / "missing_make_target_after_runner.json"


def _classify(path: Path) -> dict:
    raw = subprocess.check_output(
        [sys.executable, str(CLASSIFY), "--job-json", str(path)],
        cwd=str(ROOT),
        text=True,
    )
    return json.loads(raw)


def test_empty_runner_is_not_a_missing_make_target() -> None:
    payload = _classify(EMPTY_RUNNER)
    assert payload["kind"] == "hosted_runner_not_assigned"
    assert payload["reason"] == "job_never_started"
    assert payload["is_billing_diagnosis"] is False
    assert payload["hosted_ci_job_never_started"] is True
    assert payload["missing_make_target"] is False
    assert payload["github_stock_never_started_annotation"] is True
    assert payload["diagnosis"] == "workflow_runner_or_config"
    assert payload["repo_stays_private"] is True
    assert payload["do_not_blame_billing"] is True
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["release_ready"] is False
    assert payload["runner_id"] == 0


def test_require_not_billing_passes_on_empty_runner() -> None:
    subprocess.check_call(
        [
            sys.executable,
            str(CLASSIFY),
            "--job-json",
            str(EMPTY_RUNNER),
            "--require-reason",
            "job_never_started",
            "--require-not-billing",
        ],
        cwd=str(ROOT),
    )


def test_step_failure_after_runner_is_not_job_never_started() -> None:
    payload = _classify(STEP_FAIL)
    assert payload["kind"] == "hosted_step_failed"
    assert payload["hosted_ci_job_never_started"] is False
    assert payload["is_billing_diagnosis"] is False
    assert payload["missing_make_target"] is False
    assert payload["do_not_blame_billing"] is True


def test_missing_make_target_requires_a_started_runner() -> None:
    payload = _classify(MISSING_TARGET)
    assert payload["kind"] == "missing_make_target"
    assert payload["missing_make_target"] is True
    assert payload["hosted_ci_job_never_started"] is False
    assert payload["is_billing_diagnosis"] is False


def test_playbook_stays_private_and_does_not_blame_billing() -> None:
    text = PLAYBOOK.read_text(encoding="utf-8")
    folded = text.casefold()
    assert "stay **private**" in folded or "stay private" in folded
    assert "do not change visibility" in folded
    assert "job never started" in folded
    assert "workflow/runner/config" in folded
    assert "do not blame github billing" in folded
    assert "no spend-limit account type" in folded
    assert "billing-blocked until public or spend limit" not in folded
    assert "live_validation_performed=false" in folded
    assert "production_accuracy_claimed=false" in folded
    assert "release_ready=false" in folded


def test_handoff_ready_for_karen_without_billing_story() -> None:
    text = HANDOFF.read_text(encoding="utf-8")
    folded = text.casefold()
    assert "stay **private**" in folded or "stay private" in folded
    assert "job never started" in folded
    assert "do not blame github billing" in folded
    assert "ready for karen" in folded
    assert "keep the pr **draft**" in folded or "stay **draft**" in folded
    assert "billing-blocked until public or spend limit" not in folded
    assert "live_validation_performed=false" in folded


def test_workflow_matches_last_successful_quality_checks_shape() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    folded = text.casefold()
    assert "python-version: '3.11'" in text
    assert "actions/setup-python@v5" in text
    assert "actions/checkout@v4" in text
    assert "runs-on: ubuntu-latest" in text
    assert "timeout-minutes" not in folded
    assert "secrets" not in folded
    assert "python-version: '3.12'" not in text


def test_sim_script_classifies_job_never_started() -> None:
    text = SIM.read_text(encoding="utf-8")
    assert "--require-reason job_never_started" in text
    assert "--require-not-billing" in text
    assert "billing_or_spend_limit" not in text


def test_synthetic_csone_metrics_count_customers_and_tac() -> None:
    raw = subprocess.check_output(
        [sys.executable, str(METRICS), "--json"],
        cwd=str(ROOT),
        text=True,
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["customer_count"] == 3
    assert payload["tac_count"] == 6
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["release_ready"] is False
    assert payload["email_domain_ok"] is True
    assert "example.invalid" in " ".join(payload.get("emails") or [])
    assert all("cisco.com" not in name.casefold() for name in payload["customers"])
    assert all("cisco.com" not in mail.casefold() for mail in payload.get("emails") or [])


def test_local_offline_sim_proof_is_green() -> None:
    raw = subprocess.check_output(
        [sys.executable, str(LOCAL_PROOF), "--json"],
        cwd=str(ROOT),
        text=True,
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["hosted_ci_job_never_started"] is True
    assert payload.get("hosted_ci_billing_blocked") is None
    assert payload["corpus_kind"] == "synthetic"
    assert payload["synthetic_csone_ok"] is True
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["release_ready"] is False
    assert payload["ready_for_karen"] is True
    assert payload["ready_for_live_cisco"] is False
    assert payload["schema_version"] == "offline-sim-local/v4"
    assert payload["gates"]["pipeline_smoke"]["ok"] is True
    assert payload["scorecard"]["required_failed"] == []
    assert "verify" in payload["scorecard"]["skipped"]
    assert "live_cisco" in payload["scorecard"]["unknown"]


def test_honesty_stamps_stay_false_in_local_proof_schema() -> None:
    text = LOCAL_PROOF.read_text(encoding="utf-8")
    assert '"live_validation_performed": False' in text
    assert '"production_accuracy_claimed": False' in text
    assert '"release_ready": False' in text
    assert "hosted_ci_job_never_started" in text
    assert "hosted_ci_billing_blocked" not in text
