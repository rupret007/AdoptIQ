"""Round 133 matrix runner preflight tests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import report_source_parity as source_parity
from scripts import r114_audit_reports, run_report_option_matrix as matrix_runner


def _bounded_process_result(
    returncode: int,
    stdout: str | bytes,
    stderr: str | bytes = b"",
    *,
    timed_out: bool = False,
    output_truncated: bool = False,
) -> matrix_runner._BoundedProcessResult:
    stdout_bytes = stdout.encode() if isinstance(stdout, str) else stdout
    stderr_bytes = stderr.encode() if isinstance(stderr, str) else stderr
    try:
        stdout_text = stdout_bytes.decode("utf-8", "strict")
    except UnicodeDecodeError:
        stdout_text = ""
        stdout_utf8_valid = False
    else:
        stdout_utf8_valid = True
    markers = re.findall(
        r"(?m)^CRITICAL_ISSUES_FOUND=(True|False)[ \t\r]*$",
        stdout_text,
    )
    return matrix_runner._BoundedProcessResult(
        returncode=returncode,
        stdout_marker=markers[0] if markers else None,
        stdout_marker_count=len(markers),
        stdout_utf8_valid=stdout_utf8_valid,
        stdout_bytes=len(stdout_bytes),
        stdout_sha256=(
            "" if output_truncated else hashlib.sha256(stdout_bytes).hexdigest()
        ),
        stderr_bytes=len(stderr_bytes),
        stderr_sha256=(
            "" if output_truncated else hashlib.sha256(stderr_bytes).hexdigest()
        ),
        timed_out=timed_out,
        output_truncated=output_truncated,
    )


def test_round133_matrix_runner_blocks_when_connectivity_preflight_fails():
    with (
        patch.object(matrix_runner, "_probe_running_reports", return_value=[]),
        patch.object(
            matrix_runner,
            "_probe_connectivity",
            return_value={"ok": False, "error_kind": "snowflake_credentials_missing"},
        ),
    ):
        code = matrix_runner.main(["--blocks", "A"])
    assert code == 5


@pytest.mark.parametrize(
    "connectivity",
    ({"ok": "false"}, {"ok": 1}, {"ok": ""}, {}),
)
def test_live_matrix_rejects_nonliteral_connectivity_success(
    connectivity: dict[str, object],
) -> None:
    with (
        patch.object(matrix_runner, "_probe_running_reports", return_value=[]),
        patch.object(
            matrix_runner,
            "_probe_connectivity",
            return_value=connectivity,
        ),
    ):
        code = matrix_runner.main(
            [
                "--blocks",
                "A",
                "--manager",
                "Authorized Matrix Manager",
                "--customer-name",
                "Authorized Matrix Customer",
            ]
        )

    assert code == 5


def test_round133_matrix_runner_connectivity_probe_parses_json():
    class _Resp:
        def read(self):
            return json.dumps({"ok": True, "stages": []}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=_Resp()):
        payload = matrix_runner._probe_connectivity("http://127.0.0.1:5151")
    assert payload.get("ok") is True


def test_live_matrix_cli_requires_explicit_manager_and_customer() -> None:
    with (
        patch.object(matrix_runner, "_probe_running_reports", return_value=[]),
        patch.object(
            matrix_runner,
            "_probe_connectivity",
            return_value={"ok": True, "mode": "snowflake"},
        ),
    ):
        code = matrix_runner.main(["--blocks", "A"])

    assert code == 2


def test_live_matrix_cli_has_no_named_scope_defaults(monkeypatch) -> None:
    monkeypatch.delenv("ADOPTIQ_MATRIX_MANAGER", raising=False)
    monkeypatch.delenv("ADOPTIQ_MATRIX_CUSTOMER", raising=False)

    args = matrix_runner.build_matrix_arg_parser().parse_args([])

    assert args.manager == ""
    assert args.customer_name == ""


@pytest.mark.parametrize(
    ("label", "family"),
    [
        ("Comprehensive", "comprehensive"),
        ("Renewal Portfolio", "renewal"),
        ("subscription-analysis", "subscription"),
    ],
)
def test_report_info_family_labels_are_canonicalized(
    label: str,
    family: str,
) -> None:
    assert matrix_runner._canonical_report_family(label) == family


def _matrix_source_workbook(
    path: Path,
    *,
    report_type: str,
    tac_ids: list[str],
    tac_attribution: str = "Alex Rivera",
    data_as_of_utc: str = "2026-08-03T21:00:00Z",
) -> None:
    info = {
        "Manager": "Fixture Manager",
        "Report_Type": report_type,
        "Technology": "All",
        "Scope_Type": "team",
        "Scope_Value": "Fixture Manager team",
        "Days": 90,
        "Data_As_Of_UTC": data_as_of_utc,
        "Data_As_Of_State": "available",
        "Retrieval_Attempted_At_UTC": "2026-08-03T21:00:00Z",
        "Evaluation_As_Of_UTC": "2026-08-03T21:00:00Z",
        "Data_Mode": "Guarded offline fixture",
        "Live_Source_Validation": "No",
        "Due_Soon_Days": 14,
        "Action_Plan_Age_Bands": "0-30; 31-60; 61-90; 90+; Unknown",
        "Activity_Total_State": "available",
        "TAC_Case_Type_Classified": 1,
        "TAC_Case_Type_Not_Derivable": 0,
        "TAC_Case_Type_Coverage_Pct": 100,
        "Partial_Data_Warning_Count": 0,
        "Fact_Contract_SHA256": "0" * 64,
    }
    source_state_sheets = {
        "Subscriptions",
        "Action_Plans",
        "Adoption_Barriers",
        "Customer_Pulse",
        "TAC_Cases",
        "BEMS",
        "Success_Priorities",
        "External_Incidents",
        "External_Bugs",
        "Defect_Correlations",
    }
    for sheet_name in matrix_runner._CROSS_REPORT_SOURCE_SHEETS[1:]:
        info[f"Sheet_SHA256:{sheet_name}"] = "0" * 64
    for sheet_name in source_state_sheets:
        info[f"Source_State:{sheet_name}"] = "available"
    frames: dict[str, pd.DataFrame] = {}
    for sheet_name in matrix_runner._CROSS_REPORT_SOURCE_SHEETS[1:]:
        ids = tac_ids if sheet_name == "TAC_Cases" else [f"{sheet_name}-1"]
        attribution = (
            [tac_attribution] * len(ids)
            if sheet_name == "TAC_Cases"
            else ["Alex Rivera"] * len(ids)
        )
        if sheet_name in {"Metric_Lineage", "Risk_Components", "Member_Summary", "Account_Summary"}:
            frame = pd.DataFrame(
                {
                    "Metric_Key": ids,
                    "Source_State": ["available"] * len(ids),
                }
            )
        elif sheet_name == "Chart_Data":
            frame = pd.DataFrame(
                {
                    "Chart_ID": ["activity_mix"] * len(ids),
                    "Metric_Key": ids,
                    "Series": ["Activities"] * len(ids),
                    "Category": ["Action Plans"] * len(ids),
                    "Value": [1] * len(ids),
                    "Source_State": ["available"] * len(ids),
                }
            )
        elif sheet_name == "Evidence_Links":
            frame = pd.DataFrame(
                {
                    "Evidence_Key": ids,
                    "Evidence_Role": ["supporting_record"] * len(ids),
                    "Source_Sheet": ["Action_Plans"] * len(ids),
                    "Source_Row_SHA256": ["1" * 64] * len(ids),
                    "Record_ID": ids,
                    "Source_State": ["available"] * len(ids),
                }
            )
        else:
            frame = pd.DataFrame(
                {
                    "Record_ID": ids,
                    "Attributed_Team_Members": attribution,
                }
            )
        frames[sheet_name] = frame
        info[f"Sheet_SHA256:{sheet_name}"] = source_parity.sheet_content_sha256(frame)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {"Item": key, "Value": value, "Detail": "fixture contract"}
                for key, value in info.items()
            ],
            columns=["Item", "Value", "Detail"],
        ).to_excel(writer, sheet_name="Report_Info", index=False)
        for sheet_name, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)


def _quartet_source_summary(
    tmp_path: Path,
    *,
    family_overrides: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Create one intentionally equivalent Compact/Comprehensive/Leader/Renewal set."""

    overrides = family_overrides or {}
    results = []
    labels = {
        "compact": "Compact",
        "comprehensive": "Comprehensive",
        "leader": "Leader",
        "renewal": "Renewal Portfolio",
    }
    for family, report_type in labels.items():
        path = tmp_path / f"{family}.xlsx"
        kwargs: dict[str, object] = {
            "report_type": report_type,
            "tac_ids": ["TAC-1"],
        }
        kwargs.update(overrides.get(family, {}))
        _matrix_source_workbook(path, **kwargs)  # type: ignore[arg-type]
        results.append(
            {
                "scenario": family,
                "all_passed": True,
                "artifacts": [{"file_type": "xlsx", "debug_path": str(path)}],
            }
        )
    return {"results": results}


def _parity_audit(
    summary: dict[str, object],
    *,
    max_freshness_skew_seconds: int = 0,
) -> dict[str, object]:
    declarations: dict[str, dict[str, str]] = {}
    for result in summary.get("results", []):
        if not isinstance(result, dict):
            continue
        scenario = str(result.get("scenario") or "")
        family = next(
            (
                candidate
                for candidate in ("compact", "comprehensive", "leader", "renewal")
                if candidate in scenario.casefold()
            ),
            "",
        )
        if family:
            declarations[scenario] = {"cohort": "fixture-quartet", "family": family}
    return matrix_runner._cross_report_source_consistency(
        summary,
        max_freshness_skew_seconds=max_freshness_skew_seconds,
        declared_cohorts=declarations,
    )


def test_cross_report_source_gate_detects_route_specific_tac_population(
    tmp_path: Path,
) -> None:
    summary = _quartet_source_summary(
        tmp_path,
        family_overrides={
            "compact": {"tac_ids": ["TAC-1", "TAC-2"]},
            "comprehensive": {"tac_ids": ["TAC-1", "TAC-3"]},
        },
    )

    audit = _parity_audit(summary)

    assert audit["ok"] is False
    assert audit["groups_evaluated"] == 1
    assert [item["source_sheet"] for item in audit["mismatches"]] == [
        "TAC_Cases"
    ]
    assert audit["privacy"] == "counts_and_sha256_only_no_source_values"


@pytest.mark.parametrize("invalid", ("false", 1, "", None))
def test_cross_report_source_gate_ignores_nonliteral_passed_rows(
    tmp_path: Path,
    invalid: object,
) -> None:
    summary = _quartet_source_summary(tmp_path)
    summary["results"].append(
        {
            "scenario": "tampered",
            "all_passed": invalid,
            "artifacts": [
                {
                    "file_type": "xlsx",
                    "debug_path": str(tmp_path / "must-not-be-read.xlsx"),
                }
            ],
        }
    )

    audit = _parity_audit(summary)

    assert audit["ok"] is True
    assert audit["groups_evaluated"] == 1
    assert audit["read_errors"] == []


def test_cross_report_source_gate_detects_attribution_drift_with_same_ids(
    tmp_path: Path,
) -> None:
    summary = _quartet_source_summary(
        tmp_path,
        family_overrides={
            "leader": {"tac_attribution": "Alex Rivera; Morgan Lee"},
        },
    )

    audit = _parity_audit(summary)

    assert audit["ok"] is False
    assert [item["source_sheet"] for item in audit["mismatches"]] == [
        "TAC_Cases"
    ]
    observed = audit["mismatches"][0]["observed"]
    assert {value["count"] for value in observed.values()} == {1}
    assert len({value["attribution_sha256"] for value in observed.values()}) == 2


def test_cross_report_source_gate_detects_freshness_drift(
    tmp_path: Path,
) -> None:
    summary = _quartet_source_summary(
        tmp_path,
        family_overrides={
            "leader": {"data_as_of_utc": "2026-08-03T20:00:00Z"},
        },
    )

    audit = _parity_audit(summary)

    assert audit["ok"] is False
    assert audit["mismatches"] == []
    assert len(audit["freshness_mismatches"]) == 1


def test_cross_report_source_gate_normalizes_equivalent_utc_formats(
    tmp_path: Path,
) -> None:
    summary = _quartet_source_summary(
        tmp_path,
        family_overrides={
            "compact": {"data_as_of_utc": "2026-08-03T21:00:00Z"},
            "comprehensive": {
                "data_as_of_utc": "2026-08-03T21:00:00+00:00"
            },
        },
    )

    audit = _parity_audit(summary)

    assert audit["ok"] is True
    assert audit["comparison_requirement_met"] is True
    assert audit["comparisons"] == len(matrix_runner._CROSS_REPORT_SOURCE_SHEETS)
    assert audit["required_report_families"] == [
        "compact",
        "comprehensive",
        "leader",
        "renewal",
    ]
    assert audit["projected_fields"] == [
        "count",
        "row_count",
        "missing_identity_count",
        "duplicate_identity_count",
        "identity_sha256",
        "semantic_sha256",
        "attribution_sha256",
        "attributed_record_count",
        "source_state",
        "source_state_sha256",
    ]
    assert audit["report_family_sets_compared"] == [
        ["compact", "comprehensive", "leader", "renewal"]
    ]


def test_cross_report_source_gate_allows_only_explicit_bounded_live_clock_skew(
    tmp_path: Path,
) -> None:
    summary = _quartet_source_summary(
        tmp_path,
        family_overrides={
            "compact": {"data_as_of_utc": "2026-08-03T20:55:00Z"},
            "comprehensive": {"data_as_of_utc": "2026-08-03T21:00:00Z"},
        },
    )

    exact = _parity_audit(summary)
    bounded = _parity_audit(
        summary,
        max_freshness_skew_seconds=300,
    )

    assert exact["ok"] is False
    assert bounded["ok"] is True
    assert bounded["max_freshness_skew_seconds"] == 300


def test_cross_report_source_gate_rejects_clean_pair_without_full_quartet(
    tmp_path: Path,
) -> None:
    paths = []
    for family, report_type in (
        ("compact", "Compact"),
        ("comprehensive", "Comprehensive"),
    ):
        path = tmp_path / f"{family}.xlsx"
        _matrix_source_workbook(
            path,
            report_type=report_type,
            tac_ids=["TAC-1"],
        )
        paths.append((family, path))
    summary = {
        "results": [
            {
                "scenario": family,
                "all_passed": True,
                "artifacts": [{"file_type": "xlsx", "debug_path": str(path)}],
            }
            for family, path in paths
        ]
    }

    audit = _parity_audit(summary)

    assert audit["ok"] is False
    assert audit["comparison_requirement_met"] is False
    assert audit["required_family_set_group_count"] == 0
    assert audit["incomplete_equivalent_scope_groups"] == [
        {
            "group_digest": audit["incomplete_equivalent_scope_groups"][0][
                "group_digest"
            ],
            "families_present": ["compact", "comprehensive"],
            "families_missing": ["leader", "renewal"],
        }
    ]


def test_cross_report_source_gate_excludes_intentional_subscription_scope(
    tmp_path: Path,
) -> None:
    summary = _quartet_source_summary(tmp_path)
    subscription = tmp_path / "subscription.xlsx"
    pd.DataFrame(
        [
            {"Item": "Report_Type", "Value": "Subscription Analysis"},
            {"Item": "Scope_Type", "Value": "subscription"},
        ]
    ).to_excel(subscription, sheet_name="Report_Info", index=False)
    results = summary["results"]
    assert isinstance(results, list)
    results.append(
        {
            "scenario": "g_subscription_analysis",
            "all_passed": True,
            "artifacts": [
                {"file_type": "xlsx", "debug_path": str(subscription)}
            ],
        }
    )

    audit = _parity_audit(summary)

    assert audit["ok"] is True
    assert audit["ignored_non_parity_scenario_count"] == 1
    assert audit["read_errors"] == []


def test_cross_report_source_gate_requires_a_real_cross_family_comparison(
    tmp_path: Path,
) -> None:
    only = tmp_path / "compact.xlsx"
    _matrix_source_workbook(only, report_type="Compact", tac_ids=["TAC-1"])
    summary = {
        "results": [
            {
                "scenario": "e_compact_am_All",
                "all_passed": True,
                "artifacts": [{"file_type": "xlsx", "debug_path": str(only)}],
            }
        ]
    }

    audit = _parity_audit(summary)

    assert audit["ok"] is False
    assert audit["comparison_requirement_met"] is False
    assert audit["groups_evaluated"] == 0
    assert audit["comparisons"] == 0


def test_cross_report_source_gate_fails_when_successful_xlsx_is_missing(
    tmp_path: Path,
) -> None:
    summary = _quartet_source_summary(tmp_path)
    compact = next(
        result
        for result in summary["results"]
        if result["scenario"] == "compact"
    )
    compact["artifacts"] = []

    audit = _parity_audit(summary)

    assert audit["ok"] is False
    assert [error["kind"] for error in audit["read_errors"]] == [
        "xlsx_artifact_missing"
    ]


def test_r114_audit_requires_both_artifacts(tmp_path: Path) -> None:
    docx = tmp_path / "pair.docx"
    docx.write_bytes(b"docx")

    audit = matrix_runner._run_r114_audit(docx, None)

    assert audit["ok"] is False
    assert audit["critical"] is True
    assert audit["reason"] == "artifact_pair_incomplete"
    assert audit["missing_artifact_types"] == ["xlsx"]


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected_reason"),
    [
        (1, "CRITICAL_ISSUES_FOUND=False\n", "audit_nonzero_exit"),
        (0, "audit completed without a marker\n", "audit_marker_missing"),
        (
            0,
            "CRITICAL_ISSUES_FOUND=False\nCRITICAL_ISSUES_FOUND=True\n",
            "audit_marker_malformed",
        ),
        (0, "CRITICAL_ISSUES_FOUND=Unknown\n", "audit_marker_missing"),
        (0, "CRITICAL_ISSUES_FOUND=True\n", "audit_critical_findings"),
    ],
)
def test_r114_audit_fails_closed_on_bad_process_or_marker(
    tmp_path: Path,
    returncode: int,
    stdout: str,
    expected_reason: str,
) -> None:
    docx = tmp_path / "pair.docx"
    xlsx = tmp_path / "pair.xlsx"
    docx.write_bytes(b"docx")
    xlsx.write_bytes(b"xlsx")
    completed = _bounded_process_result(returncode, stdout)

    with patch.object(matrix_runner, "_run_bounded_process", return_value=completed):
        audit = matrix_runner._run_r114_audit(docx, xlsx)

    assert audit["ok"] is False
    assert audit["critical"] is True
    assert audit["reason"] == expected_reason


def test_r114_audit_accepts_only_zero_with_single_false_marker(
    tmp_path: Path,
) -> None:
    docx = tmp_path / "pair.docx"
    xlsx = tmp_path / "pair.xlsx"
    docx.write_bytes(b"docx")
    xlsx.write_bytes(b"xlsx")
    completed = _bounded_process_result(
        0, "audit details\nCRITICAL_ISSUES_FOUND=False\n"
    )

    with patch.object(
        matrix_runner,
        "_run_bounded_process",
        return_value=completed,
    ) as run:
        audit = matrix_runner._run_r114_audit(docx, xlsx)

    assert audit["ok"] is True
    assert audit["critical"] is False
    assert audit["reason"] == "audit_passed"
    command = run.call_args.args[0]
    assert command[-6:] == [
        "--name",
        "run",
        "--docx",
        str(docx),
        "--xlsx",
        str(xlsx),
    ]


def test_r114_exact_pair_cli_never_discovers_a_stale_sibling(
    tmp_path: Path,
) -> None:
    docx = tmp_path / "AdoptIQ_Report_current.docx"
    exact_xlsx = tmp_path / "explicitly_supplied.xlsx"
    stale_xlsx = tmp_path / "AdoptIQ_Source_Data_current__ts-stale.xlsx"
    for path in (docx, exact_xlsx, stale_xlsx):
        path.write_bytes(b"placeholder")
    clean_docx = {
        "per_cell_citations": 0,
        "caption_paragraphs": 0,
        "tac_case_na": 0,
        "mid_string_citations": [],
        "markdown_chrome": [],
        "stub_bullets": [],
        "global_config_tokens": [],
        "html_leakage": [],
        "nanish_cells": 0,
        "nanish_context": [],
    }
    clean_xlsx = {
        "dup_ids": {},
        "dup_case_ids": {},
        "risk_saturation": {},
        "html_cells": 0,
        "customers_in_portfolio": None,
    }

    with (
        patch.object(r114_audit_reports, "audit_docx", return_value=clean_docx),
        patch.object(
            r114_audit_reports,
            "audit_xlsx",
            return_value=clean_xlsx,
        ) as audit_xlsx,
        patch.object(
            r114_audit_reports,
            "_resolve_xlsx_for_base",
            side_effect=AssertionError("exact pair must bypass sibling discovery"),
        ),
    ):
        code = r114_audit_reports.main(
            [
                "--name",
                "run",
                "--docx",
                str(docx),
                "--xlsx",
                str(exact_xlsx),
            ]
        )

    assert code == 0
    audit_xlsx.assert_called_once_with(exact_xlsx)


def test_r114_audit_rejects_non_utf8_output_as_malformed(tmp_path: Path) -> None:
    docx = tmp_path / "pair.docx"
    xlsx = tmp_path / "pair.xlsx"
    docx.write_bytes(b"docx")
    xlsx.write_bytes(b"xlsx")
    completed = _bounded_process_result(0, b"\xffCRITICAL_ISSUES_FOUND=False\n")

    with patch.object(matrix_runner, "_run_bounded_process", return_value=completed):
        audit = matrix_runner._run_r114_audit(docx, xlsx)

    assert audit["ok"] is False
    assert audit["critical"] is True
    assert audit["reason"] == "audit_output_malformed"


def test_matrix_inventory_exposes_exact_expected_and_completed_keys() -> None:
    summary = {
        "all_passed": True,
        "results": [
            {"scenario": "a_compact"},
            {"scenario": "g_subscription_analysis"},
        ],
    }

    matrix_runner._attach_scenario_inventory(
        summary, ["a_compact", "g_subscription_analysis"]
    )

    assert summary["scenario_inventory_exact"] is True
    assert summary["scenario_count_expected"] == 2
    assert summary["scenario_count_completed"] == 2
    assert summary["scenario_keys_expected"] == [
        "a_compact",
        "g_subscription_analysis",
    ]
    assert summary["scenario_keys_completed"] == summary["scenario_keys_expected"]


def test_matrix_inventory_fails_closed_on_partial_execution() -> None:
    summary = {"all_passed": True, "aborted": False, "results": [{"scenario": "a_compact"}]}

    matrix_runner._attach_scenario_inventory(
        summary, ["a_compact", "a_comprehensive"]
    )

    assert summary["scenario_inventory_exact"] is False
    assert summary["scenario_keys_missing"] == ["a_comprehensive"]
    assert summary["all_passed"] is False
    assert summary["aborted"] is True


def test_matrix_inventory_fails_closed_on_malformed_extra_result() -> None:
    summary = {
        "all_passed": True,
        "results": [None, {"scenario": "a_compact"}],
    }

    matrix_runner._attach_scenario_inventory(summary, ["a_compact"])

    assert summary["scenario_results_malformed_count"] == 1
    assert summary["scenario_inventory_exact"] is False
    assert summary["all_passed"] is False


def test_live_main_runs_consistency_and_one_audit_per_successful_pair(
    tmp_path: Path,
) -> None:
    scenario_keys = [
        "a_comprehensive",
        "a_compact",
        "a_renewal",
        "a_leader",
    ]
    summary = {
        "all_passed": True,
        "aborted": False,
        "results": [
            {
                "scenario": key,
                "all_passed": True,
                "artifacts": [
                    {"file_type": "docx", "debug_path": str(tmp_path / f"{key}.docx")},
                    {"file_type": "xlsx", "debug_path": str(tmp_path / f"{key}.xlsx")},
                ],
            }
            for key in scenario_keys
        ],
    }
    audit_results = [
        {
            "ok": False,
            "critical": True,
            "reason": "audit_marker_missing",
        },
        *[
            {"ok": True, "critical": False, "reason": "audit_passed"}
            for _ in range(3)
        ],
    ]
    with (
        patch.object(matrix_runner, "_probe_running_reports", return_value=[]),
        patch.object(
            matrix_runner,
            "_probe_connectivity",
            return_value={"ok": True, "mode": "snowflake"},
        ),
        patch("report_iteration_loop.run_option_matrix", return_value=summary),
        patch.object(
            matrix_runner,
            "_cross_report_source_consistency",
            return_value={
                "ok": True,
                "comparison_requirement_met": True,
                "groups_evaluated": 1,
                "comparisons": 6,
            },
        ) as consistency,
        patch.object(
            matrix_runner,
            "_run_r114_audit",
            side_effect=audit_results,
        ) as r114,
    ):
        code = matrix_runner.main(
            [
                "--blocks",
                "A",
                "--manager",
                "Authorized Matrix Manager",
                "--customer-name",
                "Authorized Matrix Customer",
                "--downloads-dir",
                str(tmp_path),
            ]
        )

    assert code == 1
    consistency.assert_called_once_with(
        summary,
        max_freshness_skew_seconds=4 * 60 * 60,
        declared_cohorts={
            "a_compact": {
                "cohort": "exhaustive-primary-team",
                "family": "compact",
            },
            "a_comprehensive": {
                "cohort": "exhaustive-primary-team",
                "family": "comprehensive",
            },
            "a_leader": {
                "cohort": "exhaustive-primary-team",
                "family": "leader",
            },
            "a_renewal": {
                "cohort": "exhaustive-primary-team",
                "family": "renewal",
            },
        },
        artifacts_root=tmp_path.resolve(),
    )
    assert r114.call_count == 4
    assert summary["r114_audit_scenario_count_expected"] == 4
    assert summary["r114_audit_scenario_count_completed"] == 4
    assert summary["r114_audit_inventory_exact"] is True
    assert summary["r114_audit_skipped"] is False
    assert summary["r114_critical_scenarios"] == ["a_comprehensive"]
    assert summary["all_passed"] is False
