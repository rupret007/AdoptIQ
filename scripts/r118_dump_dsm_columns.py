#!/usr/bin/env python3
# Round 118 / Build 87: VPN-gated DSM column-discovery diagnostic.
#
# Why this exists: the live ACC Comprehensive customer count regressed to
# 24 (Build 86) because get_subscriptions_for_team fetched ONLY the primary
# DSM email column (PRIMARY_DSM_EMAIL, 32 rows) and zero R82 secondary
# candidates matched -- the live table's real secondary-owner column is not
# in adoptiq_backend._R82_SECONDARY_DSM_EMAIL_CANDIDATES. The /api/diag/dsm-columns
# endpoint surfaces the same data but requires a CSRF token (production has
# WTF_CSRF_ENABLED=True), so a raw browser GET returns 403. This script calls
# the SAME introspect_dsm_columns helper directly, loading credentials the way
# the app does, so an operator on VPN can dump the live schema with one command:
#
#     python3 scripts/r118_dump_dsm_columns.py
#
# It prints a JSON payload. Paste it back so the secondary owner column(s)
# can be pinned into _R82_SECONDARY_DSM_EMAIL_CANDIDATES. The key fields are
# "all_email_like_columns" (email-style secondary candidates) and the full
# "columns" inventory (which also reveals NON-email owner columns like the
# R88/F4 BUYING_CIRCLE_DM2_C shape, if the live table attributes secondary
# owners by name/ID rather than email).
#
# No PII is printed -- only schema metadata. The script never writes anything.
import json
import os
import sys

# Runtime credentials must be available BEFORE adoptiq_backend/config is
# imported because config.py snapshots SNOWFLAKE_CONFIG / KEEPER_CONFIG at
# import time. Use the process environment or the per-user AdoptIQ .env.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from dotenv import load_dotenv

    if sys.platform == "win32":
        _runtime_env = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), "AdoptIQ", ".env"
        )
    elif sys.platform == "darwin":
        _runtime_env = os.path.expanduser(
            "~/Library/Application Support/AdoptIQ/.env"
        )
    else:
        _runtime_env = os.path.expanduser("~/.adoptiq/.env")
    load_dotenv(_runtime_env, override=False)
except Exception:
    pass


def main() -> int:
    try:
        from adoptiq_backend import _connect_with_keeper, introspect_dsm_columns
    except Exception as exc:  # pragma: no cover - import-time environment issue
        print(
            json.dumps(
                {"ok": False, "error": "import_failed", "detail": str(exc)},
                indent=2,
            )
        )
        return 2

    ctx = None
    try:
        ctx = _connect_with_keeper()
    except Exception as exc:
        # _connect_with_keeper already redacts driver detail; surface the
        # public message so the operator knows whether VPN/creds are the issue.
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "snowflake_connect_failed",
                    "detail": str(exc),
                    "hint": "Confirm VPN is connected and the process environment or per-user AdoptIQ .env carries SNOWFLAKE_* or KEEPER_* values.",
                },
                indent=2,
            )
        )
        return 3

    try:
        payload = introspect_dsm_columns(ctx)
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("ok") else 4
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
