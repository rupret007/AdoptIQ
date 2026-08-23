#!/usr/bin/env python3
"""Classify hosted GitHub Actions failures. Round 169.2.

Hosted jobs on the free private plan can finish in ~3s with empty ``steps``
and ``runner_name=""`` because GitHub never assigned a runner. The 2026-08-23
check-run annotation was:

    The job was not started because recent account payments have failed or
    your spending limit needs to be increased.

That is billing / spend-limit, not a missing Makefile target and not a
reason to change this Cisco repo's visibility. AdoptIQ stays PRIVATE.
Local/fixture proof (``make offline-sim-local``) is authoritative until
spend limit or billing is restored. Making the repo public would also
unblock hosted runners; that option is documented only — it is not allowed.
"""
# Round 169.2

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "testdata" / "hosted_actions" / "empty_runner_billing.json"
SCHEMA_VERSION = "hosted-actions-classify/v1"

BILLING_OR_SPEND_LIMIT_MARKERS = (
    "recent account payments have failed",
    "spending limit needs to be increased",
    "your spending limit",
    "spending limit",
)
MISSING_TARGET_MARKERS = (
    "no rule to make target",
    "missing separator",
)
HOSTED_CI_BLOCKED_UNTIL = (
    "spend_limit_or_billing_restored",
    "or_public_repo_not_allowed_cisco_private",
)


def _annotation_text(job: dict[str, Any]) -> str:
    parts: list[str] = []
    annotations = job.get("annotations")
    if isinstance(annotations, list):
        for item in annotations:
            if isinstance(item, dict):
                parts.append(str(item.get("message") or ""))
                parts.append(str(item.get("title") or ""))
            else:
                parts.append(str(item))
    for key in ("message", "error"):
        value = job.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts).casefold()


def _steps(job: dict[str, Any]) -> list[Any]:
    raw = job.get("steps")
    return raw if isinstance(raw, list) else []


def classify_hosted_job(job: dict[str, Any] | None) -> dict[str, Any]:
    """Map a hosted job payload to a local, honest failure class.

    Empty-runner + billing annotation is never ``missing_make_target``.
    """
    # Round 169.2
    if not isinstance(job, dict):
        return {
            "schema_version": SCHEMA_VERSION,
            "round": "169.2",
            "kind": "invalid_payload",
            "reason": "job_must_be_object",
            "is_missing_make_target": False,
            "is_repo_visibility_issue": False,
            "repo_must_stay_private": True,
            "local_proof_is_authoritative": True,
            "hosted_ci_billing_blocked": False,
            "hosted_ci_blocked_until": list(HOSTED_CI_BLOCKED_UNTIL),
            "empty_runner": False,
            "empty_steps": True,
            "billing_annotation_matched": False,
            "live_validation_performed": False,
            "production_accuracy_claimed": False,
            "release_ready": False,
        }

    runner = str(job.get("runner_name") or "").strip()
    steps = _steps(job)
    empty_runner = runner == ""
    empty_steps = len(steps) == 0
    text = _annotation_text(job)
    billing = any(marker in text for marker in BILLING_OR_SPEND_LIMIT_MARKERS)
    missing_target_text = any(marker in text for marker in MISSING_TARGET_MARKERS)
    runner_never_started = empty_runner or empty_steps

    if runner_never_started and billing:
        kind = "hosted_runner_not_assigned"
        reason = "billing_or_spend_limit"
        is_missing = False
        billing_blocked = True
    elif runner_never_started:
        kind = "hosted_runner_not_assigned"
        reason = "runner_never_assigned"
        is_missing = False
        billing_blocked = False
    elif missing_target_text:
        kind = "missing_make_target"
        reason = "make_recipe_absent"
        is_missing = True
        billing_blocked = False
    else:
        kind = "workflow_step_failed"
        reason = "hosted_step_ran_and_failed"
        is_missing = False
        billing_blocked = False

    return {
        "schema_version": SCHEMA_VERSION,
        "round": "169.2",
        "kind": kind,
        "reason": reason,
        "is_missing_make_target": is_missing,
        "is_repo_visibility_issue": False,
        "repo_must_stay_private": True,
        "local_proof_is_authoritative": True,
        "hosted_ci_billing_blocked": billing_blocked,
        "hosted_ci_blocked_until": list(HOSTED_CI_BLOCKED_UNTIL),
        "empty_runner": empty_runner,
        "empty_steps": empty_steps,
        "billing_annotation_matched": billing,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "release_ready": False,
    }


def classify_hosted_job_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return classify_hosted_job(None)
    return classify_hosted_job(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="Hosted job JSON (default: checked-in empty-runner billing fixture)",
    )
    parser.add_argument("--require-kind")
    parser.add_argument("--require-reason")
    parser.add_argument(
        "--require-not-missing-target",
        action="store_true",
        help="Exit 1 if the classifier treats this as a missing make target",
    )
    args = parser.parse_args(argv)
    result = classify_hosted_job_file(args.input)
    print(json.dumps(result, indent=2, sort_keys=True))
    ok = True
    if args.require_kind and result["kind"] != args.require_kind:
        ok = False
    if args.require_reason and result["reason"] != args.require_reason:
        ok = False
    if args.require_not_missing_target and result["is_missing_make_target"]:
        ok = False
    if result["kind"] == "invalid_payload":
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
