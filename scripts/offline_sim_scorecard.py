#!/usr/bin/env python3
"""Offline sim scorecard — PASS / FAIL / SKIPPED / UNKNOWN.

Round 169.5 SSoT for Cloud/Bob reporting and corpus-blocker fail-closed.
Skipped gates are not passes. Blocked in-repo / missing-external corpus
kinds fail the sim instead of skip-passing.

Never claims live Cisco accuracy. Honesty stamps stay false.
AdoptIQ stays PRIVATE — do not change visibility.
"""
# Round 169.5

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# Scorecard labels, not credentials. S105 flags any *PASS = "PASS" assignment.
VERDICT_PASS = "PASS"  # noqa: S105
VERDICT_FAIL = "FAIL"
VERDICT_SKIPPED = "SKIPPED"
VERDICT_UNKNOWN = "UNKNOWN"

BLOCKED_CORPUS_KINDS = frozenset(
    {
        "blocked_in_repo_corpus",
        "blocked_missing_external",
    }
)
RUNNABLE_CORPUS_KINDS = frozenset(
    {
        "synthetic_checked_in",
        "external_operator_dir",
        "synthetic",
    }
)

HONESTY_FALSE_KEYS = (
    "live_validation_performed",
    "production_accuracy_claimed",
    "release_ready",
)
OFFLINE_FALSE_KEYS = (*HONESTY_FALSE_KEYS, "ready_for_live_cisco")
GATE_EVIDENCE_KEYS = frozenset({"status", "ran", "ok", "exit_code"})


def corpus_action(kind: str) -> str:
    """Decide replay/production-sim action for a resolved corpus kind.

    ``run`` — a usable synthetic or external workbook dir.
    ``skip`` — no corpus present (honest skip, not a pass claim).
    ``fail`` — operator pointed at a blocked or invalid path (fail-closed).
    Unknown kinds fail closed so a typo cannot skip-pass.
    """
    # Round 169.5
    normalized = str(kind or "").strip()
    if normalized in RUNNABLE_CORPUS_KINDS:
        return "run"
    if normalized == "skipped_no_corpus":
        return "skip"
    return "fail"


def corpus_status(kind: str) -> str:
    action = corpus_action(kind)
    if action == "fail":
        return f"failed_{kind}"
    if action == "skip":
        return "skipped_no_corpus"
    return "ran"


def gate_verdict(
    *,
    ran: bool | None = None,
    exit_code: int | None = None,
    ok: bool | None = None,
    status: str = "",
) -> str:
    """Map a gate row to PASS / FAIL / SKIPPED / UNKNOWN.

    Skip statuses never become PASS. Fail-closed corpus blockers are FAIL
    even if a caller left exit_code at 0.
    """
    # Round 169.5
    status_l = str(status or "").strip().casefold()
    if status_l.startswith("failed_") or status_l in BLOCKED_CORPUS_KINDS:
        return VERDICT_FAIL
    if (
        status_l.startswith("skipped")
        or status_l.startswith("covered_by")
        or status_l == "unknown"
    ):
        return VERDICT_SKIPPED if status_l != "unknown" else VERDICT_UNKNOWN
    if ok is False:
        return VERDICT_FAIL
    # A non-zero process result is failure evidence even if a malformed or
    # stale producer also stamped ``ok=true``. Never let an optimistic field
    # outrank the concrete exit status.
    if type(exit_code) is int and exit_code != 0:
        return VERDICT_FAIL
    if ran is False:
        return VERDICT_UNKNOWN
    if ok is True:
        return VERDICT_PASS
    if exit_code is None:
        return VERDICT_UNKNOWN
    if type(exit_code) is not int:
        return VERDICT_FAIL
    return VERDICT_PASS if exit_code == 0 else VERDICT_FAIL


def counts_as_failure(verdict: str) -> bool:
    return verdict == VERDICT_FAIL


def enrich_gates(gates: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach a verdict to each gate dict. Does not mutate the input map."""
    rows: list[dict[str, Any]] = []
    for name, raw in gates.items():
        row = dict(raw) if isinstance(raw, dict) else {"ok": bool(raw)}
        status = str(row.get("status") or "")
        ran = row.get("ran")
        if ran is None:
            ran = not (
                status.startswith("skipped")
                or status.startswith("covered_by")
                or status.startswith("failed_")
                or status == "unknown"
            )
        verdict = gate_verdict(
            ran=bool(ran) if status or ran is not None else None,
            exit_code=row.get("exit_code"),
            ok=row.get("ok"),
            status=status,
        )
        rows.append(
            {
                "name": name,
                "verdict": verdict,
                "ok": row.get("ok"),
                "exit_code": row.get("exit_code"),
                "status": status,
                "detail": str(row.get("detail") or row.get("reason") or "")[:240],
            }
        )
    return rows


def required_failed(rows: list[dict[str, Any]]) -> list[str]:
    return [row["name"] for row in rows if counts_as_failure(str(row.get("verdict")))]


def offline_honesty_violations(payload: dict[str, Any]) -> list[str]:
    """Return offline-only stamps that are missing or not exactly false.

    The scorecard normalizes its emitted stamps to ``False`` so downstream
    readers cannot mistake the offline sim for live proof.  Normalization must
    not hide an upstream regression, though: a missing, truthy, or string value
    is a failed honesty contract rather than something to silently rewrite.
    """
    # Round 169.6
    return [key for key in OFFLINE_FALSE_KEYS if payload.get(key) is not False]


def offline_gate_contract_violations(payload: dict[str, Any]) -> list[str]:
    """Return malformed gate paths that must not be rewritten as green."""
    # Round 169.7
    gates = payload.get("gates")
    if not isinstance(gates, dict) or not gates:
        return ["gates"]

    violations: list[str] = []
    for name, raw in gates.items():
        path = f"gates.{name}" if str(name).strip() else "gates.<empty>"
        if not isinstance(name, str) or not name.strip():
            violations.append(path)
            continue
        if not isinstance(raw, dict) or not raw or not GATE_EVIDENCE_KEYS.intersection(raw):
            violations.append(path)
            continue
        if "status" in raw and (
            not isinstance(raw["status"], str) or not raw["status"].strip()
        ):
            violations.append(f"{path}.status")
        if "ran" in raw and type(raw["ran"]) is not bool:
            violations.append(f"{path}.ran")
        if "ok" in raw and type(raw["ok"]) is not bool:
            violations.append(f"{path}.ok")
        if "exit_code" in raw and type(raw["exit_code"]) is not int:
            violations.append(f"{path}.exit_code")
    return violations


def format_scorecard(
    title: str,
    rows: list[dict[str, Any]],
    *,
    honesty: dict[str, Any] | None = None,
) -> str:
    lines = [title, ""]
    stamps = honesty or {}
    lines.append(
        "Honesty: "
        + "  ".join(f"{key}=false" for key in HONESTY_FALSE_KEYS)
        + "  sim≠live"
    )
    if stamps.get("repo_must_stay_private") is True:
        lines.append("Repo: PRIVATE — do not change visibility. Do not blame billing.")
    lines.append("")
    width = max((len(str(row.get("name") or "")) for row in rows), default=8)
    for row in rows:
        name = str(row.get("name") or "")
        verdict = str(row.get("verdict") or VERDICT_UNKNOWN)
        extra = str(row.get("status") or row.get("detail") or "").strip()
        suffix = f"  ({extra})" if extra else ""
        lines.append(f"{verdict:<8} {name:<{width}}{suffix}")
    failed = required_failed(rows)
    skipped = [row["name"] for row in rows if row.get("verdict") == VERDICT_SKIPPED]
    unknown = [row["name"] for row in rows if row.get("verdict") == VERDICT_UNKNOWN]
    lines.append("")
    lines.append(
        "required_failed="
        + (",".join(failed) if failed else "none")
        + "  skipped="
        + (",".join(skipped) if skipped else "none")
        + "  unknown="
        + (",".join(unknown) if unknown else "none")
    )
    lines.append("ready_for_live_cisco=false")
    return "\n".join(lines) + "\n"


def enrich_bob_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a bash-sim summary with verdicts. Fail-closed on FAIL rows."""
    # Round 169.5 / Round 169.6 / Round 169.7
    out = dict(payload)
    gates = out.get("gates") if isinstance(out.get("gates"), dict) else {}
    rows = enrich_gates(gates)
    gate_contract_violations = offline_gate_contract_violations(payload)
    if gate_contract_violations:
        rows.append(
            {
                "name": "offline_gate_contract",
                "verdict": VERDICT_FAIL,
                "ok": False,
                "exit_code": None,
                "status": "failed_gate_contract",
                "detail": "missing or malformed: " + ", ".join(gate_contract_violations),
            }
        )
    honesty_violations = offline_honesty_violations(payload)
    if honesty_violations:
        rows.append(
            {
                "name": "offline_honesty_contract",
                "verdict": VERDICT_FAIL,
                "ok": False,
                "exit_code": None,
                "status": "failed_honesty_contract",
                "detail": "must be exactly false: " + ", ".join(honesty_violations),
            }
        )
    failed = required_failed(rows)
    out["scorecard"] = {
        "gates": rows,
        "required_failed": failed,
        "skipped": [row["name"] for row in rows if row.get("verdict") == VERDICT_SKIPPED],
        "unknown": [row["name"] for row in rows if row.get("verdict") == VERDICT_UNKNOWN],
    }
    out["all_passed"] = not failed
    out["ready_for_live_cisco"] = False
    out["live_validation_performed"] = False
    out["production_accuracy_claimed"] = False
    out["release_ready"] = False
    out["offline_gate_contract_valid"] = not gate_contract_violations
    out["offline_honesty_contract_valid"] = not honesty_violations
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-action",
        metavar="KIND",
        help="Print run|skip|fail for a resolved corpus kind",
    )
    parser.add_argument(
        "--enrich-bob-summary",
        type=Path,
        help="Rewrite a bash-sim summary JSON with verdicts and print the table",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.corpus_action is not None:
        print(corpus_action(args.corpus_action))
        return 0
    if args.enrich_bob_summary is not None:
        path = args.enrich_bob_summary.expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            print("summary is not a JSON object", file=sys.stderr)
            return 2
        enriched = enrich_bob_summary(payload)
        path.write_text(json.dumps(enriched, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            format_scorecard(
                "AdoptIQ offline Bob sim scorecard",
                enriched["scorecard"]["gates"],
                honesty=enriched,
            )
        )
        print(
            json.dumps(
                {
                    "all_passed": enriched["all_passed"],
                    "ready_for_live_cisco": False,
                    "live_validation_performed": False,
                },
                sort_keys=True,
            )
        )
        return 0 if enriched["all_passed"] else 1
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
