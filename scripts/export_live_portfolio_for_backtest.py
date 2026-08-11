#!/usr/bin/env python3
"""Round 161 — export live leader-scope portfolio JSON for predictive backtest.

Requires VPN + Keeper/Snowflake credentials (same as the main app).

Example:
  python scripts/export_live_portfolio_for_backtest.py \\
    --manager "Brian Frazier" --days 365 \\
    --output .adoptiq-acceptance/brian_365d.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _df_to_records(df: pd.DataFrame | None) -> List[dict]:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    cleaned = df.where(pd.notna(df), None)
    return cleaned.to_dict(orient="records")


def _serialize_team_data(team_data: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, List[dict]]]:
    keys = (
        "tac_cases",
        "adoption_barriers",
        "customer_pulse",
        "action_plans",
        "success_priorities",
        "subscriptions",
    )
    out: Dict[str, Dict[str, List[dict]]] = {}
    for member, bundle in sorted(team_data.items()):
        if not isinstance(bundle, dict):
            continue
        out[str(member)] = {k: _df_to_records(bundle.get(k)) for k in keys}
    return out


def export_live_portfolio(
    *,
    manager: str,
    days: int,
    technology: str,
    output: Path,
    data_end: str | None = None,
    members: List[str] | None = None,
) -> dict:
    from adoptiq_backend import get_subscriptions_for_team
    from app_simple import TEAM_ROSTER, _connect_with_keeper
    from leader_report_generator import LeaderReportGenerator
    from leader_scope import validate_leader_scope_request

    ctx = _connect_with_keeper()
    scope_selection = validate_leader_scope_request(
        manager,
        "team",
        "",
        TEAM_ROSTER,
    )
    cssm_emails = [email for mgr, _name, email in TEAM_ROSTER if mgr == manager and email]
    team_subs_df = get_subscriptions_for_team(ctx, cssm_emails) if cssm_emails else pd.DataFrame()

    evaluation_as_of = datetime.now(timezone.utc)
    if data_end:
        evaluation_as_of = pd.to_datetime(data_end, utc=True).to_pydatetime()

    prefetch_meta = {
        "attempted_at": datetime.now(timezone.utc).isoformat(),
        "outcome": "attempting",
    }
    generator = LeaderReportGenerator(
        ctx,
        TEAM_ROSTER,
        data_retrieved_at=evaluation_as_of,
        leader_prefetch_meta=prefetch_meta,
    )
    direct_reports = [
        {"name": name, "email": email}
        for mgr, name, email in TEAM_ROSTER
        if mgr == manager and email
    ]
    if members:
        allow = {m.strip().casefold() for m in members if m.strip()}
        direct_reports = [
            r for r in direct_reports if str(r.get("name", "")).strip().casefold() in allow
        ]
    team_data = generator._collect_team_data(  # noqa: SLF001 — intentional CLI reuse
        direct_reports,
        days,
        subscriptions_override=team_subs_df,
    )
    prefetch_meta["data_retrieved_at"] = datetime.now(timezone.utc).isoformat()
    prefetch_meta["outcome"] = "success"

    payload = {
        "derived_from": "live_cisco_sources",
        "data_end": evaluation_as_of.isoformat(),
        "manager_name": manager,
        "technology": technology,
        "days": int(days),
        "team_data": _serialize_team_data(team_data),
        "export_meta": {
            "scope_type": scope_selection.scope_type,
            "scope_value": scope_selection.display_value,
            "prefetch_meta": prefetch_meta,
            "subscription_rows": int(len(team_subs_df)),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Export live leader portfolio for backtest")
    parser.add_argument("--manager", required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--technology", default="All")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-end", default="", help="Optional ISO anchor for evaluation_as_of")
    parser.add_argument(
        "--members",
        default="",
        help="Optional comma-separated CSSM names to cap export size",
    )
    args = parser.parse_args()
    members = [m.strip() for m in args.members.split(",") if m.strip()] or None
    export_live_portfolio(
        manager=args.manager,
        days=args.days,
        technology=args.technology,
        output=args.output,
        data_end=args.data_end or None,
        members=members,
    )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
