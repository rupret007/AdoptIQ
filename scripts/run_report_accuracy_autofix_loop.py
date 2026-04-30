#!/usr/bin/env python3
"""Round 53: guarded overnight report accuracy and quality supervisor."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_RE = re.compile(r"^\[summary\]\s+(?P<path>.+\.json)\s*$", re.MULTILINE)


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_") or "run"


def _run_command(command: list[str], *, cwd: Path, timeout: int, input_text: str | None = None) -> dict[str, Any]:
    started = datetime.now(UTC)
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "started_at_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "completed_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"Timed out after {timeout}s",
            "started_at_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "completed_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def _runner_command(args: argparse.Namespace, scenario: str, run_id: str) -> list[str]:
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_report_iteration_loop.py"),
        "--base-url",
        args.base_url,
        "--downloads-dir",
        str(args.downloads_dir),
        "--iterations",
        "1",
        "--scenarios",
        scenario,
        "--run-id",
        run_id,
        "--baseline-mode",
        "manifest",
        "--baseline-manifest",
        str(args.baseline_manifest),
        "--strict",
        "--stop-on-failure",
        "--timeout",
        str(args.timeout),
        "--poll-interval",
        str(args.poll_interval),
    ]
    return command


def _parse_summary_path(output: str) -> Path | None:
    match = SUMMARY_RE.search(output or "")
    if not match:
        return None
    return Path(match.group("path").strip()).expanduser()


def _load_summary(path: Path) -> dict[str, Any]:
    """Round 53.3: bare ``json.loads`` would crash the supervisor on a
    truncated/corrupt summary file. Always return a dict; callers detect
    the empty/error shape and fall through to ``_runner_failure_result``
    so the failed scenario stays repairable."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"_load_error": f"unreadable_summary: {exc}"}
    try:
        loaded = json.loads(text)
    except (ValueError, TypeError) as exc:
        return {"_load_error": f"invalid_summary_json: {exc}"}
    if not isinstance(loaded, dict):
        return {"_load_error": "summary_not_object"}
    return loaded


def _runner_failure_result(
    scenario: str,
    runner: dict[str, Any],
    summary_path: Path | None,
    *,
    reason: str = "runner_failed_or_missing_summary",
) -> dict[str, Any]:
    """Round 53.2/53.3: fail closed when the live harness exits without a
    parseable summary, regardless of the child's return code."""

    return {
        "scenario": scenario,
        "analysis_id": None,
        "all_passed": False,
        "failure_phase": "runner_summary",
        "exception_type": "RunnerFailed",
        "final_status": {
            "status": "error",
            "error": (
                "Report iteration runner failed or did not emit a readable summary "
                f"(returncode={runner.get('returncode')}, summary_path={summary_path}, "
                f"reason={reason})."
            ),
        },
        "operational": {
            "passed": False,
            "details": {
                "reason": reason,
                "returncode": runner.get("returncode"),
                "summary_path": str(summary_path) if summary_path else None,
                "stderr_tail": str(runner.get("stderr") or "")[-2000:],
            },
        },
        "artifacts": [],
        "parity": {"passed": False, "details": {"reason": "runner_failed"}},
        "quality": {"passed": False, "details": {"reason": "runner_failed"}},
    }


def _results_needing_repair(summary: dict[str, Any], *, repair_on_recommendations: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for result in summary.get("results") or []:
        if not isinstance(result, dict):
            continue
        if not result.get("all_passed"):
            out.append(result)
            continue
        quality = result.get("quality") or {}
        details = quality.get("details") if isinstance(quality, dict) else {}
        if repair_on_recommendations and int((details or {}).get("recommendation_count") or 0) > 0:
            out.append(result)
    return out


def _write_repair_bundle(
    *,
    summary: dict[str, Any],
    result: dict[str, Any],
    args: argparse.Namespace,
    attempt: int,
) -> Path:
    scenario = str(result.get("scenario") or "unknown")
    bundle_dir = args.bundle_dir / _slug(args.run_id) / scenario
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_dir / f"repair_bundle_attempt{attempt}_{_utc_stamp()}.json"
    payload = {
        "created_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attempt": attempt,
        "scenario": scenario,
        "run_id": args.run_id,
        "quality_goal": (
            "Report must be accurate against raw/detail data, source-cited next to metric claims, "
            "well formatted, complete, and improved only when the change is evidence-backed."
        ),
        "summary_path": summary.get("summary_path"),
        "result": result,
        "repo_root": str(REPO_ROOT),
        "baseline_manifest": str(args.baseline_manifest),
        "guardrails": [
            "Do not commit changes.",
            "Do not delete, weaken, skip, or xfail tests to make the run green.",
            "Do not replace real logic with fake success paths.",
            "Prefer the smallest report or harness change that fixes the root cause.",
            "If adding a chart or section, back it with existing raw data and add/update a regression test.",
        ],
    }
    bundle_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return bundle_path


def _agent_command(args: argparse.Namespace) -> list[str]:
    """Round 53.3: keep agent defaults conservative.

    Pre-Round-53.3 the defaults included ``--trust --force`` (cursor-agent)
    and ``--permission-mode auto`` (claude), which let the overnight loop
    silently authorize broad filesystem edits / dependency installs. The
    repair bundle's textual guardrails do not actually constrain those
    flags, so the defaults now omit them; operators who explicitly want
    that behavior can pass ``--repair-command`` (or ``ADOPTIQ_REPAIR_COMMAND``)
    with the precise argv they accept responsibility for."""

    if args.repair_agent == "none":
        return []
    if args.repair_command:
        return shlex.split(args.repair_command)
    if args.repair_agent == "claude":
        return ["claude", "--print"]
    return ["cursor-agent", "--print", "--workspace", str(REPO_ROOT)]


def _repair_prompt(bundle_path: Path) -> str:
    return f"""You are repairing AdoptIQ report generation for an overnight quality loop.

Read this repair bundle first: {bundle_path}

Goals:
- Fix correctness, source citation, formatting, content, or chart/table usefulness issues described in the bundle.
- If the bundle identifies a useful chart or section improvement, implement it only when it is backed by source data.
- Add or update focused regression tests for any behavior change.
- Do not commit, do not push, do not weaken tests, and do not hide real failures.
- Keep changes small and aligned with existing report patterns.

When done, stop after making code/test changes. The supervisor will run verification and rerun the failed report.
"""


def _run_repair_agent(args: argparse.Namespace, bundle_path: Path) -> dict[str, Any]:
    command = _agent_command(args)
    if not command:
        return {"command": [], "returncode": 0, "stdout": "repair_agent=none", "stderr": ""}
    return _run_command(command + [_repair_prompt(bundle_path)], cwd=REPO_ROOT, timeout=args.repair_timeout)


def _run_targeted_tests(args: argparse.Namespace) -> dict[str, Any]:
    command = shlex.split(args.test_command) if args.test_command else [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_round51_report_iteration_loop.py",
        "tests/test_round52_kpi_coverage.py",
        "tests/test_round53_report_accuracy.py",
        "tests/test_round53_autofix_supervisor.py",
        "-q",
    ]
    return _run_command(command, cwd=REPO_ROOT, timeout=args.test_timeout)


def run_supervisor(args: argparse.Namespace) -> dict[str, Any]:
    args.downloads_dir.mkdir(parents=True, exist_ok=True)
    args.bundle_dir.mkdir(parents=True, exist_ok=True)
    scenarios = args.scenarios
    if scenarios == ["all"]:
        scenarios = ["comprehensive", "compact", "renewal", "leader"]
    events: list[dict[str, Any]] = []
    aborted = False

    for iteration in range(1, args.iterations + 1):
        for scenario in scenarios:
            run_id = f"{args.run_id}-iter{iteration}-{scenario}"
            runner = _run_command(
                _runner_command(args, scenario, run_id),
                cwd=REPO_ROOT,
                timeout=args.runner_timeout,
            )
            summary_path = _parse_summary_path(runner.get("stdout", ""))
            summary: dict[str, Any] = {}
            load_error: str | None = None
            # Round 53.3: a rc=0 child that omits ``[summary]`` (or
            # writes an unreadable file) was previously swallowed as an
            # empty result set, producing a silent supervisor pass.
            # Always synthesize a failure result unless we successfully
            # loaded a dict-shaped summary with at least one result.
            if summary_path and summary_path.exists():
                loaded = _load_summary(summary_path)
                load_error = loaded.get("_load_error") if isinstance(loaded, dict) else "summary_not_object"
                if load_error:
                    summary = {
                        "summary_path": str(summary_path),
                        "results": [
                            _runner_failure_result(
                                scenario, runner, summary_path, reason=load_error,
                            )
                        ],
                    }
                else:
                    summary = loaded
                    summary["summary_path"] = str(summary_path)
                    if not summary.get("results"):
                        summary["results"] = [
                            _runner_failure_result(
                                scenario, runner, summary_path,
                                reason="summary_missing_results",
                            )
                        ]
            else:
                summary = {
                    "summary_path": str(summary_path) if summary_path else None,
                    "results": [
                        _runner_failure_result(
                            scenario,
                            runner,
                            summary_path,
                            reason=(
                                "runner_summary_path_missing"
                                if summary_path
                                else "runner_summary_unreported"
                            ),
                        )
                    ],
                }
            event: dict[str, Any] = {
                "iteration": iteration,
                "scenario": scenario,
                "runner": {k: v for k, v in runner.items() if k not in {"stdout", "stderr"}},
                "summary_path": str(summary_path) if summary_path else None,
                "summary_load_error": load_error,
                "repairs": [],
                # Round 53.3: per-event green/red so the supervisor exit
                # code reflects "any scenario ended unrepaired" rather
                # than only honoring ``--stop-on-unrepaired``.
                "ended_green": False,
            }
            needs_repair = _results_needing_repair(
                summary,
                repair_on_recommendations=args.repair_on_recommendations,
            )
            event["needed_repair"] = bool(needs_repair)
            if not needs_repair:
                event["ended_green"] = True
            if needs_repair and args.max_repair_attempts == 0:
                aborted = bool(args.stop_on_unrepaired)
            for attempt, failed_result in enumerate(needs_repair[: args.max_repair_attempts], start=1):
                bundle_path = _write_repair_bundle(
                    summary=summary,
                    result=failed_result,
                    args=args,
                    attempt=attempt,
                )
                repair = _run_repair_agent(args, bundle_path)
                tests = _run_targeted_tests(args)
                rerun_id = f"{run_id}-repair{attempt}"
                rerun = _run_command(
                    _runner_command(args, scenario, rerun_id),
                    cwd=REPO_ROOT,
                    timeout=args.runner_timeout,
                )
                event["repairs"].append(
                    {
                        "bundle_path": str(bundle_path),
                        "repair_returncode": repair.get("returncode"),
                        "test_returncode": tests.get("returncode"),
                        "rerun_returncode": rerun.get("returncode"),
                        "rerun_summary_path": str(_parse_summary_path(rerun.get("stdout", "")) or ""),
                    }
                )
                rerun_green = (
                    tests.get("returncode") == 0 and rerun.get("returncode") == 0
                )
                if rerun_green:
                    event["ended_green"] = True
                else:
                    aborted = bool(args.stop_on_unrepaired)
                    if aborted:
                        break
            events.append(event)
            if aborted:
                break
        if aborted:
            break

    unrepaired_events = [evt for evt in events if not evt.get("ended_green")]
    payload = {
        "run_id": args.run_id,
        "started_at_utc": args.started_at_utc,
        "completed_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "iterations_requested": args.iterations,
        "scenarios": scenarios,
        "aborted": aborted,
        "unrepaired_events": len(unrepaired_events),
        "events": events,
    }
    out_path = args.downloads_dir / f"AdoptIQ_ReportQualitySupervisor__{_slug(args.run_id)}__{_utc_stamp()}.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["summary_path"] = str(out_path)
    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Round 53 guarded report quality/autofix loop.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5151")
    parser.add_argument("--downloads-dir", type=Path, default=Path("~/Downloads").expanduser())
    parser.add_argument("--bundle-dir", type=Path, default=Path("~/Downloads/adoptiq_repair_bundles").expanduser())
    # Round 57: default to the Build31 + R57 citations baseline (round57)
    # because R57's post-render citation injector adds ~698 / 480 / 558 /
    # 2207 ``[Source: AdoptIQ Report Data Sources]`` citations to the
    # comprehensive / compact / renewal / leader DOCX outputs respectively
    # -- the Round 56 baseline was captured BEFORE the injector was wired
    # so its DOCX text similarity drops below the gate's threshold even
    # though the underlying KPIs are unchanged. Pass ``--baseline-manifest
    # <path>`` explicitly to compare against any other captured baseline
    # (e.g. baselines/round56 for pre-citation comparison, or
    # baselines/round52 for historical diffs).
    parser.add_argument("--baseline-manifest", type=Path, default=REPO_ROOT / "baselines/round57/baseline_manifest.json")
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--scenarios", default="all")
    parser.add_argument("--run-id", default=f"round53-{_utc_stamp()}")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--runner-timeout", type=int, default=2400)
    parser.add_argument("--repair-timeout", type=int, default=1800)
    parser.add_argument("--test-timeout", type=int, default=600)
    parser.add_argument("--repair-agent", choices=["cursor-agent", "claude", "none"], default="cursor-agent")
    parser.add_argument("--repair-command", default=os.environ.get("ADOPTIQ_REPAIR_COMMAND", ""))
    parser.add_argument("--test-command", default=os.environ.get("ADOPTIQ_REPAIR_TEST_COMMAND", ""))
    parser.add_argument("--max-repair-attempts", type=int, default=1)
    parser.add_argument("--no-repair-on-recommendations", action="store_true")
    parser.add_argument("--stop-on-unrepaired", action="store_true")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.started_at_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    args.downloads_dir = args.downloads_dir.expanduser().resolve()
    args.bundle_dir = args.bundle_dir.expanduser().resolve()
    args.baseline_manifest = args.baseline_manifest.expanduser().resolve()
    args.iterations = max(int(args.iterations), 1)
    args.max_repair_attempts = max(int(args.max_repair_attempts), 0)
    args.scenarios = [item.strip().lower() for item in str(args.scenarios).split(",") if item.strip()] or ["all"]
    args.repair_on_recommendations = not bool(args.no_repair_on_recommendations)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_supervisor(args)
    print(f"[supervisor-summary] {summary['summary_path']}")
    # Round 53.3: a scenario that needed repair and did not end green
    # is a real failure regardless of ``--stop-on-unrepaired`` so CI/cron
    # wrappers see a non-zero exit instead of falsely declaring success.
    if summary.get("aborted") or summary.get("unrepaired_events"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
