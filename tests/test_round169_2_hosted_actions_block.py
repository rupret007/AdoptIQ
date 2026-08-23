"""Round 169.2: hosted CI billing-block vs local/fixture offline-sim proof."""

from __future__ import annotations

import json
from pathlib import Path

from data_normalization import customer_names_match
from scripts.check_offline_sim_ci_surface import run_ci_surface_check
from scripts.classify_hosted_actions_failure import (
    DEFAULT_FIXTURE,
    classify_hosted_job,
    classify_hosted_job_file,
)
from scripts.run_offline_sim_local import run_offline_sim_local
from scripts.run_synthetic_csone_metrics import run_synthetic_csone_metrics


ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "OFFLINE_SIM_PLAYBOOK.md"
HANDOFF = ROOT / "WORK_MAC_CURSOR_HANDOFF.md"
MAKEFILE = ROOT / "Makefile"
SIM_SCRIPT = ROOT / "scripts" / "run_offline_bob_sim.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "offline-sim.yml"
FIXTURES = ROOT / "testdata" / "hosted_actions"
BILLING = FIXTURES / "empty_runner_billing.json"
EMPTY = FIXTURES / "empty_runner_no_annotation.json"
STEP_FAIL = FIXTURES / "step_failed_after_runner.json"
MISSING_TARGET = FIXTURES / "missing_make_target_after_runner.json"


def _playbook() -> str:
    return PLAYBOOK.read_text(encoding="utf-8")


def _handoff() -> str:
    return HANDOFF.read_text(encoding="utf-8")


def test_billing_empty_runner_is_not_a_missing_make_target() -> None:
    payload = classify_hosted_job_file(BILLING)
    assert payload["kind"] == "hosted_runner_not_assigned"
    assert payload["reason"] == "billing_or_spend_limit"
    assert payload["is_missing_make_target"] is False
    assert payload["is_repo_visibility_issue"] is False
    assert payload["repo_must_stay_private"] is True
    assert payload["local_proof_is_authoritative"] is True
    assert payload["hosted_ci_billing_blocked"] is True
    assert payload["empty_runner"] is True
    assert payload["empty_steps"] is True
    assert payload["billing_annotation_matched"] is True
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["release_ready"] is False
    assert "spend_limit_or_billing_restored" in payload["hosted_ci_blocked_until"]
    assert "or_public_repo_not_allowed_cisco_private" in payload["hosted_ci_blocked_until"]


def test_default_classifier_fixture_is_the_2026_08_23_annotation() -> None:
    assert Path(DEFAULT_FIXTURE).resolve() == BILLING.resolve()
    job = json.loads(BILLING.read_text(encoding="utf-8"))
    message = job["annotations"][0]["message"]
    assert "recent account payments have failed" in message
    assert "spending limit needs to be increased" in message
    assert job["runner_name"] == ""
    assert job["steps"] == []
    assert job["live_validation_performed"] is False


def test_empty_runner_without_annotation_is_still_not_a_missing_target() -> None:
    payload = classify_hosted_job_file(EMPTY)
    assert payload["kind"] == "hosted_runner_not_assigned"
    assert payload["reason"] == "runner_never_assigned"
    assert payload["is_missing_make_target"] is False
    assert payload["repo_must_stay_private"] is True


def test_step_failure_after_runner_is_not_classified_as_billing() -> None:
    payload = classify_hosted_job_file(STEP_FAIL)
    assert payload["kind"] == "workflow_step_failed"
    assert payload["hosted_ci_billing_blocked"] is False
    assert payload["is_missing_make_target"] is False
    assert payload["empty_runner"] is False


def test_missing_make_target_requires_a_runner_and_steps() -> None:
    payload = classify_hosted_job_file(MISSING_TARGET)
    assert payload["kind"] == "missing_make_target"
    assert payload["is_missing_make_target"] is True
    # The same annotation on an empty-runner job must NOT flip to missing target.
    hybrid = json.loads(MISSING_TARGET.read_text(encoding="utf-8"))
    hybrid["runner_name"] = ""
    hybrid["steps"] = []
    blocked = classify_hosted_job(hybrid)
    assert blocked["kind"] == "hosted_runner_not_assigned"
    assert blocked["is_missing_make_target"] is False


def test_invalid_job_payload_stays_honest() -> None:
    payload = classify_hosted_job(None)  # type: ignore[arg-type]
    assert payload["kind"] == "invalid_payload"
    assert payload["release_ready"] is False
    assert payload["repo_must_stay_private"] is True


def test_playbook_stays_private_and_documents_billing_block() -> None:
    text = _playbook()
    folded = text.casefold()
    assert "stay private" in folded
    assert "do not change visibility" in folded
    assert "billing-blocked until public or spend limit" in folded
    assert "free private plan" in folded
    assert "empty-runner" in folded
    assert "spend limit" in folded
    assert "do not run `gh repo edit --visibility public`" in folded
    assert "make offline-sim-local" in text
    assert "classify_hosted_actions_failure.py" in text
    assert "live_validation_performed=false" in text
    assert "release_ready=false" in text
    assert "production_accuracy_claimed=false" in text


def test_handoff_keeps_draft_and_does_not_wait_on_hosted_green() -> None:
    text = _handoff()
    folded = text.casefold()
    assert "stay private" in folded
    assert "do not change visibility" in folded
    assert "billing-blocked until public or spend limit" in folded
    assert "make offline-sim-local" in text
    assert "keep the pr **draft**" in folded or "keep the pr draft" in folded
    assert "do not undraft" in folded
    assert "gh repo edit --visibility public" in text
    assert "do not run" in folded


def test_makefile_wires_local_proof_targets() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "offline-sim-local:" in text
    assert "hosted-actions-classify:" in text
    assert "synthetic-csone-metrics:" in text
    assert "run_offline_sim_local.py" in text
    assert "classify_hosted_actions_failure.py" in text
    assert "run_synthetic_csone_metrics.py" in text
    assert "Round 169.2" in text
    assert "stay PRIVATE" in text


def test_sim_script_classifies_billing_and_counts_synthetic_csone() -> None:
    text = SIM_SCRIPT.read_text(encoding="utf-8")
    assert "classify_hosted_actions_failure.py" in text
    assert "billing_or_spend_limit" in text
    assert "run_synthetic_csone_metrics.py" in text
    assert "hosted_actions_classify" in text
    assert "synthetic_csone_metrics" in text
    assert "offline-bob-sim/v4" in text
    assert "169.2" in text


def test_workflow_stays_secret_free_and_private_repo_safe() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets" not in text.casefold()
    assert "SNOWFLAKE_PASSWORD" not in text
    assert "visibility" not in text.casefold()
    assert "make offline-sim-pr" in text


def test_ci_surface_requires_local_proof_targets() -> None:
    payload = run_ci_surface_check()
    assert payload["all_passed"] is True
    assert payload["round"] == "169.2"
    assert payload["live_validation_performed"] is False
    names = {item["name"] for item in payload["checks"]}
    assert "hosted_actions_classifier_and_local_proof_exist" in names
    assert "makefile_offline_sim_targets" in names


def test_synthetic_csone_metrics_use_canonical_counts() -> None:
    payload = run_synthetic_csone_metrics()
    assert payload["ok"] is True
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["release_ready"] is False
    assert payload["customer_count"] >= 1
    assert payload["tac_count"] >= 1
    assert set(payload["customers"]) <= {
        "Acme Corporation",
        "Beta Industries",
        "Gamma Public Sector",
    }
    assert "@cisco.com" not in json.dumps(payload).casefold()


def test_customer_names_match_does_not_casefold() -> None:
    # Round 132 / 169.2: display normalize does not case-fold.
    assert customer_names_match("Acme Corporation", "Acme Corporation") is True
    assert customer_names_match("Acme Corporation", "acme corporation") is False


def test_offline_sim_local_proves_in_repo_without_hosted_runner() -> None:
    output = ROOT / ".adoptiq-acceptance" / "round169-2-local-proof"
    payload = run_offline_sim_local(output)
    assert payload["all_passed"] is True
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["release_ready"] is False
    assert payload["repo_must_stay_private"] is True
    assert payload["hosted_ci_billing_blocked"] is True
    assert payload["local_proof_is_authoritative"] is True
    failed = [name for name, gate in payload["gates"].items() if not gate.get("ok")]
    assert failed == []


def test_new_round169_2_files_carry_markers_and_stay_honest() -> None:
    paths = (
        ROOT / "scripts" / "classify_hosted_actions_failure.py",
        ROOT / "scripts" / "run_offline_sim_local.py",
        ROOT / "scripts" / "run_synthetic_csone_metrics.py",
        PLAYBOOK,
        HANDOFF,
        BILLING,
    )
    joined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "Round 169.2" in joined
    assert "live_validation_performed=true" not in joined
    assert "release_ready=true" not in joined
    assert "@cisco.com" not in joined.casefold()
    assert "SNOWFLAKE_PASSWORD" not in joined
