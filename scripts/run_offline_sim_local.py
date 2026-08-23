#!/usr/bin/env python3
"""Local/fixture offline-sim proof. Round 169.3.

Hosted jobs that never start (``runner_id=0``, empty steps) are a
workflow/runner/config diagnosis, not a missing make target and not
billing. This runner is the Cloud/Bob gate that does **not** need a
hosted runner:

  ci-surface → hosted-actions classify → resolve-only → synthetic metrics
  → jeff stubs → fixture KPI → manager UX probe

It never claims live Cisco accuracy. Honesty stamps stay false.
AdoptIQ stays PRIVATE — do not change visibility.
"""
# Round 169.3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check_offline_sim_ci_surface import run_ci_surface_check  # noqa: E402
from scripts.classify_hosted_actions_failure import (  # noqa: E402
    DEFAULT_FIXTURE,
    classify_hosted_job_file,
)
from scripts.run_jeff_only_stubs import run_jeff_only_stubs  # noqa: E402
from scripts.run_metamorphic_acceptance import run_metamorphic_acceptance  # noqa: E402
from scripts.run_offline_pipeline_smoke import probe_manager_ux  # noqa: E402
from scripts.run_synthetic_csone_metrics import run_synthetic_csone_metrics  # noqa: E402

SCHEMA_VERSION = "offline-sim-local/v2"
EMPTY_RUNNER_FIXTURE = DEFAULT_FIXTURE


def _safe_summary_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        relative = resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] != ".adoptiq-acceptance":
        raise ValueError("in-repository summaries must stay under .adoptiq-acceptance")
    return resolved


def _resolve_only() -> dict[str, Any]:
    env = os.environ.copy()
    completed = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "run_offline_bob_sim.sh"), "--resolve-only"],
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    payload: dict[str, Any] = {}
    stdout = (completed.stdout or "").strip()
    if stdout:
        try:
            payload = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            payload = {}
    payload["exit_code"] = completed.returncode
    return payload


def run_offline_sim_local(output_dir: Path) -> dict[str, Any]:
    """Prove the offline sim locally without hosted runners or make verify."""
    # Round 169.3
    output_dir = _safe_summary_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    surface = run_ci_surface_check()
    classified = classify_hosted_job_file(EMPTY_RUNNER_FIXTURE)
    resolve = _resolve_only()
    metrics = run_synthetic_csone_metrics()
    stubs = run_jeff_only_stubs(output_dir / "jeff-only-stubs")
    fixture_kpi = run_metamorphic_acceptance()
    ux = probe_manager_ux()

    classify_ok = (
        classified.get("kind") == "hosted_runner_not_assigned"
        and classified.get("reason") == "job_never_started"
        and classified.get("is_missing_make_target") is False
        and classified.get("is_billing_diagnosis") is False
        and classified.get("repo_must_stay_private") is True
        and classified.get("local_proof_is_authoritative") is True
    )
    resolve_ok = (
        resolve.get("exit_code") == 0
        and resolve.get("live_validation_performed") is False
        and resolve.get("corpus_kind") in {"synthetic_checked_in", "external_operator_dir"}
    )
    honesty = (
        surface.get("live_validation_performed") is False
        and metrics.get("live_validation_performed") is False
        and stubs.get("live_validation_performed") is False
        and fixture_kpi.get("live_validation_performed") is False
        and ux.get("live_validation_performed") is False
        and classified.get("release_ready") is False
    )
    all_passed = bool(
        surface.get("all_passed")
        and classify_ok
        and resolve_ok
        and metrics.get("ok")
        and stubs.get("all_passed")
        and fixture_kpi.get("all_passed")
        and ux.get("ok")
        and honesty
    )
    raw_corpus = str(resolve.get("corpus_kind") or "")
    corpus_kind = "synthetic" if raw_corpus in {"synthetic", "synthetic_checked_in"} else raw_corpus
    return {
        "schema_version": SCHEMA_VERSION,
        "round": "169.3",
        "ok": all_passed,
        "sanitized": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "release_ready": False,
        "repo_must_stay_private": True,
        "hosted_ci_job_never_started": True,
        "local_proof_is_authoritative": True,
        "ready_for_karen": all_passed,
        "corpus_kind": corpus_kind,
        "synthetic_csone_ok": bool(metrics.get("ok")),
        "all_passed": all_passed,
        "gates": {
            "offline_sim_ci_surface": {"ok": bool(surface.get("all_passed"))},
            "hosted_actions_classify": {
                "ok": classify_ok,
                "kind": classified.get("kind"),
                "reason": classified.get("reason"),
            },
            "resolve_only": {
                "ok": resolve_ok,
                "corpus_kind": resolve.get("corpus_kind"),
            },
            "synthetic_csone_metrics": {
                "ok": bool(metrics.get("ok")),
                "customer_count": metrics.get("customer_count"),
                "tac_count": metrics.get("tac_count"),
            },
            "jeff_only_stubs": {"ok": bool(stubs.get("all_passed"))},
            "fixture_kpi_metamorphic": {"ok": bool(fixture_kpi.get("all_passed"))},
            "manager_ux": {"ok": bool(ux.get("ok")), "path_count": len(ux.get("paths") or [])},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".adoptiq-acceptance" / "offline-bob-sim" / "local",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full local-proof payload (Round 169.3)",
    )
    args = parser.parse_args(argv)
    payload = run_offline_sim_local(Path(args.output_dir))
    summary = _safe_summary_path(Path(args.output_dir) / "offline_sim_local_summary.json")
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps({"all_passed": payload["all_passed"], "summary": str(summary)}, sort_keys=True))
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
