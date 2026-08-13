#!/usr/bin/env python3
"""Run real Snowflake fetchers against the fail-closed local DB-API simulator."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import adoptiq_backend as backend  # noqa: E402
from local_acceptance_lab import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    SOURCE_MODE,
    assert_safe_activation,
    build_scenario_bundle,
)
from local_snowflake_simulator import (  # noqa: E402
    FixtureSnowflakeConnection,
    UnexpectedSimulatedQuery,
)


def _distinct(frame: pd.DataFrame, column: str) -> int:
    if column not in frame.columns:
        return 0
    values = frame[column].fillna("").astype(str).str.strip()
    return int(values.loc[values.ne("")].nunique())


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    if REPO_ROOT == resolved or REPO_ROOT in resolved.parents:
        try:
            relative = resolved.relative_to(REPO_ROOT)
        except ValueError:  # pragma: no cover - guarded by containment above
            relative = None
        if relative is not None and not str(relative).startswith(".adoptiq-acceptance/"):
            raise ValueError("local source-contract summaries inside the repo must use .adoptiq-acceptance")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, resolved)


def run_contracts(*, scenario: str, manifest: Path) -> dict[str, Any]:
    bundle = build_scenario_bundle(scenario, manifest)
    connection = FixtureSnowflakeConnection(bundle)
    ownership = bundle.frame("ownership")
    emails = ownership["OWNER_EMAIL"].astype(str).tolist()

    original_blocked = backend.is_table_blocked
    original_guard = backend.guard_table
    original_connect = backend._connect_with_keeper
    backend.invalidate_table_column_cache()
    try:
        # This simulator proves the fetcher's behavior when an approved table
        # is available. Production policy remains untouched outside this
        # reversible local-only call.
        backend.is_table_blocked = lambda _table: False
        backend.guard_table = lambda _table: None

        subscriptions = backend.get_subscriptions_for_team(connection, emails)
        account_ids = sorted(
            {
                str(value).strip()
                for value in subscriptions.get("ACCOUNT_ID_C", pd.Series(dtype=str))
                if str(value).strip()
            }
        )
        customer_names = sorted(
            {
                str(value).strip()
                for value in subscriptions.get("BU_NAME", pd.Series(dtype=str))
                if str(value).strip()
            }
        )
        action_plans = backend.fetch_csconsole_action_plans(
            connection, account_ids, 90, owner_emails=emails
        )
        barriers = backend.fetch_csconsole_adoption_barriers(
            connection, account_ids, 90, owner_emails=emails
        )
        legacy_barriers = backend.fetch_adoption_barriers(connection, account_ids, 90)
        pulse = backend.fetch_csconsole_customer_pulse(
            connection, account_ids, 90, owner_emails=emails
        )
        priorities = backend.fetch_csconsole_success_priorities(
            connection, customer_names, 90
        )
        support = backend.fetch_support_cases_snowflake(connection, account_ids, 90)
        enhanced = backend.fetch_enhanced_account_insights(
            connection, account_ids, 90, as_of=bundle.as_of_utc
        )

        # These public helpers own their connection lifecycle. Give each a
        # dedicated simulator connection so their real close/finally paths are
        # exercised without invalidating the main multi-fetch connection.
        customer_search_connection = FixtureSnowflakeConnection(bundle)
        backend._connect_with_keeper = lambda: customer_search_connection
        customer_search = backend.search_subscriptions_by_customer(
            "Beta Industries",
            limit=50,
        )

        diag = subscriptions.attrs.get("_r82_team_subs_diag") or {}
        counts = {
            "team_attribution_rows": len(subscriptions),
            "subscriptions_distinct": _distinct(subscriptions, "SUBSCRIPTION_ID"),
            "action_plans_rows": len(action_plans),
            "action_plans_distinct": _distinct(action_plans, "ID"),
            "adoption_barriers_rows": len(barriers),
            "adoption_barriers_distinct": _distinct(barriers, "ID"),
            "legacy_adoption_barriers_rows": len(legacy_barriers),
            "customer_pulse_rows": len(pulse),
            "customer_pulse_distinct": _distinct(pulse, "ID"),
            "success_priorities_rows": len(priorities),
            "success_priorities_distinct": _distinct(priorities, "ID"),
            "support_cases_rows": len(support),
            "support_cases_distinct": _distinct(support, "CASE_ID"),
            "enhanced_accounts": int(
                (enhanced.get("account_summary") or {}).get("count") or 0
            ),
            "active_contracts": int(
                (enhanced.get("contracts") or {}).get("active_contracts") or 0
            ),
            "contracts_expiring_90d": int(
                (enhanced.get("contracts") or {}).get("expiring_within_90d") or 0
            ),
            "recently_expired_accounts": int(
                (enhanced.get("recently_expired") or {}).get("count") or 0
            ),
            "renewal_rows": int(
                (enhanced.get("renewals") or {}).get("count") or 0
            ),
            "at_risk_renewals": int(
                (enhanced.get("renewals") or {}).get("at_risk_total") or 0
            ),
        }
        expected = {
            "team_attribution_rows": 7,
            "subscriptions_distinct": 6,
            "action_plans_rows": 7,
            "action_plans_distinct": 6,
            "adoption_barriers_rows": 3,
            "adoption_barriers_distinct": 3,
            "legacy_adoption_barriers_rows": 4,
            "customer_pulse_rows": 3,
            "customer_pulse_distinct": 3,
            "success_priorities_rows": 3,
            "success_priorities_distinct": 3,
            "support_cases_rows": 3,
            "support_cases_distinct": 3,
            "enhanced_accounts": 3,
            "active_contracts": 3,
            "contracts_expiring_90d": 2,
            "recently_expired_accounts": 1,
            "renewal_rows": 3,
            "at_risk_renewals": 1,
        }
        count_contract_ok = counts == expected
        enhanced_contract_ok = bool(
            not (enhanced.get("_meta") or {}).get("subsection_errors")
            and (enhanced.get("contracts") or {}).get("is_multi_currency") is True
            and (enhanced.get("contracts") or {}).get("expiring_arr") is None
            and set(
                (enhanced.get("contracts") or {}).get("expiring_arr_by_currency")
                or {}
            )
            == {"EUR", "USD"}
            and (enhanced.get("renewals") or {}).get("avg_probability") == 74.7
            and (enhanced.get("renewals") or {}).get("min_probability") == 58.0
            and (enhanced.get("_meta") or {}).get("evaluation_as_of_date")
            == "2026-08-03"
            and len((enhanced.get("contracts") or {}).get("details") or []) == 3
            and all(
                detail.get("account_id") and detail.get("contract")
                for detail in (enhanced.get("contracts") or {}).get("details") or []
            )
        )
        secondary_attribution_ok = bool(
            diag.get("primary_rows") == 2
            and diag.get("secondary_rows") == 5
            and diag.get("merged_rows") == 7
            and "DSM_EMAIL1" in (diag.get("secondary_email_columns_used") or [])
        )
        customer_search_attribution_ok = bool(
            customer_search
            and all(row.get("CSSM_EMAIL") for row in customer_search)
            and {
                str(row.get("ACCOUNT_ID_C") or "")
                for row in customer_search
            }
            == {"ACC-002"}
        )

        failed_connection = FixtureSnowflakeConnection(
            bundle, fail_families={"customer_pulse"}
        )
        backend.invalidate_table_column_cache()
        failed_pulse = backend.fetch_csconsole_customer_pulse(
            failed_connection, account_ids, 90
        )
        failure_state_ok = bool(
            failed_pulse.empty
            and failed_pulse.attrs.get("fetch_error_kind")
            and not failed_pulse.attrs.get("true_zero")
        )

        unknown_query_rejected = False
        try:
            connection.cursor().execute("SELECT * FROM UNDECLARED_LOCAL_TABLE")
        except UnexpectedSimulatedQuery:
            unknown_query_rejected = True

        trace = connection.trace_summary()
        customer_search_trace = customer_search_connection.trace_summary()
        trace["query_count"] += customer_search_trace["query_count"]
        for family, count in customer_search_trace["families"].items():
            trace["families"][family] = trace["families"].get(family, 0) + count
        trace["families"] = dict(sorted(trace["families"].items()))
        trace["all_data_queries_parameterized"] = bool(
            trace["all_data_queries_parameterized"]
            and customer_search_trace["all_data_queries_parameterized"]
        )
        trace["query_fingerprints"] = sorted(
            set(trace["query_fingerprints"])
            | set(customer_search_trace["query_fingerprints"])
        )
        trace_ok = bool(
            trace["query_count"] >= 10
            and trace["all_data_queries_parameterized"]
            and {
                "team_subscriptions",
                "customer_subscription_search",
                "action_plans",
                "adoption_barriers",
                "customer_pulse",
                "success_priorities",
                "support_cases",
                "account_summary",
                "contract_aggregate",
                "contracts",
                "recently_expired",
                "renewal_aggregate",
                "renewals",
            }
            <= set(trace["families"])
        )
        all_passed = bool(
            count_contract_ok
            and enhanced_contract_ok
            and secondary_attribution_ok
            and customer_search_attribution_ok
            and failure_state_ok
            and unknown_query_rejected
            and trace_ok
        )
        return {
            "schema_version": "local-snowflake-source-contracts/v1",
            "sanitized": True,
            "do_not_commit": True,
            "source_mode": SOURCE_MODE,
            "scenario": scenario,
            "live_validation_attempted": False,
            "live_validation_performed": False,
            "production_accuracy_claimed": False,
            "all_passed": all_passed,
            "checks": {
                "count_contract": count_contract_ok,
                "enhanced_account_contract": enhanced_contract_ok,
                "secondary_attribution": secondary_attribution_ok,
                "customer_search_attribution": customer_search_attribution_ok,
                "failure_state_not_zero": failure_state_ok,
                "unknown_query_rejected": unknown_query_rejected,
                "parameter_binding_and_family_coverage": trace_ok,
            },
            "counts": counts,
            "expected_counts": expected,
            "secondary_attribution": {
                "primary_rows": int(diag.get("primary_rows") or 0),
                "secondary_rows": int(diag.get("secondary_rows") or 0),
                "merged_rows": int(diag.get("merged_rows") or 0),
                "duplicate_rows_dropped": int(diag.get("duplicate_rows_dropped") or 0),
            },
            "query_trace": trace,
        }
    finally:
        backend.is_table_blocked = original_blocked
        backend.guard_table = original_guard
        backend._connect_with_keeper = original_connect
        backend.invalidate_table_column_cache()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise real Snowflake fetchers with sanitized local DB-API fixtures."
    )
    parser.add_argument("--enable-local-fixtures", action="store_true")
    parser.add_argument("--scenario", default="healthy")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--summary",
        type=Path,
        default=REPO_ROOT / ".adoptiq-acceptance" / "source-contracts" / "summary.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assert_safe_activation(
        explicit=bool(args.enable_local_fixtures),
        host="127.0.0.1",
    )
    payload = run_contracts(scenario=str(args.scenario), manifest=Path(args.manifest))
    _write_summary(Path(args.summary), payload)
    print(
        json.dumps(
            {
                "all_passed": payload["all_passed"],
                "query_count": payload["query_trace"]["query_count"],
                "summary": str(Path(args.summary).expanduser().resolve()),
            },
            sort_keys=True,
        )
    )
    return 0 if payload["all_passed"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
