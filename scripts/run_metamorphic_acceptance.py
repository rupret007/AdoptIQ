#!/usr/bin/env python3
"""Round 168 metamorphic acceptance over Round 145 local fixtures.

These checks never touch Snowflake, Keeper, CircuIT, or real CSOne.  They
assert invariants that must hold after order, alias, and window transforms
of the sanitized fixture frames.  Results always record
``live_validation_performed=false``.
"""
# Round 168

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import canonical_metrics as cm  # noqa: E402
from data_normalization import (  # noqa: E402
    _clean_name_for_key,
    add_case_lifecycle_fields,
    collapse_customer_name_set,
    customer_names_match,
)
from local_acceptance_lab import (  # noqa: E402
    SOURCE_MODE,
    build_scenario_bundle,
)


SCHEMA_VERSION = "metamorphic-acceptance/v1"
AS_OF = "2026-08-03T21:00:00Z"


def _safe_summary_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        relative = resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] != ".adoptiq-acceptance":
        raise ValueError("in-repository summaries must stay under .adoptiq-acceptance")
    return resolved


def _shuffle(frame: pd.DataFrame, seed: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    shuffled = frame.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    shuffled.attrs.update(frame.attrs)
    return shuffled


def _tac_counts(frame: pd.DataFrame) -> dict[str, int]:
    normalized = add_case_lifecycle_fields(frame, as_of=AS_OF)
    return {
        "total": int(cm.count_total_tac(normalized)),
        "open": int(cm.count_open_tac(normalized)),
        "closed": int(cm.count_closed_tac(normalized)),
    }


def _check(
    name: str,
    predicate: Callable[[], bool],
    detail: str,
) -> dict[str, Any]:
    ok = bool(predicate())
    return {"name": name, "passed": ok, "detail": detail}


def run_metamorphic_acceptance() -> dict[str, Any]:
    healthy = build_scenario_bundle("healthy")
    ambiguous = build_scenario_bundle("ambiguous_customer")
    timezone = build_scenario_bundle("timezone_boundary")

    tac = healthy.frame("tac_cases")
    action_plans = healthy.frame("action_plans")
    subscriptions = healthy.frame("subscriptions")
    baseline_tac = _tac_counts(tac)
    # Round 168: APs are the optional second argument, never the AB positional.
    baseline_open_aps = int(cm.count_open_action_plans(None, ap_df=action_plans))
    baseline_customers = int(cm.count_customers(subs_df=subscriptions))

    shuffled_tac = _shuffle(tac, seed=168)
    shuffled_aps = _shuffle(action_plans, seed=168)
    shuffled_subs = _shuffle(subscriptions, seed=42)

    rebuilt = build_scenario_bundle("healthy")
    rebuilt_tac = _tac_counts(rebuilt.frame("tac_cases"))

    # Round 168: bundled alias SSoT is NYU, not case-fold of fixture names.
    nyu_aliases = {
        "NYU MEDICAL CENTER",
        "NYU LANGONE HEALTH SYSTEMS",
        "NYULH",
    }
    collapsed = collapse_customer_name_set(nyu_aliases)

    checks = [
        _check(
            "tac_row_order_invariance",
            lambda: _tac_counts(shuffled_tac) == baseline_tac,
            "Shuffling TAC rows must not change total/open/closed counts.",
        ),
        _check(
            "action_plan_row_order_invariance",
            lambda: int(cm.count_open_action_plans(None, ap_df=shuffled_aps))
            == baseline_open_aps,
            "Shuffling Action Plan rows must not change the open count.",
        ),
        _check(
            "customer_row_order_invariance",
            lambda: int(cm.count_customers(subs_df=shuffled_subs))
            == baseline_customers,
            "Shuffling subscription rows must not change the customer count.",
        ),
        _check(
            "rebuild_is_deterministic",
            lambda: rebuilt_tac == baseline_tac
            and int(
                cm.count_open_action_plans(None, ap_df=rebuilt.frame("action_plans"))
            )
            == baseline_open_aps,
            "Rebuilding the healthy fixture must reproduce the same KPI counts.",
        ),
        _check(
            "bundled_nyu_aliases_match",
            lambda: customer_names_match(
                "NYU MEDICAL CENTER",
                "NYU LANGONE HEALTH SYSTEMS",
            ),
            "Bundled NYU / NYULH aliases must match through the SSoT helper.",
        ),
        _check(
            "bundled_nyu_alias_set_collapses",
            lambda: len(collapsed) == 1,
            "NYU MEDICAL CENTER / NYU LANGONE HEALTH SYSTEMS / NYULH collapse to one identity.",
        ),
        _check(
            "join_key_casefolds_without_aliasing_display",
            lambda: _clean_name_for_key("Acme Corporation")
            == _clean_name_for_key("ACME CORPORATION")
            and not customer_names_match("Acme Corporation", "ACME CORPORATION"),
            "Join keys case-fold; display names do not alias unless registered.",
        ),
        _check(
            "ambiguous_scenario_keeps_extra_customer_row",
            lambda: int(ambiguous.expected_counts["customers"])
            == int(healthy.expected_counts["customers"]) + 1,
            "The ambiguous_customer mutation must add exactly one extra customer row.",
        ),
        _check(
            "timezone_boundary_adds_one_activity",
            lambda: int(timezone.expected_counts["activities"])
            == int(healthy.expected_counts["activities"]) + 1,
            "The timezone_boundary mutation must add exactly one activity.",
        ),
        _check(
            "fixture_stamps_remain_honest",
            lambda: tac.attrs.get("live_validation_performed") is False
            and tac.attrs.get("source_mode") == SOURCE_MODE
            and action_plans.attrs.get("sanitized") is True,
            "Metamorphic inputs stay fixture-stamped and non-live.",
        ),
    ]
    all_passed = all(item["passed"] for item in checks)
    return {
        "schema_version": SCHEMA_VERSION,
        "sanitized": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "release_ready": False,
        "source_mode": SOURCE_MODE,
        "all_passed": all_passed,
        "check_count": len(checks),
        "passed_count": sum(1 for item in checks if item["passed"]),
        "baseline": {
            "tac": baseline_tac,
            "open_action_plans": baseline_open_aps,
            "customers": baseline_customers,
        },
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fixture-only metamorphic acceptance; never live validation."
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT
        / ".adoptiq-acceptance"
        / "offline-bob-sim"
        / "metamorphic_summary.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_metamorphic_acceptance()
    dest = _safe_summary_path(args.summary_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "all_passed",
        "passed_count",
        "check_count",
        "live_validation_performed",
        "production_accuracy_claimed",
        "schema_version",
    )}, sort_keys=True))
    return 0 if payload["all_passed"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
