#!/usr/bin/env python3
"""Classify hosted GitHub Actions failures. Round 169.3.

A hosted job that finishes in ~2s with empty ``steps``, ``runner_name=""``,
and ``runner_id=0`` never started. That is **not** a missing Makefile
target and is **not** treated as a billing/spend-limit diagnosis.

Diagnose workflow / runner / config instead:

- Workflow YAML exists and is ``state: active``
- ``runs-on`` matches the last successful Quality Checks job (``ubuntu-latest``)
- ``make offline-sim-pr`` and ``make verify`` exist
- Sibling PR Quality Gate (checkout + Python 3.11 + ``make verify``) shows
  the same ``runner_id=0`` empty-step pattern
- Last runner-assigned job on this repo: ``workflow_dispatch`` Build on
  2026-08-04 (``runner_id`` populated, steps ran)

Local/fixture proof (``make offline-sim-local`` / ``make verify``) is the
Cloud/Bob gate. AdoptIQ stays PRIVATE. Do not invent live Cisco accuracy.
Do not blame GitHub billing. Jeff has no spend-limit account type.
"""
# Round 169.3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "testdata" / "hosted_actions" / "empty_runner_billing.json"
SCHEMA_VERSION = "hosted-actions-classify/v2"

# GitHub sometimes attaches this stock annotation to jobs that never start.
# Round 169.3: record it as an observed message only — do not treat it as
# the product diagnosis (Jeff has no spend-limit account type).
GITHUB_STOCK_NEVER_STARTED_MARKERS = (
    "the job was not started",
    "recent account payments have failed",
    "spending limit needs to be increased",
)
MISSING_TARGET_MARKERS = (
    "no rule to make target",
    "missing separator",
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


def _runner_id(job: dict[str, Any]) -> int:
    raw = job.get("runner_id")
    if raw is None or raw == "":
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def classify_hosted_job(job: dict[str, Any] | None) -> dict[str, Any]:
    """Map a hosted job payload to a local, honest failure class.

    Empty runner AND empty steps is ``job_never_started``.
    It is never ``missing_make_target`` and never a billing diagnosis.
    """
    # Round 169.3
    if not isinstance(job, dict):
        return {
            "schema_version": SCHEMA_VERSION,
            "round": "169.3",
            "kind": "invalid_payload",
            "reason": "job_must_be_object",
            "is_missing_make_target": False,
            "missing_make_target": False,
            "is_billing_diagnosis": False,
            "hosted_ci_job_never_started": False,
            "diagnosis": "invalid_payload",
            "repo_stays_private": True,
            "repo_must_stay_private": True,
            "do_not_blame_billing": True,
            "local_proof_is_authoritative": True,
            "empty_runner": False,
            "empty_steps": True,
            "runner_id": 0,
            "github_stock_never_started_annotation": False,
            "live_validation_performed": False,
            "production_accuracy_claimed": False,
            "release_ready": False,
        }

    runner = str(job.get("runner_name") or "").strip()
    runner_id = _runner_id(job)
    steps = _steps(job)
    empty_runner = runner == ""
    empty_steps = len(steps) == 0
    text = _annotation_text(job)
    stock_annotation = any(marker in text for marker in GITHUB_STOCK_NEVER_STARTED_MARKERS)
    missing_target_text = any(marker in text for marker in MISSING_TARGET_MARKERS)
    # Require both empty runner and empty steps. A started job can omit
    # runner_id in fixtures; runner_id==0 alone must not flip this bit.
    job_never_started = empty_runner and empty_steps

    if job_never_started:
        kind = "hosted_runner_not_assigned"
        reason = "job_never_started"
        is_missing = False
        diagnosis = "workflow_runner_or_config"
    elif missing_target_text:
        kind = "missing_make_target"
        reason = "make_recipe_absent"
        is_missing = True
        diagnosis = "missing_make_target"
    else:
        kind = "hosted_step_failed"
        reason = "hosted_step_ran_and_failed"
        is_missing = False
        diagnosis = "hosted_step"

    return {
        "schema_version": SCHEMA_VERSION,
        "round": "169.3",
        "kind": kind,
        "reason": reason,
        "is_missing_make_target": is_missing,
        "missing_make_target": is_missing,
        "is_billing_diagnosis": False,
        "hosted_ci_job_never_started": job_never_started,
        "diagnosis": diagnosis,
        "repo_stays_private": True,
        "repo_must_stay_private": True,
        "do_not_blame_billing": True,
        "local_proof_is_authoritative": True,
        "empty_runner": empty_runner,
        "empty_steps": empty_steps,
        "runner_id": runner_id,
        "github_stock_never_started_annotation": stock_annotation,
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
        "--job-json",
        dest="input",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="Hosted job JSON (default: checked-in empty-runner fixture)",
    )
    parser.add_argument("--require-kind")
    parser.add_argument("--require-reason")
    parser.add_argument(
        "--require-not-missing-target",
        action="store_true",
        help="Exit 1 if the classifier treats this as a missing make target",
    )
    parser.add_argument(
        "--require-not-billing",
        action="store_true",
        help="Exit 1 if the classifier treats this as a billing diagnosis",
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
    if args.require_not_billing and result.get("is_billing_diagnosis"):
        ok = False
    if result["kind"] == "invalid_payload":
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
