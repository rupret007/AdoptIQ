#!/usr/bin/env python3
"""Local/fixture offline-sim proof. Round 169.5.

Hosted jobs that never start (``runner_id=0``, empty steps) are a
workflow/runner/config diagnosis, not a missing make target and not
billing. This runner is the Cloud/Bob local-proof gate:

  ci-surface → hosted-actions classify → resolve-only → pipeline smoke
    (source-contracts + offline decision reports + manager UX)
    → synthetic metrics → jeff stubs → fixture KPI

It does **not** run ``make verify``, the official Round 169 metamorphic
SSoT, the 23-scenario lab, or production-simulation. Those stay on
``make offline-sim-pr`` / ``make offline-sim`` and are scored SKIPPED
here so a local green cannot be misread as a full PR-profile green.

It never claims live Cisco accuracy. Honesty stamps stay false.
AdoptIQ stays PRIVATE — do not change visibility.
"""
# Round 169.5

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check_offline_sim_ci_surface import run_ci_surface_check  # noqa: E402
from scripts.classify_hosted_actions_failure import (  # noqa: E402
    DEFAULT_FIXTURE,
    classify_hosted_job_file,
)
from scripts.offline_sim_scorecard import (  # noqa: E402
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_SKIPPED,
    VERDICT_UNKNOWN,
    corpus_action,
    format_scorecard,
)
from scripts.run_jeff_only_stubs import run_jeff_only_stubs  # noqa: E402
from scripts.run_metamorphic_acceptance import run_metamorphic_acceptance  # noqa: E402
from scripts.run_offline_pipeline_smoke import run_offline_pipeline_smoke  # noqa: E402
from scripts.run_synthetic_csone_metrics import run_synthetic_csone_metrics  # noqa: E402

SCHEMA_VERSION = "offline-sim-local/v4"
EMPTY_RUNNER_FIXTURE = DEFAULT_FIXTURE

_SKIPPED_PR_PROFILE = (
    (
        "verify",
        "PR/full profile only — make verify / make offline-sim-pr",
    ),
    (
        "official_r169_metamorphic",
        "PR/full profile — make metamorphic-acceptance",
    ),
    (
        "local_acceptance_lab",
        "PR/full profile — make local-acceptance-lab",
    ),
    (
        "production_simulation",
        "full profile — make offline-sim",
    ),
)


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


def _invoke(name: str, fn: Callable[[], dict[str, Any]], detail: str = "") -> dict[str, Any]:
    """Run a required gate. Exceptions are FAIL, not a missing scorecard row."""
    # Round 169.5
    try:
        payload = fn()
    except Exception as exc:  # noqa: BLE001 — scorecard must still emit
        return {
            "name": name,
            "verdict": VERDICT_FAIL,
            "ok": False,
            "required": True,
            "status": "failed_exception",
            "detail": f"{type(exc).__name__}: {exc}"[:240],
        }
    return {
        "name": name,
        "verdict": VERDICT_PASS if bool(payload.get("_gate_ok")) else VERDICT_FAIL,
        "ok": bool(payload.get("_gate_ok")),
        "required": True,
        "status": "ran",
        "detail": detail or str(payload.get("_gate_detail") or "")[:240],
        "payload": payload,
    }


def run_offline_sim_local(output_dir: Path) -> dict[str, Any]:
    """Prove the offline sim locally without hosted runners or make verify."""
    # Round 169.5
    output_dir = _safe_summary_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _surface() -> dict[str, Any]:
        payload = run_ci_surface_check()
        payload["_gate_ok"] = payload.get("all_passed") is True and payload.get(
            "live_validation_performed"
        ) is False
        return payload

    def _classify() -> dict[str, Any]:
        classified = classify_hosted_job_file(EMPTY_RUNNER_FIXTURE)
        classified["_gate_ok"] = (
            classified.get("kind") == "hosted_runner_not_assigned"
            and classified.get("reason") == "job_never_started"
            and classified.get("is_missing_make_target") is False
            and classified.get("is_billing_diagnosis") is False
            and classified.get("repo_must_stay_private") is True
            and classified.get("local_proof_is_authoritative") is True
            and classified.get("release_ready") is False
        )
        classified["_gate_detail"] = (
            f"{classified.get('kind')}/{classified.get('reason')}"
        )
        return classified

    def _resolve() -> dict[str, Any]:
        resolve = _resolve_only()
        kind = str(resolve.get("corpus_kind") or "")
        action = corpus_action(kind)
        resolve["_gate_ok"] = (
            resolve.get("exit_code") == 0
            and resolve.get("live_validation_performed") is False
            and action == "run"
        )
        resolve["_gate_detail"] = f"{kind}/{action}"
        return resolve

    def _pipeline() -> dict[str, Any]:
        smoke = run_offline_pipeline_smoke(output_dir / "pipeline-smoke")
        smoke["_gate_ok"] = (
            smoke.get("all_passed") is True
            and smoke.get("live_validation_performed") is False
            and smoke.get("production_accuracy_claimed") is False
        )
        decision = (smoke.get("gates") or {}).get("decision_reports_offline") or {}
        smoke["_gate_detail"] = (
            "17-sheet="
            + str(decision.get("exact_17_sheet_inventory"))
            + " contracts="
            + str(((smoke.get("gates") or {}).get("source_contracts") or {}).get("ok"))
        )
        return smoke

    def _metrics() -> dict[str, Any]:
        metrics = run_synthetic_csone_metrics()
        metrics["_gate_ok"] = (
            metrics.get("ok") is True
            and metrics.get("live_validation_performed") is False
        )
        metrics["_gate_detail"] = (
            f"customers={metrics.get('customer_count')} tac={metrics.get('tac_count')}"
        )
        return metrics

    def _stubs() -> dict[str, Any]:
        stubs = run_jeff_only_stubs(output_dir / "jeff-only-stubs")
        stubs["_gate_ok"] = (
            stubs.get("all_passed") is True
            and stubs.get("live_validation_performed") is False
        )
        return stubs

    def _fixture_kpi() -> dict[str, Any]:
        fixture_kpi = run_metamorphic_acceptance()
        fixture_kpi["_gate_ok"] = (
            fixture_kpi.get("all_passed") is True
            and fixture_kpi.get("live_validation_performed") is False
        )
        return fixture_kpi

    rows = [
        _invoke("offline_sim_ci_surface", _surface),
        _invoke("hosted_actions_classify", _classify),
        _invoke("resolve_only", _resolve),
        _invoke("pipeline_smoke", _pipeline),
        _invoke("synthetic_csone_metrics", _metrics),
        _invoke("jeff_only_stubs", _stubs),
        _invoke("fixture_kpi_metamorphic", _fixture_kpi),
    ]
    for name, reason in _SKIPPED_PR_PROFILE:
        rows.append(
            {
                "name": name,
                "verdict": VERDICT_SKIPPED,
                "ok": None,
                "required": False,
                "status": "skipped_profile_local",
                "detail": reason,
            }
        )
    rows.append(
        {
            "name": "live_cisco",
            "verdict": VERDICT_UNKNOWN,
            "ok": None,
            "required": False,
            "status": "unknown",
            "detail": "Jeff-only — Snowflake / Keeper / CSOne / CircuIT / package / promote",
        }
    )

    payloads = {row["name"]: row.get("payload") or {} for row in rows}
    resolve = payloads.get("resolve_only") or {}
    raw_corpus = str(resolve.get("corpus_kind") or "")
    corpus_kind = (
        "synthetic" if raw_corpus in {"synthetic", "synthetic_checked_in"} else raw_corpus
    )
    failed = [row["name"] for row in rows if row.get("verdict") == VERDICT_FAIL]
    all_passed = not failed
    honesty = (
        (payloads.get("offline_sim_ci_surface") or {}).get("live_validation_performed")
        is False
        and (payloads.get("pipeline_smoke") or {}).get("live_validation_performed")
        is False
        and (payloads.get("synthetic_csone_metrics") or {}).get("live_validation_performed")
        is False
        and (payloads.get("jeff_only_stubs") or {}).get("live_validation_performed")
        is False
        and (payloads.get("fixture_kpi_metamorphic") or {}).get("live_validation_performed")
        is False
    )
    if not honesty:
        all_passed = False
        if "honesty" not in failed:
            rows.append(
                {
                    "name": "honesty",
                    "verdict": VERDICT_FAIL,
                    "ok": False,
                    "required": True,
                    "status": "failed_honesty",
                    "detail": "a local gate omitted live_validation_performed=false",
                }
            )
            failed.append("honesty")

    smoke = payloads.get("pipeline_smoke") or {}
    metrics = payloads.get("synthetic_csone_metrics") or {}
    classified = payloads.get("hosted_actions_classify") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "round": "169.5",
        "ok": all_passed,
        "sanitized": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "release_ready": False,
        "repo_must_stay_private": True,
        "hosted_ci_job_never_started": True,
        "local_proof_is_authoritative": True,
        "ready_for_karen": all_passed,
        "ready_for_live_cisco": False,
        "verify_ran": False,
        "official_r169_metamorphic_ran": False,
        "corpus_kind": corpus_kind,
        "synthetic_csone_ok": bool(metrics.get("ok")),
        "all_passed": all_passed,
        "scorecard": {
            "gates": [
                {
                    "name": row["name"],
                    "verdict": row["verdict"],
                    "ok": row.get("ok"),
                    "status": row.get("status") or "",
                    "detail": row.get("detail") or "",
                }
                for row in rows
            ],
            "required_failed": failed,
            "skipped": [row["name"] for row in rows if row.get("verdict") == VERDICT_SKIPPED],
            "unknown": [row["name"] for row in rows if row.get("verdict") == VERDICT_UNKNOWN],
        },
        "gates": {
            "offline_sim_ci_surface": {
                "ok": (payloads.get("offline_sim_ci_surface") or {}).get("all_passed")
                is True,
                "verdict": next(
                    row["verdict"]
                    for row in rows
                    if row["name"] == "offline_sim_ci_surface"
                ),
            },
            "hosted_actions_classify": {
                "ok": classified.get("_gate_ok") is True,
                "kind": classified.get("kind"),
                "reason": classified.get("reason"),
                "verdict": next(
                    row["verdict"]
                    for row in rows
                    if row["name"] == "hosted_actions_classify"
                ),
            },
            "resolve_only": {
                "ok": resolve.get("_gate_ok") is True,
                "corpus_kind": resolve.get("corpus_kind"),
                "verdict": next(
                    row["verdict"] for row in rows if row["name"] == "resolve_only"
                ),
            },
            "pipeline_smoke": {
                "ok": smoke.get("_gate_ok") is True,
                "verdict": next(
                    row["verdict"] for row in rows if row["name"] == "pipeline_smoke"
                ),
                "source_contracts": ((smoke.get("gates") or {}).get("source_contracts") or {}).get(
                    "ok"
                ),
                "exact_17_sheet_inventory": (
                    (smoke.get("gates") or {}).get("decision_reports_offline") or {}
                ).get("exact_17_sheet_inventory"),
            },
            "synthetic_csone_metrics": {
                "ok": bool(metrics.get("ok")),
                "customer_count": metrics.get("customer_count"),
                "tac_count": metrics.get("tac_count"),
                "verdict": next(
                    row["verdict"]
                    for row in rows
                    if row["name"] == "synthetic_csone_metrics"
                ),
            },
            "jeff_only_stubs": {
                "ok": (payloads.get("jeff_only_stubs") or {}).get("all_passed") is True,
                "verdict": next(
                    row["verdict"] for row in rows if row["name"] == "jeff_only_stubs"
                ),
            },
            "fixture_kpi_metamorphic": {
                "ok": (payloads.get("fixture_kpi_metamorphic") or {}).get("all_passed")
                is True,
                "verdict": next(
                    row["verdict"]
                    for row in rows
                    if row["name"] == "fixture_kpi_metamorphic"
                ),
            },
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
        help="Print the full local-proof payload (Round 169.5)",
    )
    args = parser.parse_args(argv)
    payload = run_offline_sim_local(Path(args.output_dir))
    summary = _safe_summary_path(Path(args.output_dir) / "offline_sim_local_summary.json")
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            format_scorecard(
                "AdoptIQ offline-sim-local scorecard (v4)",
                payload["scorecard"]["gates"],
                honesty=payload,
            )
        )
        print(
            json.dumps(
                {
                    "all_passed": payload["all_passed"],
                    "ready_for_live_cisco": False,
                    "summary": str(summary),
                },
                sort_keys=True,
            )
        )
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
