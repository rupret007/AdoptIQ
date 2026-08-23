#!/usr/bin/env python3
"""Round 169: fixture ingest → canonical truth → reports/workbook → manager UX.

Reuses Round 145 source contracts, Round 143 offline decision-report
acceptance, and the Flask test client.  Never opens Snowflake, Keeper,
CircuIT, or a real CSOne folder.
"""
# Round 169

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import openpyxl


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from decision_report_delivery import SOURCE_DATA_SHEET_NAMES  # noqa: E402


AS_OF = "2026-08-03T21:00:00Z"
MANAGER = "Local Fixture Manager"
SCHEMA_VERSION = "offline-pipeline-smoke/v1"
UX_PATHS = (
    "/",
    "/help",
    "/preferences",
    "/ask-ai",
    "/history",
    "/previous-reports",
    "/leader_report_form",
    "/ping",
    "/api/version",
    "/api/status/all",
    "/api/update/status",
    "/api/settings/report-defaults",
    "/api/corpus/status",  # Round 169.2: offline corpus/intel status widen
    "/api/intel/status",
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


def _run(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "command": command[1:],
        "exit_code": completed.returncode,
        "stdout_tail": (completed.stdout or "")[-800:],
        "stderr_tail": (completed.stderr or "")[-800:],
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _workbook_inventory(xlsx_path: Path) -> list[str]:
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        return list(workbook.sheetnames)
    finally:
        workbook.close()


def probe_manager_ux() -> dict[str, Any]:
    """Hit manager-facing pages that do not need live Cisco."""
    os.environ.setdefault("ADOPTIQ_TESTING", "1")
    settings_home = Path(tempfile.mkdtemp(prefix="adoptiq-r169-settings-"))
    import adoptiq_settings as settings

    settings._app_support_dir = lambda: settings_home  # type: ignore[method-assign]
    from app_simple import app as flask_app

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    results: list[dict[str, Any]] = []
    with flask_app.test_client() as client:
        for path in UX_PATHS:
            response = client.get(path)
            ok = response.status_code in {200, 302}
            results.append({"path": path, "status_code": response.status_code, "ok": ok})
    all_ok = all(item["ok"] for item in results)
    return {
        "ok": all_ok,
        "live_validation_performed": False,
        "paths": results,
    }


def run_offline_pipeline_smoke(output_dir: Path) -> dict[str, Any]:
    output_dir = _safe_summary_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    contracts_summary = output_dir / "source_contracts.json"
    decision_dir = output_dir / "decision-reports"

    contracts = _run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_local_source_contracts.py"),
            "--enable-local-fixtures",
            "--scenario",
            "healthy",
            "--summary",
            str(contracts_summary),
        ]
    )
    contracts_payload = _load_json(contracts_summary)
    contracts_ok = contracts["exit_code"] == 0 and contracts_payload.get("all_passed") is True
    contracts_honest = contracts_payload.get("live_validation_performed") is False

    decisions = _run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_decision_report_acceptance.py"),
            "--mode",
            "offline",
            "--manager",
            MANAGER,
            "--days",
            "90",
            "--as-of",
            AS_OF,
            "--output-dir",
            str(decision_dir),
        ]
    )
    decision_payload = _load_json(decision_dir / "decision_report_acceptance_summary.json")
    decisions_ok = decisions["exit_code"] == 0 and decision_payload.get("all_passed") is True
    decisions_honest = (
        decision_payload.get("live_validation_performed") is False
        and decision_payload.get("mode_executed") == "offline"
    )

    workbooks = sorted(decision_dir.rglob("*.xlsx"))
    expected_sheets = list(SOURCE_DATA_SHEET_NAMES)
    inventory_ok = False
    inspected = ""
    actual_sheets: list[str] = []
    if workbooks:
        inspected = workbooks[0].name
        actual_sheets = _workbook_inventory(workbooks[0])
        inventory_ok = actual_sheets == expected_sheets

    ux = probe_manager_ux()
    serialized = json.dumps(
        {
            "contracts": contracts_payload,
            "decisions": {
                key: decision_payload.get(key)
                for key in (
                    "all_passed",
                    "live_validation_performed",
                    "mode_executed",
                    "mode_requested",
                )
            },
        },
        sort_keys=True,
    )
    secret_free = "cisco.com" not in serialized.casefold() and "keeper" not in serialized.casefold()

    all_passed = bool(
        contracts_ok
        and contracts_honest
        and decisions_ok
        and decisions_honest
        and inventory_ok
        and ux.get("ok")
        and secret_free
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "round": 169,
        "sanitized": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "release_ready": False,
        "all_passed": all_passed,
        "gates": {
            "source_contracts": {
                "ok": contracts_ok and contracts_honest,
                "exit_code": contracts["exit_code"],
            },
            "decision_reports_offline": {
                "ok": decisions_ok and decisions_honest,
                "exit_code": decisions["exit_code"],
                "mode_executed": decision_payload.get("mode_executed"),
                "workbook_count": len(workbooks),
                "inspected_workbook": inspected,
                "exact_17_sheet_inventory": inventory_ok,
                "actual_sheets": actual_sheets,
            },
            "manager_ux": ux,
            "secret_free_summaries": secret_free,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fixture pipeline smoke: ingest, canonical reports, manager UX."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".adoptiq-acceptance" / "offline-bob-sim" / "pipeline-smoke",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_offline_pipeline_smoke(Path(args.output_dir))
    summary_path = _safe_summary_path(Path(args.output_dir) / "pipeline_smoke_summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"all_passed": payload["all_passed"], "summary": str(summary_path)}, sort_keys=True))
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
