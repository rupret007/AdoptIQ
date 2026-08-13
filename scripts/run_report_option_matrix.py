#!/usr/bin/env python3
"""Round 133: live exhaustive report-option matrix runner.

Exercises every report type, manager, and technology at a fixed scope
(default 90 days) against a running AdoptIQ instance.  Writes a
``matrix_summary.json`` rollup to the downloads directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
_CROSS_REPORT_SOURCE_SHEETS = (
    "Subscriptions",
    "Action_Plans",
    "Adoption_Barriers",
    "Customer_Pulse",
    "TAC_Cases",
    "Success_Priorities",
)


def _ensure_repo_root_on_path() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def _probe_connectivity(base_url: str) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    url = f"{base_url.rstrip('/')}/api/diag/connectivity"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310  # nosec B310
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return {"ok": False, "error_kind": "connectivity_probe_failed", "error": str(exc)}
    if not isinstance(payload, dict):
        return {"ok": False, "error_kind": "connectivity_invalid_payload"}
    return payload


def _probe_running_reports(base_url: str) -> list[dict[str, Any]]:
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from run_report_soak import probe_running_reports  # noqa: WPS433

    return probe_running_reports(base_url)


def _run_r114_audit(docx_path: Path, xlsx_path: Path) -> dict[str, Any]:
    """Run read-only R114 audit on one artifact pair; return critical flag."""
    if not docx_path.exists() and not xlsx_path.exists():
        return {"critical": True, "reason": "artifacts_missing"}
    base = docx_path.with_suffix("") if docx_path.exists() else xlsx_path.with_suffix("")
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "r114_audit_reports.py"),
        "--target",
        f"run={base}",
    ]
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    critical = "CRITICAL_ISSUES_FOUND=True" in (completed.stdout or "")
    return {
        "critical": critical,
        "returncode": completed.returncode,
        "stdout_tail": (completed.stdout or "")[-2000:],
        "stderr_tail": (completed.stderr or "")[-1000:],
    }


def _cross_report_source_consistency(summary: dict[str, Any]) -> dict[str, Any]:
    """Compare canonical source identities across equivalent report scopes.

    Word/XLSX parity can be perfect while different report routes silently
    apply different date or ownership filters. This gate groups successful
    artifacts by their canonical manager/scope/technology/window and requires
    each report family to publish the same source state and stable Record_ID
    set. Only counts and SHA-256 digests enter the summary; source identifiers
    and customer data never do.
    """

    import pandas as pd  # noqa: PLC0415

    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    read_errors: list[dict[str, str]] = []
    for result in summary.get("results") or []:
        if not isinstance(result, dict) or not result.get("all_passed"):
            continue
        scenario = str(result.get("scenario") or "unspecified")
        xlsx_debug_path = next(
            (
                str(artifact.get("debug_path") or "")
                for artifact in result.get("artifacts") or []
                if isinstance(artifact, dict)
                and artifact.get("file_type") == "xlsx"
                and artifact.get("debug_path")
            ),
            None,
        )
        if xlsx_debug_path is None:
            continue
        xlsx_path = Path(xlsx_debug_path)
        if not xlsx_path.is_file():
            read_errors.append({"scenario": scenario, "kind": "xlsx_missing"})
            continue
        try:
            with pd.ExcelFile(xlsx_path) as excel:
                info_frame = pd.read_excel(excel, sheet_name="Report_Info", dtype=object)
                info = {
                    str(row.get("Item") or "").strip(): row.get("Value")
                    for _, row in info_frame.iterrows()
                    if str(row.get("Item") or "").strip()
                }
                scope_type = str(info.get("Scope_Type") or "").strip().casefold()
                scope_value = (
                    "<team>"
                    if scope_type == "team"
                    else str(info.get("Scope_Value") or "").strip().casefold()
                )
                group_key = (
                    str(info.get("Manager") or "").strip().casefold(),
                    str(info.get("Technology") or "All").strip().casefold(),
                    scope_type,
                    scope_value,
                    str(info.get("Days") or "").strip(),
                )
                signatures: dict[str, dict[str, Any]] = {}
                for sheet_name in _CROSS_REPORT_SOURCE_SHEETS:
                    frame = pd.read_excel(excel, sheet_name=sheet_name, dtype=object)
                    if "Legacy_Record_Type" in frame.columns:
                        frame = frame.loc[
                            frame["Legacy_Record_Type"]
                            .fillna("")
                            .astype(str)
                            .str.strip()
                            .str.casefold()
                            .ne("family-specific reported fact")
                        ].copy()
                    if "Record_ID" not in frame.columns:
                        raise ValueError(f"{sheet_name} lacks Record_ID")
                    identities = sorted(
                        {
                            str(value).strip()
                            for value in frame["Record_ID"].dropna()
                            if str(value).strip()
                        }
                    )
                    digest = hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()
                    attribution_by_record: dict[str, set[str]] = {
                        identity: set() for identity in identities
                    }
                    if "Attributed_Team_Members" in frame.columns:
                        for _, row in frame.iterrows():
                            record_value = row.get("Record_ID")
                            record_id = (
                                ""
                                if pd.isna(record_value)
                                else str(record_value).strip()
                            )
                            if record_id not in attribution_by_record:
                                continue
                            attribution_value = row.get("Attributed_Team_Members")
                            attribution_text = (
                                ""
                                if pd.isna(attribution_value)
                                else str(attribution_value)
                            )
                            labels = {
                                label.strip().casefold()
                                for label in re.split(
                                    r"\s*(?:;|\|)\s*",
                                    attribution_text,
                                )
                                if label.strip()
                            }
                            attribution_by_record[record_id].update(labels)
                    attribution_lines = [
                        f"{record_id}\t{';'.join(sorted(attribution_by_record[record_id]))}"
                        for record_id in identities
                    ]
                    signatures[sheet_name] = {
                        "count": len(identities),
                        "identity_sha256": digest,
                        "attribution_sha256": hashlib.sha256(
                            "\n".join(attribution_lines).encode("utf-8")
                        ).hexdigest(),
                        "attributed_record_count": sum(
                            bool(labels) for labels in attribution_by_record.values()
                        ),
                        "source_state": str(
                            info.get(f"Source_State:{sheet_name}") or ""
                        ).strip().casefold(),
                    }
        except (OSError, ValueError, KeyError) as exc:
            read_errors.append(
                {"scenario": scenario, "kind": type(exc).__name__}
            )
            continue
        groups.setdefault(group_key, []).append(
            {
                "scenario": scenario,
                "signatures": signatures,
                "freshness": {
                    "data_as_of_utc": str(
                        info.get("Data_As_Of_UTC") or ""
                    ).strip(),
                    "data_as_of_state": str(
                        info.get("Data_As_Of_State") or ""
                    ).strip().casefold(),
                    "evaluation_as_of_utc": str(
                        info.get("Evaluation_As_Of_UTC") or ""
                    ).strip(),
                },
            }
        )

    mismatches: list[dict[str, Any]] = []
    freshness_mismatches: list[dict[str, Any]] = []
    groups_evaluated = 0
    comparisons = 0
    for group_key, entries in sorted(groups.items()):
        if len(entries) < 2:
            continue
        groups_evaluated += 1
        group_digest = hashlib.sha256("\x1f".join(group_key).encode("utf-8")).hexdigest()[:12]
        freshness_observed = {
            entry["scenario"]: entry["freshness"] for entry in entries
        }
        if len(
            {
                (
                    value["data_as_of_utc"],
                    value["data_as_of_state"],
                    value["evaluation_as_of_utc"],
                )
                for value in freshness_observed.values()
            }
        ) > 1:
            freshness_mismatches.append(
                {
                    "group_digest": group_digest,
                    "observed": freshness_observed,
                }
            )
        for sheet_name in _CROSS_REPORT_SOURCE_SHEETS:
            comparisons += 1
            observed = {
                entry["scenario"]: entry["signatures"][sheet_name]
                for entry in entries
            }
            unique = {
                (
                    signature["count"],
                    signature["identity_sha256"],
                    signature["attribution_sha256"],
                    signature["attributed_record_count"],
                    signature["source_state"],
                )
                for signature in observed.values()
            }
            if len(unique) > 1:
                mismatches.append(
                    {
                        "group_digest": group_digest,
                        "source_sheet": sheet_name,
                        "observed": observed,
                    }
                )

    return {
        "ok": not read_errors and not mismatches and not freshness_mismatches,
        "groups_evaluated": groups_evaluated,
        "comparisons": comparisons,
        "mismatches": mismatches,
        "freshness_mismatches": freshness_mismatches,
        "read_errors": read_errors,
        "privacy": "counts_and_sha256_only",
    }


def build_matrix_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Round 133 exhaustive AdoptIQ report option matrix.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:5151", help="Live AdoptIQ base URL")
    parser.add_argument("--downloads-dir", default="~/Downloads", help="Directory for debug artifacts")
    parser.add_argument("--days", type=int, default=90, help="Analysis window (1-365)")
    parser.add_argument(
        "--blocks",
        default="all",
        help="Comma-separated matrix blocks: E,F,A,B,C,D,G or 'all' (cheap-first default order)",
    )
    parser.add_argument(
        "--resume-from",
        default="",
        help="Scenario key to resume from (inclusive)",
    )
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between /status polls")
    parser.add_argument("--timeout", type=int, default=1800, help="Max seconds per scenario")
    parser.add_argument("--request-timeout", type=int, default=120, help="HTTP request timeout")
    parser.add_argument("--download-timeout", type=int, default=300, help="Artifact download timeout")
    parser.add_argument("--run-id", default="", help="Optional run identifier")
    parser.add_argument("--stop-on-failure", action="store_true", help="Stop on first failing scenario")
    parser.add_argument("--strict", action="store_true", help="Enable strict quality gates")
    parser.add_argument(
        "--baseline-mode",
        choices=["off", "latest", "manifest"],
        default="off",
        help="Baseline comparison mode (default off for live matrix)",
    )
    parser.add_argument("--baseline-manifest", default="", help="Baseline manifest path when mode=manifest")
    parser.add_argument(
        "--customer-name",
        default="Wells Fargo",
        help="Block G renewal single-customer name",
    )
    parser.add_argument(
        "--subscription-id",
        default=os.environ.get("ADOPTIQ_MATRIX_SUBSCRIPTION_ID", ""),
        help="Block G subscription analysis ID (skip when empty)",
    )
    parser.add_argument(
        "--csone-upload-path",
        default=os.environ.get("ADOPTIQ_MATRIX_CSONE_FILE", ""),
        help="Block G explicit CSOne upload path (skip upload edges when empty)",
    )
    parser.add_argument(
        "--allow-running",
        action="store_true",
        help="Allow matrix start even when other analyses are running",
    )
    parser.add_argument(
        "--skip-r114",
        action="store_true",
        help="Skip per-run R114 artifact audit (faster; not recommended)",
    )
    parser.add_argument("--min-docx-similarity", type=float, default=0.35)
    parser.add_argument("--min-sheet-overlap", type=float, default=0.5)
    parser.add_argument("--min-header-similarity", type=float, default=0.3)
    parser.add_argument("--min-docx-chars", type=int, default=200)
    parser.add_argument("--min-docx-numeric-similarity", type=float, default=0.8)
    parser.add_argument("--min-docx-table-numeric-similarity", type=float, default=0.95)
    parser.add_argument("--max-xlsx-row-delta-ratio", type=float, default=0.2)
    parser.add_argument("--max-xlsx-row-delta-abs", type=int, default=25)
    parser.add_argument(
        "--local-acceptance",
        action="store_true",
        help=(
            "Require the explicitly started sanitized local-acceptance runtime "
            "and use its production-contract report matrix"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_repo_root_on_path()
    from report_iteration_loop import (
        EdgeMatrixConfig,
        build_exhaustive_option_matrix,
        build_local_acceptance_multi_manager_matrix,
        build_local_acceptance_option_matrix,
        build_runner_config,
        parse_matrix_blocks,
        run_option_matrix,
        select_matrix_scenario_keys,
    )

    parser = build_matrix_arg_parser()
    args = parser.parse_args(argv)

    parsed = urlparse(args.base_url)
    if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost"}:
        print("[matrix] base-url must target the local AdoptIQ app", file=sys.stderr)
        return 2

    if not args.allow_running:
        try:
            running = _probe_running_reports(args.base_url)
        except RuntimeError as exc:
            print(f"[matrix] preflight failed: {exc}", file=sys.stderr)
            return 2
        if running:
            print(f"[matrix] refusing to start: {len(running)} analysis(es) still running", file=sys.stderr)
            for item in running[:5]:
                print(f"  - {item}", file=sys.stderr)
            return 3

    connectivity = _probe_connectivity(args.base_url)
    if args.local_acceptance:
        if (
            not connectivity.get("ok")
            or connectivity.get("mode") != "local_acceptance_fixture"
            or connectivity.get("live_validation_performed") is not False
        ):
            print(
                "[matrix] local-acceptance mode requires the explicit sanitized "
                "loopback fixture runtime",
                file=sys.stderr,
            )
            print(json.dumps(connectivity, indent=2, sort_keys=True), file=sys.stderr)
            return 5
    elif not connectivity.get("ok"):
        print(
            "[matrix] Snowflake/connectivity preflight failed — live matrix requires VPN + credentials",
            file=sys.stderr,
        )
        print(json.dumps(connectivity, indent=2, sort_keys=True), file=sys.stderr)
        return 5

    edge = EdgeMatrixConfig(
        customer_name=args.customer_name,
        subscription_id=args.subscription_id,
        csone_upload_path=args.csone_upload_path,
    )
    if args.local_acceptance:
        local_customer = (
            "Acme Corporation" if args.customer_name == "Wells Fargo" else args.customer_name
        )
        if str(connectivity.get("scenario") or "") == "multi_manager":
            matrix = build_local_acceptance_multi_manager_matrix(
                days=max(min(int(args.days), 365), 1),
            )
        else:
            matrix = build_local_acceptance_option_matrix(
                days=max(min(int(args.days), 365), 1),
                customer_name=local_customer,
                subscription_id=args.subscription_id or "SUB-001",
            )
    else:
        matrix = build_exhaustive_option_matrix(
            days=max(min(int(args.days), 365), 1), edge=edge
        )
    blocks = parse_matrix_blocks(args.blocks)
    scenario_keys = select_matrix_scenario_keys(matrix, blocks, resume_from=args.resume_from)
    if not scenario_keys:
        print("[matrix] no scenarios selected", file=sys.stderr)
        return 4

    runner_args = argparse.Namespace(
        base_url=args.base_url,
        downloads_dir=args.downloads_dir,
        iterations=1,
        scenarios="all",
        poll_interval=args.poll_interval,
        timeout=args.timeout,
        request_timeout=args.request_timeout,
        download_timeout=args.download_timeout,
        run_id=args.run_id,
        stop_on_failure=args.stop_on_failure,
        baseline_mode=args.baseline_mode,
        baseline_manifest=args.baseline_manifest,
        init_baseline=False,
        init_baseline_dir="",
        init_baseline_label="",
        strict=args.strict,
        min_docx_similarity=args.min_docx_similarity,
        min_sheet_overlap=args.min_sheet_overlap,
        min_header_similarity=args.min_header_similarity,
        min_docx_chars=args.min_docx_chars,
        min_docx_numeric_similarity=args.min_docx_numeric_similarity,
        min_docx_table_numeric_similarity=args.min_docx_table_numeric_similarity,
        max_xlsx_row_delta_ratio=args.max_xlsx_row_delta_ratio,
        max_xlsx_row_delta_abs=args.max_xlsx_row_delta_abs,
    )
    config = build_runner_config(runner_args)
    config.scenario_keys = scenario_keys

    print(
        f"[matrix] starting {len(scenario_keys)} scenario(s) blocks={','.join(blocks)} days={args.days}"
    )
    summary = run_option_matrix(config, matrix, scenario_keys)
    if args.local_acceptance:
        summary["local_acceptance_scenario"] = str(
            connectivity.get("scenario") or ""
        )
        source_consistency = _cross_report_source_consistency(summary)
        summary["cross_report_source_consistency"] = source_consistency
        if not source_consistency.get("ok") and summary.get("all_passed"):
            summary["all_passed"] = False
            summary["aborted"] = True

    if not args.skip_r114:
        audit_results: dict[str, Any] = {}
        for result in summary.get("results", []):
            if not isinstance(result, dict) or not result.get("all_passed"):
                continue
            scenario_key = str(result.get("scenario") or "")
            docx_path = xlsx_path = None
            for artifact in result.get("artifacts") or []:
                if not isinstance(artifact, dict):
                    continue
                if artifact.get("file_type") == "docx":
                    docx_path = Path(str(artifact.get("debug_path") or ""))
                elif artifact.get("file_type") == "xlsx":
                    xlsx_path = Path(str(artifact.get("debug_path") or ""))
            if docx_path and xlsx_path:
                audit_results[scenario_key] = _run_r114_audit(docx_path, xlsx_path)
        summary["r114_audit"] = audit_results
        critical_hits = [k for k, v in audit_results.items() if v.get("critical")]
        summary["r114_critical_scenarios"] = critical_hits
        if critical_hits and summary.get("all_passed"):
            summary["all_passed"] = False
            summary["aborted"] = True

    summary_path = sorted(
        Path(config.downloads_dir).glob("AdoptIQ_ReportOptionMatrixSummary__*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if summary_path:
        summary_path[0].write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"[matrix] all_passed={summary.get('all_passed')} completed={summary.get('scenarios_completed')}")
    return 0 if summary.get("all_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
