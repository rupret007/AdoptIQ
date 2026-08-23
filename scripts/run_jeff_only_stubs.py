#!/usr/bin/env python3
"""Round 169: prove live-only gates fail closed without the work Mac.

These checks never open Keeper, CircuIT, Snowflake, OneDrive, or a real CSOne
folder.  They record ``needs_work_mac`` stubs and assert the fail-closed
behavior already implemented by Round 143/145/146.
"""
# Round 169

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

from local_acceptance_lab import (  # noqa: E402
    LocalAcceptanceSafetyError,
    assert_safe_activation,
)
from local_acceptance_runtime import UnexpectedLiveDependency, _LocalConnection  # noqa: E402


AS_OF = "2026-08-03T21:00:00Z"
SCHEMA_VERSION = "jeff-only-stubs/v1"


def _safe_summary_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        relative = resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] != ".adoptiq-acceptance":
        raise ValueError("in-repository summaries must stay under .adoptiq-acceptance")
    return resolved


def _check(name: str, passed: bool, detail: str, *, status: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "status": status,
        "detail": detail,
        "needs_work_mac": status == "needs_work_mac",
    }


def prove_fixture_activation_requires_explicit_flag() -> dict[str, Any]:
    raised = False
    try:
        assert_safe_activation(explicit=False, host="127.0.0.1")
    except LocalAcceptanceSafetyError:
        raised = True
    return _check(
        "fixture_activation_requires_flag",
        raised,
        "--enable-local-fixtures is required; silent fixture activation is forbidden",
        status="fail_closed_offline",
    )


def prove_unadapted_snowflake_cursor_is_rejected() -> dict[str, Any]:
    raised = False
    try:
        _LocalConnection().cursor()
    except UnexpectedLiveDependency:
        raised = True
    return _check(
        "unadapted_snowflake_cursor_rejected",
        raised,
        "local acceptance raises UnexpectedLiveDependency on a live Snowflake cursor",
        status="fail_closed_offline",
    )


def prove_live_decision_reports_fail_closed(output_dir: Path) -> dict[str, Any]:
    live_dir = output_dir / "live-decision-reports-must-fail"
    live_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_decision_report_acceptance.py"),
            "--mode",
            "live",
            "--manager",
            "Local Fixture Manager",
            "--days",
            "90",
            "--as-of",
            AS_OF,
            "--output-dir",
            str(live_dir),
            "--base-url",
            "http://127.0.0.1:1",
        ],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    summary_path = live_dir / "decision_report_acceptance_summary.json"
    payload: dict[str, Any] = {}
    if summary_path.is_file():
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    honest = (
        payload.get("live_validation_performed") is False
        and payload.get("mode_executed") == "live"
        and payload.get("all_passed") is False
    )
    passed = completed.returncode != 0 and honest
    return _check(
        "live_decision_reports_fail_closed",
        passed,
        "live mode without a local app + Snowflake preflight exits nonzero and stays honest",
        status="needs_work_mac" if passed else "failed",
    )


def prove_work_machine_profile_requires_dmg(output_dir: Path) -> dict[str, Any]:
    missing = output_dir / "missing-candidate.dmg"
    manifest = output_dir / "missing-candidate.json"
    work_dir = output_dir / "work-machine-must-fail"
    work_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_round146_acceptance.py"),
            "--output-dir",
            str(work_dir),
            "work-machine",
            "--candidate-dmg",
            str(missing),
            "--candidate-manifest",
            str(manifest),
            "--manager",
            "Local Fixture Manager",
            "--member-email",
            "morgan.lee@example.invalid",
            "--customer-name",
            "Acme Corporation",
            "--subscription-id",
            "SUB-001",
            "--as-of",
            AS_OF,
        ],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    combined = f"{completed.stdout}\n{completed.stderr}"
    passed = completed.returncode != 0 and "candidate-dmg" in combined
    return _check(
        "work_machine_profile_requires_dmg",
        passed,
        "Round 146 work-machine profile refuses to start without a real candidate DMG",
        status="needs_work_mac" if passed else "failed",
    )


def documented_work_mac_only_stubs() -> list[dict[str, Any]]:
    items = (
        (
            "live_snowflake_keeper",
            "Authorized Snowflake + Keeper on the work Mac / VPN",
        ),
        (
            "live_csone_folder",
            "External CSONE_CORPUS_DIR of real CSOne exports (never copy into Git)",
        ),
        (
            "live_circuit",
            "Live CircuIT narrative / Ask AI (cassettes stay the offline floor)",
        ),
        (
            "onedrive_publication",
            "OneDrive latest.json / DMG-EXE publication",
        ),
        (
            "build115_package_smoke_promote",
            "Package, smoke, visual review, and promote Build 115",
        ),
        (
            "build114_must_stay_invalidated",
            "Build 114 must never be installed, staged, or promoted",
        ),
    )
    return [
        _check(name, True, detail, status="needs_work_mac")
        for name, detail in items
    ]


def run_jeff_only_stubs(output_dir: Path) -> dict[str, Any]:
    output_dir = _safe_summary_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checks = [
        prove_fixture_activation_requires_explicit_flag(),
        prove_unadapted_snowflake_cursor_is_rejected(),
        prove_live_decision_reports_fail_closed(output_dir),
        prove_work_machine_profile_requires_dmg(output_dir),
        *documented_work_mac_only_stubs(),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "round": 169,
        "sanitized": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "release_ready": False,
        "all_passed": all(item["passed"] for item in checks),
        "needs_work_mac": [item["name"] for item in checks if item["needs_work_mac"]],
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record fail-closed Jeff-only stubs for Cloud/Bob offline sim."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".adoptiq-acceptance" / "offline-bob-sim" / "jeff-only-stubs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_jeff_only_stubs(Path(args.output_dir))
    summary_path = _safe_summary_path(Path(args.output_dir) / "jeff_only_stubs.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"all_passed": payload["all_passed"], "summary": str(summary_path)}, sort_keys=True))
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
