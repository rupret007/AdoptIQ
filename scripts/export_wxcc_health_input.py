#!/usr/bin/env python3
"""Round 134: CLI for WxCC health-check plain-text export."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _ensure_repo_root_on_path() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def _resolve_csone_path(explicit: str | None) -> tuple[str | None, str | None]:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            return None, f"path_not_found:{path.name}"
        return str(path), "provided"
    try:
        from app_simple import get_latest_csone_from_folder_diag

        path, sync_status, _count = get_latest_csone_from_folder_diag()
        if not path:
            return None, sync_status or "no_onedrive_sync"
        return path, sync_status
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__


def main(argv: list[str] | None = None) -> int:
    _ensure_repo_root_on_path()

    parser = argparse.ArgumentParser(
        description="Export a deterministic WxCC health-check input file for one customer.",
    )
    parser.add_argument("--customer", help="Customer name (BU_NAME search)")
    parser.add_argument("--subscription-id", help="Snowflake subscription id")
    parser.add_argument(
        "--technology",
        default="Webex Contact Center",
        help="Technology scope (wxcc alias supported)",
    )
    parser.add_argument("--days", type=int, default=90, help="Analysis window in days (1-365)")
    parser.add_argument("--output", help="Output .txt path (required unless --dry-run)")
    parser.add_argument("--csone-path", help="Optional explicit CSOne .xlsx path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print export to stdout instead of writing a file",
    )
    args = parser.parse_args(argv)

    if not args.customer and not args.subscription_id:
        print("error: --customer or --subscription-id is required", file=sys.stderr)
        return 2
    if not args.dry_run and not args.output:
        print("error: --output is required unless --dry-run", file=sys.stderr)
        return 2

    from wxcc_health_input_exporter import WxccExportError, export_wxcc_health_input

    csone_path, csone_sync = _resolve_csone_path(args.csone_path)
    output_path = None if args.dry_run else args.output

    try:
        result = export_wxcc_health_input(
            customer=args.customer,
            subscription_id=args.subscription_id,
            technology=args.technology,
            days=args.days,
            output_path=output_path,
            csone_path=csone_path,
            csone_sync_status=csone_sync,
        )
    except WxccExportError as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        if exc.code in ("validation",):
            return 2
        if exc.code == "customer_not_found":
            return 3
        if exc.code == "snowflake_connect_failed":
            return 4
        if exc.code == "empty_export":
            return 5
        return 1

    if args.dry_run:
        sys.stdout.write(result.text)
    else:
        print(f"Wrote {result.output_path} ({len(result.text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
