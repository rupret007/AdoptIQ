#!/usr/bin/env python3
"""Metadata-only Snowflake capability audit for report decision coverage.

Local mode describes the fail-closed fixture connection.  Live mode is an
explicit, separately authorized work-machine operation and issues only
``DESCRIBE TABLE`` statements against the existing allowlist.  No row values,
customer identifiers, SQL text, credentials, or raw provider errors are ever
written to the summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_acceptance_lab import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    SOURCE_MODE,
    assert_safe_activation,
    build_scenario_bundle,
)
from local_snowflake_simulator import FixtureSnowflakeConnection  # noqa: E402
from snowflake_table_policy import guard_table  # noqa: E402


ALLOWED_CAPABILITY_TABLES = (
    "CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA",
    "CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY",
    "CX_DB.CX_SWSSBST_BR.ACCOUNTS_EXPIRED_LAST_MONTH",
    "CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU",
    "CX_DB.CX_SWSSBST_BR.RENEWAL_DATA",
    "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW",
    "EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C",
)

POLICY_BLOCKED_TABLES = (
    "CX_DB.CX_SWSSBST_BR.SUPPORT_CASES",
    "CX_DB.CX_SWSSBST_BR.USER_DATA",
    "CX_DB.CX_SWSSBST_BR.PRODUCT_USAGE",
    "EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C",
    "EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C",
)

# These are fields already mapped into canonical report decisions, not merely
# fields retrieved by SELECT *.  New live columns outside these sets are audit
# candidates; they do not enter reports until their meaning and authorization
# are reviewed and tested.
DECISION_MAPPED_FIELDS: Mapping[str, frozenset[str]] = {
    "DSM_ASSIGNMENT_DATA": frozenset(
        {
            "SUBSCRIPTION_ID",
            "ACCOUNT_ID_C",
            "BU_NAME",
            "TECHNOLOGY_C",
            "SUB_TECHNOLOGY_C",
            "PRIMARY_DSM_EMAIL",
            "CSSM_EMAIL",
            "DSM_EMAIL1",
            "DSM_EMAIL2",
            "STATUS_C",
            # Leader technology summaries already consume this optional
            # assignment-view label when it is present.
            "PRODUCT_NAME",
        }
    ),
    "COLLAB_ACCOUNT_SUMMARY": frozenset(
        {
            "ACCOUNT_ID_C",
            "BU_ACCOUNT_NAME",
            "RENEWAL_RISK_CATEGORY",
            "CONTRACT_STATUS",
            "CISCO_TIER_RANKING__C",
            "ABC_CATEGORY__C",
        }
    ),
    "ACCOUNTS_EXPIRED_LAST_MONTH": frozenset(
        {"ACCOUNT_ID_C", "NAME", "EXPIRED_DATE", "RENEWAL_ACCOUNT"}
    ),
    "COLLAB_ARR_CON_SKU": frozenset(
        {
            "CONTRACT_NUMBER",
            "SERVICE_END_DATE",
            "C_360_SERVICE_TIER_C",
            "ARR_AMOUNT",
            "ACCOUNT_ID_C",
            "CURRENCY_CODE",
        }
    ),
    "RENEWAL_DATA": frozenset(
        {
            "CONTRACT_NUMBER",
            "ACCOUNT_ID_C",
            "RENEWAL_STATUS",
            "RENEWAL_PROBABILITY",
        }
    ),
    "C360_CS_TASK_C_VW": frozenset(
        {
            "ID",
            "ACCOUNT_ID_C",
            "RELATED_CUSTOMER_C",
            "SUBJECT_C",
            "STATUS_C",
            "PRIORITY_C",
            "DUE_DATE_C",
            "OPEN_DATE_C",
            "CLOSED_DATE_C",
            "CREATED_DATE",
            "OWNER_EMAIL",
            "CREATEDBY_EMAIL",
            "NEXT_ACTION_OWNER_C",
            "NEXT_ACTION_C",
            "DESCRIPTION_C",
            "SEVERITY_C",
            "CREATED_DATE_C",
            "RECORD_TYPE_ID",
            "TECHNOLOGY_C",
        }
    ),
    "ESA_C360_CUSTOMER_PULSE__C": frozenset(
        {
            "ID",
            "ACCOUNT__C",
            "BU_NAME",
            "CUSTOMER_PULSE__C",
            "PULSE_RATING__C",
            "SCORE__C",
            "REASON__C",
            "COMMENTS__C",
            "PULSE_DATE_C",
            "CREATEDDATE",
            "OWNER_EMAIL",
            "CREATEDBY_EMAIL",
            "TECHNOLOGY_C",
        }
    ),
}

OPPORTUNITY_PATTERNS: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "scope_and_attribution": (
        "Manager/team/customer scope and ownership",
        ("ACCOUNT", "CUSTOMER", "DSM", "OWNER", "MANAGER", "SUBSCRIPTION", "BU_NAME"),
    ),
    "technology_segmentation": (
        "Technology/product segmentation without scope widening",
        ("TECHNOLOGY", "PRODUCT", "SKU", "OFFER"),
    ),
    "renewal_timing": (
        "Upcoming, recently expired, and overdue renewal timing",
        ("RENEWAL", "SERVICE_END", "EXPIRED_DATE", "END_DATE"),
    ),
    "commercial_value": (
        "ARR and contract exposure with currency-safe totals",
        ("ARR", "CURRENCY", "CONTRACT", "TIER", "ABC_CATEGORY"),
    ),
    "risk_and_health": (
        "Source-provided risk, probability, status, and health signals",
        ("RISK", "PROBABILITY", "STATUS", "HEALTH", "SCORE"),
    ),
    "action_execution": (
        "Action ownership, due dates, blockers, and lifecycle execution",
        ("ACTION", "DUE", "PRIORITY", "BLOCK", "CREATED", "COMPLETED", "SUBJECT"),
    ),
    "customer_sentiment": (
        "Customer pulse, sentiment, reason, and supporting commentary",
        ("PULSE", "SENTIMENT", "REASON", "COMMENT"),
    ),
    "record_traceability": (
        "Stable record identifiers for evidence and source links",
        ("ID", "NUMBER", "URL", "LINK"),
    ),
}

_SAFE_COLUMN = re.compile(r"^[A-Z][A-Z0-9_$]{0,127}$")
_FIXTURE_ONLY_PREFIXES = ("FIXTURE_", "LOCAL_ACCEPTANCE_")


def _basename(table: str) -> str:
    return str(table).rsplit(".", 1)[-1].upper()


def _classify_error(exc: BaseException) -> str:
    text = str(exc).casefold()
    if any(token in text for token in ("not authorized", "insufficient privilege", "access denied")):
        return "permission_denied"
    if any(token in text for token in ("does not exist", "not found", "unknown table")):
        return "not_found"
    if any(token in text for token in ("timeout", "network", "connection", "dns")):
        return "connection_unavailable"
    return "metadata_probe_failed"


def _describe_table(connection: Any, table: str) -> tuple[list[str], dict[str, str]]:
    guard_table(table)
    cursor = connection.cursor()
    try:
        cursor.execute(f"DESC TABLE {table}")
        rows = cursor.fetchall() or []
        description = [str(item[0] or "").strip().casefold() for item in cursor.description or []]
        name_index = description.index("name") if "name" in description else 0
        type_index = description.index("type") if "type" in description else 1
        types: dict[str, str] = {}
        for row in rows:
            if len(row) <= name_index:
                continue
            name = str(row[name_index] or "").strip().upper()
            if not _SAFE_COLUMN.fullmatch(name) or name.startswith(_FIXTURE_ONLY_PREFIXES):
                continue
            raw_type = str(row[type_index] or "").strip().upper() if len(row) > type_index else ""
            # Keep only a generic type family; precision/defaults/comments are
            # unnecessary for the capability audit and can reveal metadata we
            # do not need.
            type_family = re.split(r"[<( ]", raw_type, maxsplit=1)[0][:32]
            types[name] = type_family
        return sorted(types), types
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def _opportunity_projection(
    table: str,
    columns: Iterable[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized = sorted(set(columns))
    mapped = DECISION_MAPPED_FIELDS.get(_basename(table), frozenset())
    opportunities: list[dict[str, Any]] = []
    expansion_fields: set[str] = set()
    for capability, (decision_use, tokens) in OPPORTUNITY_PATTERNS.items():
        matches = sorted(
            column
            for column in normalized
            if any(token in column for token in tokens)
        )
        if not matches:
            continue
        mapped_matches = sorted(set(matches) & set(mapped))
        new_matches = sorted(set(matches) - set(mapped))
        expansion_fields.update(new_matches)
        opportunities.append(
            {
                "capability": capability,
                "decision_use": decision_use,
                "matched_columns": matches,
                "decision_mapped_columns": mapped_matches,
                "expansion_candidate_columns": new_matches,
                "coverage_state": "expansion_candidate" if new_matches else "implemented",
            }
        )
    return opportunities, sorted(expansion_fields)


def profile_connection(connection: Any, *, mode: str) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    for table in ALLOWED_CAPABILITY_TABLES:
        try:
            columns, types = _describe_table(connection, table)
            opportunities, expansion = _opportunity_projection(table, columns)
            tables.append(
                {
                    "table": table,
                    "access_state": "simulated_available" if mode == "local" else "live_accessible",
                    "column_count": len(columns),
                    "schema_sha256": hashlib.sha256("\n".join(columns).encode("utf-8")).hexdigest(),
                    "column_type_families": dict(sorted(types.items())),
                    "decision_mapped_columns": sorted(
                        set(columns) & set(DECISION_MAPPED_FIELDS.get(_basename(table), frozenset()))
                    ),
                    "expansion_candidate_columns": expansion,
                    "decision_opportunities": opportunities,
                }
            )
        except Exception as exc:  # noqa: BLE001 - sanitized classification only
            tables.append(
                {
                    "table": table,
                    "access_state": _classify_error(exc),
                    "column_count": 0,
                    "schema_sha256": "",
                    "column_type_families": {},
                    "decision_mapped_columns": [],
                    "expansion_candidate_columns": [],
                    "decision_opportunities": [],
                }
            )

    accessible = sum(
        row["access_state"] in {"simulated_available", "live_accessible"}
        for row in tables
    )
    return {
        "schema_version": "snowflake-capability-profile/v1",
        "sanitized": True,
        "do_not_commit": True,
        "mode": mode,
        "source_mode": SOURCE_MODE if mode == "local" else "live_metadata_only",
        "live_validation_attempted": mode == "live",
        "live_validation_performed": mode == "live" and accessible > 0,
        "production_accuracy_claimed": False,
        "row_values_queried": False,
        "allowed_table_count": len(ALLOWED_CAPABILITY_TABLES),
        "accessible_table_count": accessible,
        "all_allowed_tables_accessible": accessible == len(ALLOWED_CAPABILITY_TABLES),
        "tables": tables,
        "policy_blocked_tables": [
            {"table": table, "probe_attempted": False, "state": "blocked_by_policy"}
            for table in POLICY_BLOCKED_TABLES
        ],
        "guardrails": [
            "Metadata only; no source rows or values were queried.",
            "Candidate fields are not authorized for report use by discovery alone.",
            "Live semantic validation and canonical parity remain required before release.",
        ],
        "all_passed": accessible == len(ALLOWED_CAPABILITY_TABLES),
    }


def _write_summary(path: Path, payload: Mapping[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    if REPO_ROOT == resolved or REPO_ROOT in resolved.parents:
        relative = resolved.relative_to(REPO_ROOT)
        if not str(relative).startswith(".adoptiq-acceptance/"):
            raise ValueError("capability summaries inside the repo must use .adoptiq-acceptance")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, resolved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit Snowflake report capabilities without reading rows.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enable-local-fixtures", action="store_true")
    mode.add_argument("--live-metadata", action="store_true")
    parser.add_argument("--confirm-authorized-live-metadata", action="store_true")
    parser.add_argument("--scenario", default="multi_manager")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--summary",
        type=Path,
        default=REPO_ROOT / ".adoptiq-acceptance" / "snowflake-capabilities" / "summary.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection: Any = None
    if args.live_metadata:
        if not args.confirm_authorized_live_metadata:
            raise RuntimeError("live metadata mode requires explicit authorization confirmation")
        import adoptiq_backend as backend  # noqa: PLC0415

        connection = backend._connect_with_keeper()  # noqa: SLF001 - explicit diagnostic
        if connection is None:
            payload = {
                "schema_version": "snowflake-capability-profile/v1",
                "sanitized": True,
                "do_not_commit": True,
                "mode": "live",
                "live_validation_attempted": True,
                "live_validation_performed": False,
                "production_accuracy_claimed": False,
                "row_values_queried": False,
                "all_passed": False,
                "error_kind": "connection_unavailable",
            }
        else:
            payload = profile_connection(connection, mode="live")
    else:
        assert_safe_activation(explicit=True, host="127.0.0.1")
        bundle = build_scenario_bundle(str(args.scenario), Path(args.manifest))
        connection = FixtureSnowflakeConnection(bundle)
        payload = profile_connection(connection, mode="local")

    try:
        _write_summary(Path(args.summary), payload)
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    print(
        json.dumps(
            {
                "all_passed": bool(payload.get("all_passed")),
                "accessible_table_count": int(payload.get("accessible_table_count") or 0),
                "row_values_queried": bool(payload.get("row_values_queried")),
                "summary": str(Path(args.summary).expanduser().resolve()),
            },
            sort_keys=True,
        )
    )
    return 0 if payload.get("all_passed") else 5


if __name__ == "__main__":
    raise SystemExit(main())
