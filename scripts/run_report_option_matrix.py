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
_CROSS_REPORT_REQUIRED_FAMILIES = (
    "compact",
    "comprehensive",
    "leader",
    "renewal",
)
_CROSS_REPORT_PROJECTED_FIELDS = (
    "count",
    "identity_sha256",
    "attribution_sha256",
    "attributed_record_count",
    "source_state",
)
_R114_RESULT_RE = re.compile(r"(?m)^CRITICAL_ISSUES_FOUND=(True|False)\s*$")
_KNOWN_REPORT_FAMILIES = {
    "compact": "compact",
    "comprehensive": "comprehensive",
    "leader": "leader",
    "renewal": "renewal",
    "renewal_portfolio": "renewal",
    "subscription": "subscription",
    "subscription_analysis": "subscription",
}


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


def _run_r114_audit(
    docx_path: Path | None,
    xlsx_path: Path | None,
) -> dict[str, Any]:
    """Run R114 on exactly one DOCX/XLSX pair and fail closed.

    A zero process status is not sufficient evidence: the audit must emit one
    recognized terminal marker and that marker must explicitly say ``False``.
    """

    missing_artifacts = [
        file_type
        for file_type, path in (("docx", docx_path), ("xlsx", xlsx_path))
        if path is None or not path.is_file()
    ]
    if missing_artifacts:
        return {
            "ok": False,
            "critical": True,
            "reason": "artifact_pair_incomplete",
            "missing_artifact_types": missing_artifacts,
            "returncode": None,
            "marker": None,
        }
    assert docx_path is not None
    assert xlsx_path is not None
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "r114_audit_reports.py"),
        "--name",
        "run",
        "--docx",
        str(docx_path),
        "--xlsx",
        str(xlsx_path),
    ]
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on audit execution
        return {
            "ok": False,
            "critical": True,
            "reason": "audit_process_failed",
            "exception_type": type(exc).__name__,
            "returncode": None,
            "marker": None,
        }

    if completed.stdout is not None and not isinstance(completed.stdout, str):
        return {
            "ok": False,
            "critical": True,
            "reason": "audit_output_malformed",
            "returncode": completed.returncode,
            "marker": None,
        }
    if completed.stderr is not None and not isinstance(completed.stderr, str):
        return {
            "ok": False,
            "critical": True,
            "reason": "audit_output_malformed",
            "returncode": completed.returncode,
            "marker": None,
        }
    stdout = completed.stdout or ""
    markers = _R114_RESULT_RE.findall(stdout)
    marker = markers[0] if len(markers) == 1 else None
    if len(markers) != 1:
        reason = "audit_marker_missing" if not markers else "audit_marker_malformed"
    elif completed.returncode != 0:
        reason = "audit_nonzero_exit"
    elif marker != "False":
        reason = "audit_critical_findings"
    else:
        reason = "audit_passed"
    ok = completed.returncode == 0 and marker == "False" and len(markers) == 1
    return {
        "ok": ok,
        "critical": not ok,
        "reason": reason,
        "returncode": completed.returncode,
        "marker": marker,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": (completed.stderr or "")[-1000:],
    }


def _canonical_report_family(raw: Any) -> str:
    token = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(raw or "").strip().casefold(),
    ).strip("_")
    return _KNOWN_REPORT_FAMILIES.get(token, "")


def _attach_scenario_inventory(
    summary: dict[str, Any],
    expected_keys: list[str],
) -> dict[str, Any]:
    """Attach an exact, machine-checkable expected/completed inventory."""

    raw_results = list(summary.get("results") or [])
    malformed_result_count = sum(
        not isinstance(result, dict) for result in raw_results
    )
    completed_keys = [
        str(result.get("scenario") or "")
        for result in raw_results
        if isinstance(result, dict)
    ]
    missing = [key for key in expected_keys if key not in completed_keys]
    unexpected = [key for key in completed_keys if key not in expected_keys]
    duplicates = sorted(
        {key for key in completed_keys if completed_keys.count(key) > 1}
    )
    exact = (
        malformed_result_count == 0
        and completed_keys == expected_keys
        and not duplicates
    )
    summary.update(
        {
            "scenario_keys_expected": list(expected_keys),
            "scenario_count_expected": len(expected_keys),
            "scenario_keys_completed": completed_keys,
            "scenario_count_completed": len(completed_keys),
            "scenario_keys_missing": missing,
            "scenario_keys_unexpected": unexpected,
            "scenario_keys_completed_duplicate": duplicates,
            "scenario_results_malformed_count": malformed_result_count,
            "scenario_inventory_exact": exact,
        }
    )
    if not exact:
        summary["all_passed"] = False
        summary["aborted"] = True
    return summary


def _cross_report_source_consistency(
    summary: dict[str, Any],
    *,
    max_freshness_skew_seconds: int = 0,
) -> dict[str, Any]:
    """Compare canonical source identities across equivalent report scopes.

    Word/XLSX parity can be perfect while different report routes silently
    apply different date or ownership filters. This gate groups successful
    artifacts by their canonical manager/scope/technology/window and requires
    the complete Compact/Comprehensive/Renewal/Leader family set to publish
    the same source state and stable Record_ID set. A pair or trio is not
    release evidence. Only counts and SHA-256 digests enter the summary;
    source identifiers and customer data never do.
    """

    import pandas as pd  # noqa: PLC0415

    def _info_text(value: Any, *, default: str = "") -> str:
        if value is None:
            return default
        try:
            if bool(pd.isna(value)):
                return default
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        return text if text else default

    def _utc_info_text(value: Any) -> str:
        text = _info_text(value)
        if not text:
            return ""
        return pd.to_datetime(text, utc=True, errors="raise").isoformat()

    if isinstance(max_freshness_skew_seconds, bool) or max_freshness_skew_seconds < 0:
        raise ValueError("max_freshness_skew_seconds must be a non-negative integer")
    required_family_set = set(_CROSS_REPORT_REQUIRED_FAMILIES)
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    read_errors: list[dict[str, str]] = []
    ignored_non_parity_scenario_count = 0
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
            read_errors.append(
                {"scenario": scenario, "kind": "xlsx_artifact_missing"}
            )
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
                report_family = _canonical_report_family(info.get("Report_Type"))
                if not report_family:
                    raise ValueError("Report_Info has no recognized Report_Type")
                if report_family not in required_family_set:
                    # Subscription reports have their own authorization and
                    # structural gates. They are not portfolio-equivalent to
                    # the four report families in this parity contract.
                    ignored_non_parity_scenario_count += 1
                    continue
                scope_type = _info_text(info.get("Scope_Type")).casefold()
                scope_value = (
                    "<team>"
                    if scope_type == "team"
                    else _info_text(info.get("Scope_Value")).casefold()
                )
                group_key = (
                    _info_text(info.get("Manager")).casefold(),
                    _info_text(info.get("Technology"), default="All").casefold(),
                    scope_type,
                    scope_value,
                    _info_text(info.get("Days")),
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
                        "source_state": _info_text(
                            info.get(f"Source_State:{sheet_name}")
                        ).casefold(),
                    }
        except Exception as exc:  # noqa: BLE001 - fail-closed workbook gate
            read_errors.append(
                {"scenario": scenario, "kind": type(exc).__name__}
            )
            continue
        groups.setdefault(group_key, []).append(
            {
                "scenario": scenario,
                "report_family": report_family,
                "signatures": signatures,
                "freshness": {
                    "data_as_of_utc": _utc_info_text(
                        info.get("Data_As_Of_UTC")
                    ),
                    "data_as_of_state": _info_text(
                        info.get("Data_As_Of_State")
                    ).casefold(),
                    "evaluation_as_of_utc": _utc_info_text(
                        info.get("Evaluation_As_Of_UTC")
                    ),
                },
            }
        )

    mismatches: list[dict[str, Any]] = []
    freshness_mismatches: list[dict[str, Any]] = []
    groups_evaluated = 0
    comparisons = 0
    report_family_sets: set[tuple[str, ...]] = set()
    incomplete_scope_groups: list[dict[str, Any]] = []
    for group_key, entries in sorted(groups.items()):
        report_families = tuple(
            sorted({str(entry["report_family"]) for entry in entries})
        )
        group_digest = hashlib.sha256(
            "\x1f".join(group_key).encode("utf-8")
        ).hexdigest()[:12]
        present_required_families = required_family_set.intersection(
            report_families
        )
        if not required_family_set.issubset(report_families):
            if len(present_required_families) >= 2:
                incomplete_scope_groups.append(
                    {
                        "group_digest": group_digest,
                        "families_present": sorted(present_required_families),
                        "families_missing": sorted(
                            required_family_set - present_required_families
                        ),
                    }
                )
            continue
        # Subscription analysis is intentionally a different scope product,
        # even when its Report_Info happens to share manager/window labels.
        # Compare only the four portfolio/decision families that are required
        # to be source-equivalent.
        entries = [
            entry
            for entry in entries
            if entry["report_family"] in required_family_set
        ]
        groups_evaluated += 1
        report_family_sets.add(
            tuple(sorted({str(entry["report_family"]) for entry in entries}))
        )
        freshness_observed = {
            entry["scenario"]: entry["freshness"] for entry in entries
        }
        freshness_values = list(freshness_observed.values())
        states = {value["data_as_of_state"] for value in freshness_values}
        clocks_within_bound = True
        for clock_key in ("data_as_of_utc", "evaluation_as_of_utc"):
            values = [value[clock_key] for value in freshness_values]
            if any(values) != all(values):
                clocks_within_bound = False
                continue
            if not values or not values[0]:
                continue
            timestamps = [pd.Timestamp(value) for value in values]
            skew = (max(timestamps) - min(timestamps)).total_seconds()
            if skew > max_freshness_skew_seconds:
                clocks_within_bound = False
        if len(states) > 1 or not clocks_within_bound:
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
                tuple(signature[field] for field in _CROSS_REPORT_PROJECTED_FIELDS)
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

    expected_comparisons = groups_evaluated * len(_CROSS_REPORT_SOURCE_SHEETS)
    comparison_requirement_met = (
        groups_evaluated > 0
        and comparisons == expected_comparisons
        and all(
            set(families) == required_family_set
            for families in report_family_sets
        )
    )
    return {
        "ok": (
            comparison_requirement_met
            and not read_errors
            and not mismatches
            and not freshness_mismatches
        ),
        "comparison_requirement_met": comparison_requirement_met,
        "groups_discovered": len(groups),
        "groups_evaluated": groups_evaluated,
        "comparisons": comparisons,
        "comparisons_expected": expected_comparisons,
        "required_report_families": list(_CROSS_REPORT_REQUIRED_FAMILIES),
        "projected_fields": list(_CROSS_REPORT_PROJECTED_FIELDS),
        "required_family_set_group_count": groups_evaluated,
        "report_family_sets_compared": [
            list(item) for item in sorted(report_family_sets)
        ],
        "incomplete_equivalent_scope_groups": incomplete_scope_groups,
        "ignored_non_parity_scenario_count": ignored_non_parity_scenario_count,
        "mismatches": mismatches,
        "freshness_mismatches": freshness_mismatches,
        "read_errors": read_errors,
        "max_freshness_skew_seconds": max_freshness_skew_seconds,
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
        "--manager",
        default=os.environ.get("ADOPTIQ_MATRIX_MANAGER", ""),
        help=(
            "Authorized manager scope for canonical and Block G live runs; "
            "required outside --local-acceptance"
        ),
    )
    parser.add_argument(
        "--customer-name",
        default=os.environ.get("ADOPTIQ_MATRIX_CUSTOMER", ""),
        help=(
            "Authorized customer scope for Block G live runs; required outside "
            "--local-acceptance"
        ),
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

    if args.local_acceptance:
        local_customer = args.customer_name.strip() or "Acme Corporation"
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
        selected_manager = args.manager.strip()
        selected_customer = args.customer_name.strip()
        if not selected_manager or not selected_customer:
            print(
                "[matrix] live matrix requires explicit --manager and "
                "--customer-name scopes",
                file=sys.stderr,
            )
            return 2
        edge = EdgeMatrixConfig(
            manager_name=selected_manager,
            customer_name=selected_customer,
            compact_customer_name=selected_customer,
            subscription_id=args.subscription_id,
            csone_upload_path=args.csone_upload_path,
        )
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
    _attach_scenario_inventory(summary, scenario_keys)
    if args.local_acceptance:
        summary["local_acceptance_scenario"] = str(
            connectivity.get("scenario") or ""
        )

    source_consistency = _cross_report_source_consistency(
        summary,
        max_freshness_skew_seconds=(0 if args.local_acceptance else 4 * 60 * 60),
    )
    summary["cross_report_source_consistency"] = source_consistency
    if not source_consistency.get("ok"):
        summary["all_passed"] = False
        summary["aborted"] = True

    if not args.skip_r114:
        audit_results: dict[str, Any] = {}
        successful_results = [
            result
            for result in summary.get("results", [])
            if isinstance(result, dict) and result.get("all_passed")
        ]
        audit_expected_keys = [
            str(result.get("scenario") or "") for result in successful_results
        ]
        for result in successful_results:
            scenario_key = str(result.get("scenario") or "")
            docx_paths: list[Path] = []
            xlsx_paths: list[Path] = []
            for artifact in result.get("artifacts") or []:
                if not isinstance(artifact, dict):
                    continue
                if artifact.get("file_type") == "docx":
                    docx_paths.append(
                        Path(str(artifact.get("debug_path") or ""))
                    )
                elif artifact.get("file_type") == "xlsx":
                    xlsx_paths.append(
                        Path(str(artifact.get("debug_path") or ""))
                    )
            if scenario_key in audit_results or not scenario_key:
                duplicate_key = scenario_key or "<missing-scenario-key>"
                audit_results[duplicate_key] = {
                    "ok": False,
                    "critical": True,
                    "reason": "audit_scenario_key_missing_or_duplicate",
                    "returncode": None,
                    "marker": None,
                }
                continue
            if len(docx_paths) != 1 or len(xlsx_paths) != 1:
                audit_results[scenario_key] = {
                    "ok": False,
                    "critical": True,
                    "reason": "artifact_pair_cardinality_invalid",
                    "docx_artifact_count": len(docx_paths),
                    "xlsx_artifact_count": len(xlsx_paths),
                    "returncode": None,
                    "marker": None,
                }
                continue
            audit_results[scenario_key] = _run_r114_audit(
                docx_paths[0], xlsx_paths[0]
            )
        summary["r114_audit"] = audit_results
        audit_completed_keys = list(audit_results)
        audit_inventory_exact = (
            audit_completed_keys == audit_expected_keys
            and len(audit_results) == len(successful_results)
        )
        summary["r114_audit_scenario_keys_expected"] = audit_expected_keys
        summary["r114_audit_scenario_count_expected"] = len(audit_expected_keys)
        summary["r114_audit_scenario_keys_completed"] = audit_completed_keys
        summary["r114_audit_scenario_count_completed"] = len(audit_completed_keys)
        summary["r114_audit_inventory_exact"] = audit_inventory_exact
        critical_hits = [k for k, v in audit_results.items() if not v.get("ok")]
        summary["r114_critical_scenarios"] = critical_hits
        if critical_hits or not audit_inventory_exact:
            summary["all_passed"] = False
            summary["aborted"] = True
    else:
        summary["r114_audit_skipped"] = True

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
