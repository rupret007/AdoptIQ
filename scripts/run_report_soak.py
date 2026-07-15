#!/usr/bin/env python3
"""Round 101: time-boxed live report soak supervisor.

This script wraps the existing live report iteration harness without adding
any auto-repair behavior. It is intended for unattended demo-readiness runs:
exercise real report endpoints for a bounded duration, preserve every child
summary/log, and write one rollup JSON for morning triage.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_MANIFEST = REPO_ROOT / "baselines" / "round72" / "baseline_manifest.json"
DEFAULT_SCENARIOS = ("comprehensive", "compact", "renewal", "leader")
SUMMARY_RE = re.compile(r"^\[summary\]\s+(?P<path>.+\.json)\s*$", re.MULTILINE)
DEFAULT_MIN_FREE_GB = 5.0


def _data_volume_for_disk_check() -> Path:
    data = Path("/System/Volumes/Data")
    return data if data.is_dir() else Path.home()


def check_acceptance_disk_space(*, min_gb: float = DEFAULT_MIN_FREE_GB) -> tuple[bool, str]:
    """Round 140: refuse soak when the data volume is below the acceptance floor."""
    usage = shutil.disk_usage(_data_volume_for_disk_check())
    avail_gb = usage.free / (1024**3)
    if avail_gb < min_gb:
        return False, f"insufficient_disk: {avail_gb:.2f} GiB free < {min_gb} GiB required"
    return True, f"disk_ok: {avail_gb:.2f} GiB free"


def _warn_if_port_busy(port: int = 5151) -> None:
    """Round 140: operator hygiene — do not auto-kill; warn only."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                print(
                    f"WARN: port {port} appears in use — ensure AdoptIQ is the expected instance; "
                    "quit manually (navbar Quit) before soak if a stale process is listening.",
                    flush=True,
                )
    except OSError:
        pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_stamp() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_") or "soak"


def _parse_scenarios(value: str) -> list[str]:
    raw = [item.strip().lower() for item in str(value or "").split(",") if item.strip()]
    if not raw or raw == ["all"]:
        return list(DEFAULT_SCENARIOS)
    allowed = set(DEFAULT_SCENARIOS)
    unknown = [item for item in raw if item not in allowed]
    if unknown:
        raise ValueError(f"unknown scenario(s): {', '.join(unknown)}")
    return raw


def _parse_summary_path(output: str) -> Path | None:
    match = SUMMARY_RE.search(output or "")
    if not match:
        return None
    return Path(match.group("path").strip()).expanduser()


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"_load_error": "summary_unreported"}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {"_load_error": f"summary_unreadable: {exc}"}
    except (TypeError, ValueError) as exc:
        return {"_load_error": f"summary_invalid_json: {exc}"}
    if not isinstance(loaded, dict):
        return {"_load_error": "summary_not_object"}
    return loaded


def _status_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported base-url scheme: {parsed.scheme or '<missing>'}")
    if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost"}:
        raise ValueError("report soak base-url must target the local AdoptIQ app")
    return f"{base_url.rstrip('/')}/api/status/all"


def _extract_running_reports(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        analyses = payload.get("statuses", payload.get("analyses", payload))
    else:
        analyses = payload
    if isinstance(analyses, dict):
        values = analyses.values()
    elif isinstance(analyses, list):
        values = analyses
    else:
        values = []
    running: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict) and item.get("status") == "running":
            running.append({
                "analysis_id": item.get("analysis_id") or item.get("id"),
                "report_type": item.get("report_type"),
                "manager": item.get("manager"),
                "progress": item.get("progress"),
                "current_step": item.get("current_step"),
            })
    return running


def probe_running_reports(base_url: str, *, timeout_s: float = 5.0) -> list[dict[str, Any]]:
    try:
        with urllib.request.urlopen(_status_url(base_url), timeout=timeout_s) as response:  # noqa: S310  # nosec B310
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise RuntimeError(f"could not read AdoptIQ status endpoint: {exc}") from exc
    return _extract_running_reports(payload)


def build_runner_command(
    args: argparse.Namespace,
    *,
    scenario: str,
    run_id: str,
    child_downloads_dir: Path,
    scenario_timeout: int | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_report_iteration_loop.py"),
        "--base-url",
        args.base_url,
        "--downloads-dir",
        str(child_downloads_dir),
        "--iterations",
        "1",
        "--scenarios",
        scenario,
        "--run-id",
        run_id,
        "--baseline-mode",
        args.baseline_mode,
        "--strict",
        "--stop-on-failure",
        "--timeout",
        str(int(scenario_timeout if scenario_timeout is not None else args.scenario_timeout)),
        "--request-timeout",
        str(int(args.request_timeout)),
        "--download-timeout",
        str(int(args.download_timeout)),
        "--poll-interval",
        str(args.poll_interval),
    ]
    if args.baseline_mode == "manifest":
        command.extend(["--baseline-manifest", str(args.baseline_manifest)])
    return command


def run_command(command: list[str], *, cwd: Path, timeout: int) -> dict[str, Any]:
    started = _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        completed = subprocess.run(  # noqa: S603 - command argv is fully constructed by this script.
            command,
            cwd=str(cwd),
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
            "started_at_utc": started,
            "completed_at_utc": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"Timed out after {timeout}s",
            "started_at_utc": started,
            "completed_at_utc": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value or "", encoding="utf-8")


def _child_event(
    *,
    iteration: int,
    scenario: str,
    run_id: str,
    command: list[str],
    runner: dict[str, Any],
    summary_path: Path | None,
    summary: dict[str, Any],
    stdout_path: Path,
    stderr_path: Path,
    remaining_before_s: float,
) -> dict[str, Any]:
    load_error = summary.get("_load_error")
    child_passed = (
        runner.get("returncode") == 0
        and not load_error
        and bool(summary.get("all_passed"))
    )
    return {
        "iteration": iteration,
        "scenario": scenario,
        "run_id": run_id,
        "command": command,
        "returncode": runner.get("returncode"),
        "passed": child_passed,
        "summary_path": str(summary_path) if summary_path else None,
        "summary_load_error": load_error,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "remaining_before_s": round(float(remaining_before_s), 3),
        "child_all_passed": summary.get("all_passed"),
        "child_aborted": summary.get("aborted"),
        "result_count": len(summary.get("results") or []),
        "started_at_utc": runner.get("started_at_utc"),
        "completed_at_utc": runner.get("completed_at_utc"),
    }


def run_soak(
    args: argparse.Namespace,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    command_runner: Callable[..., dict[str, Any]] = run_command,
    running_probe: Callable[..., list[dict[str, Any]]] = probe_running_reports,
) -> dict[str, Any]:
    args.downloads_dir.mkdir(parents=True, exist_ok=True)
    started_at_utc = _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    start = monotonic()
    deadline = start + float(args.duration_seconds)
    events: list[dict[str, Any]] = []
    abort_reason = ""

    # Round 140: disk preflight before any child harness work.
    disk_ok, disk_detail = check_acceptance_disk_space(
        min_gb=float(getattr(args, "min_free_gb", DEFAULT_MIN_FREE_GB))
    )
    if not disk_ok:
        print(f"[soak-abort] {disk_detail}", flush=True)
        summary = {
            "run_id": args.run_id,
            "started_at_utc": started_at_utc,
            "completed_at_utc": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_seconds": args.duration_seconds,
            "aborted": True,
            "abort_reason": "insufficient_disk",
            "disk_detail": disk_detail,
            "events": events,
            "passed": False,
        }
        return _persist_summary(args, summary)

    _warn_if_port_busy()

    try:
        running_reports = running_probe(args.base_url, timeout_s=args.status_timeout)
    except TypeError:
        running_reports = running_probe(args.base_url)
    if running_reports and not args.allow_existing_running:
        abort_reason = "active_reports_present"
        summary = {
            "run_id": args.run_id,
            "started_at_utc": started_at_utc,
            "completed_at_utc": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_seconds": args.duration_seconds,
            "aborted": True,
            "abort_reason": abort_reason,
            "running_reports": running_reports,
            "events": events,
            "passed": False,
        }
        return _persist_summary(args, summary)

    failure_count = 0
    iteration = 1
    while monotonic() < deadline:
        started_any = False
        for scenario in args.scenarios:
            remaining = deadline - monotonic()
            if remaining < args.min_remaining_seconds:
                abort_reason = "deadline_remaining_too_small"
                break
            started_any = True
            run_id = f"{args.run_id}-iter{iteration}-{scenario}"
            child_dir = args.downloads_dir / _slug(run_id)
            child_dir.mkdir(parents=True, exist_ok=True)
            child_timeout = max(60, min(int(args.scenario_timeout), int(remaining - 30)))
            command = build_runner_command(
                args,
                scenario=scenario,
                run_id=run_id,
                child_downloads_dir=child_dir,
                scenario_timeout=child_timeout,
            )
            runner_timeout = max(child_timeout + 120, int(args.runner_timeout))
            runner = command_runner(command, cwd=REPO_ROOT, timeout=runner_timeout)
            stdout_path = child_dir / "runner.stdout.log"
            stderr_path = child_dir / "runner.stderr.log"
            _write_text(stdout_path, str(runner.get("stdout") or ""))
            _write_text(stderr_path, str(runner.get("stderr") or ""))
            summary_path = _parse_summary_path(str(runner.get("stdout") or ""))
            summary = _load_json(summary_path)
            event = _child_event(
                iteration=iteration,
                scenario=scenario,
                run_id=run_id,
                command=command,
                runner=runner,
                summary_path=summary_path,
                summary=summary,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                remaining_before_s=remaining,
            )
            events.append(event)
            print(
                "[soak-event] iteration=%s scenario=%s passed=%s returncode=%s summary=%s"
                % (iteration, scenario, event["passed"], event["returncode"], event["summary_path"]),
                flush=True,
            )
            if not event["passed"]:
                failure_count += 1
                if failure_count >= args.max_failures:
                    abort_reason = "max_failures_reached"
                    break
        if abort_reason:
            break
        if not started_any:
            break
        iteration += 1

    completed = _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    passed_count = sum(1 for event in events if event.get("passed"))
    failed_count = len(events) - passed_count
    summary = {
        "run_id": args.run_id,
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed,
        "duration_seconds": args.duration_seconds,
        "elapsed_seconds_observed": round(monotonic() - start, 3),
        "scenarios": args.scenarios,
        "baseline_manifest": str(args.baseline_manifest),
        "downloads_dir": str(args.downloads_dir),
        "aborted": bool(abort_reason and abort_reason != "deadline_remaining_too_small"),
        "abort_reason": abort_reason,
        "events_completed": len(events),
        "passed_events": passed_count,
        "failed_events": failed_count,
        "passed": bool(events) and failed_count == 0 and abort_reason != "max_failures_reached",
        "events": events,
    }
    return _persist_summary(args, summary)


def _persist_summary(args: argparse.Namespace, summary: dict[str, Any]) -> dict[str, Any]:
    out_path = args.downloads_dir / "soak_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    summary["summary_path"] = str(out_path)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a time-boxed live AdoptIQ report soak.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5151")
    parser.add_argument("--downloads-dir", default="")
    parser.add_argument("--duration-seconds", type=int, default=7200)
    parser.add_argument("--min-remaining-seconds", type=int, default=1200)
    parser.add_argument("--scenario-timeout", type=int, default=1800)
    parser.add_argument("--request-timeout", type=int, default=120)
    parser.add_argument("--download-timeout", type=int, default=300)
    parser.add_argument("--runner-timeout", type=int, default=2400)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--status-timeout", type=float, default=5.0)
    parser.add_argument("--max-failures", type=int, default=1)
    parser.add_argument("--allow-existing-running", action="store_true")
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=DEFAULT_MIN_FREE_GB,
        help="Round 140: minimum free GiB on the data volume before starting soak",
    )
    parser.add_argument("--scenarios", default="all")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--baseline-mode", choices=["off", "manifest"], default="manifest")
    parser.add_argument("--baseline-manifest", default=str(DEFAULT_BASELINE_MANIFEST))
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.scenarios = _parse_scenarios(args.scenarios)
    args.baseline_manifest = Path(args.baseline_manifest).expanduser().resolve()
    if args.baseline_mode == "manifest" and not args.baseline_manifest.exists():
        parser.error(f"baseline manifest does not exist: {args.baseline_manifest}")
    if not args.run_id:
        args.run_id = f"round101-soak-{_utc_stamp()}"
    if args.downloads_dir:
        args.downloads_dir = Path(args.downloads_dir).expanduser().resolve()
    else:
        args.downloads_dir = Path("~/Downloads").expanduser() / f"adoptiq_report_soak_{_slug(args.run_id)}"
    args.duration_seconds = max(60, int(args.duration_seconds))
    args.min_remaining_seconds = max(60, int(args.min_remaining_seconds))
    args.scenario_timeout = max(60, int(args.scenario_timeout))
    args.request_timeout = max(30, int(args.request_timeout))
    args.download_timeout = max(60, int(args.download_timeout))
    args.runner_timeout = max(60, int(args.runner_timeout))
    args.max_failures = max(1, int(args.max_failures))
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_soak(args)
    print(f"[soak-summary] {summary['summary_path']}")
    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
