#!/usr/bin/env python3
"""Round 169.1: prove the hosted offline-sim CI surface exists and is honest.

This is the local/Cloud answer to a 3-second empty-step GitHub Actions
failure.  It checks the committed workflow file and Makefile targets so a
missing ``make offline-sim-pr`` recipe cannot be confused with a runner
that never started.

Honesty stamps stay false.  No secrets, customer rows, or CSOne.
"""
# Round 169.1

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "offline-sim.yml"
MAKEFILE = REPO_ROOT / "Makefile"
SIM_SCRIPT = REPO_ROOT / "scripts" / "run_offline_bob_sim.sh"
REQUIRED_MAKE_TARGETS = (
    "offline-sim",
    "offline-sim-pr",
    "metamorphic-acceptance",
    "offline-pipeline-smoke",
    "jeff-only-stubs",
    "offline-sim-ci-surface",
)
FORBIDDEN_WORKFLOW_TOKENS = (
    "SNOWFLAKE_PASSWORD",
    "KEEPER",
    "secrets.",
    "ADOPTIQ_ADMIN_SECRET_KEY",
    "live_validation_performed=true",
    "release_ready=true",
)
HONESTY_FALSE = (
    "live_validation_performed",
    "production_accuracy_claimed",
    "release_ready",
)


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _makefile_has_target(text: str, name: str) -> bool:
    return bool(re.search(rf"^{re.escape(name)}\s*:", text, flags=re.MULTILINE))


def run_ci_surface_check() -> dict[str, Any]:
    workflow = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.is_file() else ""
    makefile = MAKEFILE.read_text(encoding="utf-8") if MAKEFILE.is_file() else ""
    script = SIM_SCRIPT.read_text(encoding="utf-8") if SIM_SCRIPT.is_file() else ""
    checks: list[dict[str, Any]] = []

    checks.append(
        _check(
            "workflow_file_present",
            WORKFLOW.is_file() and "make offline-sim-pr" in workflow,
            str(WORKFLOW.relative_to(REPO_ROOT)),
        )
    )
    forbidden_hits = [
        marker
        for marker in FORBIDDEN_WORKFLOW_TOKENS
        if marker != "secrets." and marker in workflow
    ]
    checks.append(
        _check(
            "workflow_is_pull_request_and_secret_free",
            "pull_request:" in workflow and "contents: read" in workflow and not forbidden_hits,
            "offline-sim.yml has pull_request trigger, contents:read, no secret names"
            if not forbidden_hits
            else "forbidden tokens: " + ", ".join(forbidden_hits),
        )
    )
    # Explicit secrets. check (the token includes a dot).
    checks.append(
        _check(
            "workflow_does_not_reference_github_secrets",
            "secrets." not in workflow,
            "no ${{ secrets.* }} in offline-sim.yml",
        )
    )
    missing_targets = [name for name in REQUIRED_MAKE_TARGETS if not _makefile_has_target(makefile, name)]
    checks.append(
        _check(
            "makefile_offline_sim_targets",
            not missing_targets,
            "missing: " + ", ".join(missing_targets) if missing_targets else "all required targets present",
        )
    )
    checks.append(
        _check(
            "makefile_metamorphic_stays_on_round169_ssot",
            "run_round169_metamorphic_acceptance.py" in makefile,
            "metamorphic-acceptance uses the official Round 169 runner",
        )
    )
    checks.append(
        _check(
            "sim_script_calls_round169_metamorphic",
            "run_round169_metamorphic_acceptance.py" in script,
            str(SIM_SCRIPT.relative_to(REPO_ROOT)),
        )
    )
    for key in HONESTY_FALSE:
        checks.append(
            _check(
                f"sim_script_{key}_stays_false",
                f"{key}=false" in script.lower() or f'"{key}": false' in script.lower() or f"{key}=False" in script,
                f"{key} remains false in the Cloud/Bob entrypoint",
            )
        )
    checks.append(
        _check(
            "empty_step_3s_failure_is_not_a_missing_target",
            WORKFLOW.is_file() and not missing_targets,
            "If GitHub reports empty steps / no runner_name, read the check-run annotation; do not treat it as a missing make target",
        )
    )

    return {
        "schema_version": "offline-sim-ci-surface/v1",
        "round": "169.1",
        "sanitized": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "release_ready": False,
        "all_passed": all(item["passed"] for item in checks),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = run_ci_surface_check()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in payload["checks"]:
            mark = "PASS" if item["passed"] else "FAIL"
            print(f"{mark} {item['name']}: {item['detail']}")
        print(json.dumps({"all_passed": payload["all_passed"], "live_validation_performed": False}, sort_keys=True))
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
