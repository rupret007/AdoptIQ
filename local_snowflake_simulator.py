"""Fail-closed Snowflake DB-API simulator for local source-contract testing.

This module is never imported by the production application or either build
spec.  It sits one layer below the existing local report adapters: production
fetch functions execute their real parameterized SQL against this deterministic
cursor, while the cursor returns sanitized fixture rows.  Unknown SQL raises
immediately so adding a new live query cannot silently receive plausible data.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from local_acceptance_lab import LocalAcceptanceBundle, SOURCE_MODE


DSM_TABLE = "CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA"
ACCOUNT_SUMMARY_TABLE = "CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY"
EXPIRED_ACCOUNTS_TABLE = "CX_DB.CX_SWSSBST_BR.ACCOUNTS_EXPIRED_LAST_MONTH"
CONTRACT_TABLE = "CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU"
RENEWAL_TABLE = "CX_DB.CX_SWSSBST_BR.RENEWAL_DATA"
TASK_TABLE = "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW"
PULSE_TABLE = "EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C"
PRIORITY_TABLE = "EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C"
SUPPORT_TABLE = "CX_DB.CX_SWSSBST_BR.SUPPORT_CASES"


class UnexpectedSimulatedQuery(RuntimeError):
    """A production fetcher issued SQL outside the declared local contract."""


class SimulatedSourceFailure(RuntimeError):
    """A declared local source failure interrupted one query family."""


@dataclass(frozen=True)
class QueryTrace:
    """Redacted SQL evidence; no query text or bound values are retained."""

    family: str
    table: str
    sql_sha256: str
    placeholder_count: int
    parameter_count: int
    parameterized: bool

    def redacted(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "table": self.table,
            "sql_sha256": self.sql_sha256,
            "placeholder_count": self.placeholder_count,
            "parameter_count": self.parameter_count,
            "parameterized": self.parameterized,
        }


def _clean_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return (
        frame.astype(object)
        .where(pd.notna(frame), None)
        .to_dict(orient="records")
    )


def _unique_columns(records: Iterable[Mapping[str, Any]]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for row in records:
        for raw in row:
            column = str(raw)
            if column not in seen:
                seen.add(column)
                columns.append(column)
    return columns


def _rows_for_columns(
    records: Iterable[Mapping[str, Any]],
    columns: Sequence[str],
) -> list[tuple[Any, ...]]:
    return [tuple(row.get(column) for column in columns) for row in records]


def _normal_sql(sql: object) -> str:
    return re.sub(r"\s+", " ", str(sql or "")).strip()


class FixtureSnowflakeConnection:
    """Small DB-API connection serving sanitized, query-shaped fixtures."""

    def __init__(
        self,
        bundle: LocalAcceptanceBundle,
        *,
        fail_families: Iterable[str] = (),
    ) -> None:
        bundle.assert_reconciled()
        self.bundle = bundle
        self.fail_families = {str(item).strip() for item in fail_families}
        self.closed = False
        self.traces: list[QueryTrace] = []
        self._tables = self._build_tables()
        self._account_ids = {
            str(row.get("ACCOUNT_ID_C") or "").strip()
            for row in self._tables[DSM_TABLE]
            if str(row.get("ACCOUNT_ID_C") or "").strip()
        }
        self._owner_emails = {
            str(row.get("OWNER_EMAIL") or "").strip().casefold()
            for row in bundle.records("ownership")
            if str(row.get("OWNER_EMAIL") or "").strip()
        }
        self._customer_names = {
            str(row.get("BU_NAME") or "").strip().casefold()
            for row in bundle.records("customers")
            if str(row.get("BU_NAME") or "").strip()
        }

    def _build_tables(self) -> dict[str, list[dict[str, Any]]]:
        ownership = self.bundle.records("ownership")
        email_by_member = {
            str(row.get("OWNER_NAME") or ""): str(row.get("OWNER_EMAIL") or "")
            for row in ownership
        }
        ordered_members = [str(row.get("OWNER_NAME") or "") for row in ownership]
        primary_member = ordered_members[0] if ordered_members else ""

        dsm: list[dict[str, Any]] = []
        for row in self.bundle.records("subscriptions"):
            member = str(row.get("FIXTURE_MEMBER") or "")
            email = email_by_member.get(member, "")
            dsm.append(
                {
                    **row,
                    "TECHNOLOGY_C": row.get("TECHNOLOGY_C")
                    or row.get("PRODUCT_NAME")
                    or "Unknown",
                    "SUB_TECHNOLOGY_C": row.get("SUB_TECHNOLOGY_C")
                    or row.get("PRODUCT_NAME")
                    or "Unknown",
                    "PRIMARY_DSM_EMAIL": email if member == primary_member else "",
                    "CSSM_EMAIL": email if member == primary_member else "",
                    # The second fixture owner is deliberately secondary-only.
                    # This makes real source-contract runs prove the DSM_EMAIL1
                    # fan-out instead of passing through the primary shortcut.
                    "DSM_EMAIL1": email if member != primary_member else "",
                    "DSM_EMAIL2": "",
                    "STATUS_C": "Active",
                }
            )

        task_rows: list[dict[str, Any]] = []
        for dataset, record_type in (
            ("action_plans", "0122T000000QHBGQA4"),
            ("adoption_barriers", "0122T000000GJfTQAW"),
        ):
            for row in self.bundle.records(dataset):
                member = str(row.get("FIXTURE_MEMBER") or "")
                task_rows.append(
                    {
                        **row,
                        "RECORD_TYPE_ID": record_type,
                        "record_type_id": record_type,
                        "CREATED_DATE": row.get("CREATED_DATE")
                        or row.get("CREATED_DATE_C")
                        or row.get("OPEN_DATE_C")
                        or self.bundle.as_of_utc,
                        "CREATEDBY_EMAIL": email_by_member.get(member, ""),
                        "OWNER_EMAIL": email_by_member.get(member, ""),
                    }
                )

        pulse: list[dict[str, Any]] = []
        for row in self.bundle.records("customer_pulse"):
            member = str(row.get("FIXTURE_MEMBER") or "")
            pulse.append(
                {
                    **row,
                    "CREATEDDATE": row.get("CREATEDDATE")
                    or row.get("PULSE_DATE_C")
                    or self.bundle.as_of_utc,
                    "CREATEDBY_EMAIL": email_by_member.get(member, ""),
                    "OWNER_EMAIL": email_by_member.get(member, ""),
                }
            )

        priorities = []
        for row in self.bundle.records("success_priorities"):
            priorities.append(
                {
                    **row,
                    "CREATEDDATE": row.get("CREATEDDATE") or self.bundle.as_of_utc,
                }
            )

        support = []
        for row in self.bundle.records("support_cases"):
            support.append(
                {
                    **row,
                    "ACCOUNT_ID": row.get("ACCOUNT_ID") or row.get("ACCOUNT_ID_C"),
                    "CREATED_DATE": row.get("CREATED_DATE") or row.get("DATE_OPENED"),
                    "CLOSED_DATE": row.get("CLOSED_DATE"),
                    "DESCRIPTION_C": row.get("DESCRIPTION_C") or row.get("DESCRIPTION"),
                }
            )

        customer_rows = self.bundle.records("customers")
        risk_cycle = ("High", "Medium", "Low")
        tier_cycle = ("Tier 1", "Tier 2", "Tier 3")
        account_summary = [
            {
                **row,
                "BU_ACCOUNT_NAME": row.get("BU_NAME"),
                "RENEWAL_RISK_CATEGORY": risk_cycle[index % len(risk_cycle)],
                "CONTRACT_STATUS": "Active",
                "CISCO_TIER_RANKING__C": tier_cycle[index % len(tier_cycle)],
                "ABC_CATEGORY__C": ("A", "B", "C")[index % 3],
            }
            for index, row in enumerate(customer_rows)
        ]

        probability_cycle = (58.0, 74.0, 92.0)
        renewal_status_cycle = ("At Risk", "In Review", "Likely")
        contracts: list[dict[str, Any]] = []
        renewals: list[dict[str, Any]] = []
        for index, row in enumerate(self.bundle.records("renewals")):
            contract_number = row.get("CONTRACT_NUMBER") or row.get("RENEWAL_ID")
            service_end = row.get("SERVICE_END_DATE") or row.get("RENEWAL_DATE")
            currency = row.get("CURRENCY_CODE") or row.get("CURRENCY") or "UNKNOWN"
            arr = row.get("ARR_AMOUNT")
            if arr is None:
                arr = row.get("ARR")
            contracts.append(
                {
                    **row,
                    "CONTRACT_NUMBER": contract_number,
                    "SERVICE_END_DATE": service_end,
                    "C_360_SERVICE_TIER_C": tier_cycle[index % len(tier_cycle)],
                    "ARR_AMOUNT": arr,
                    "CURRENCY_CODE": currency,
                }
            )
            renewals.append(
                {
                    **row,
                    "CONTRACT_NUMBER": contract_number,
                    "RENEWAL_STATUS": row.get("RENEWAL_STATUS")
                    or renewal_status_cycle[index % len(renewal_status_cycle)],
                    "RENEWAL_PROBABILITY": row.get("RENEWAL_PROBABILITY")
                    if row.get("RENEWAL_PROBABILITY") is not None
                    else probability_cycle[index % len(probability_cycle)],
                }
            )

        # A prior-contract record makes the real recently-expired query path
        # observable without claiming that the current renewal is expired.
        expired_accounts: list[dict[str, Any]] = []
        if customer_rows:
            first = customer_rows[0]
            expired_accounts.append(
                {
                    "ACCOUNT_ID_C": first.get("ACCOUNT_ID_C"),
                    "NAME": first.get("BU_NAME"),
                    "EXPIRED_DATE": "2026-07-15",
                    "RENEWAL_ACCOUNT": True,
                }
            )

        return {
            DSM_TABLE: dsm,
            ACCOUNT_SUMMARY_TABLE: account_summary,
            EXPIRED_ACCOUNTS_TABLE: expired_accounts,
            CONTRACT_TABLE: contracts,
            RENEWAL_TABLE: renewals,
            TASK_TABLE: task_rows,
            PULSE_TABLE: pulse,
            PRIORITY_TABLE: priorities,
            SUPPORT_TABLE: support,
        }

    def cursor(self, *_args: Any, **_kwargs: Any) -> "FixtureSnowflakeCursor":
        if self.closed:
            raise RuntimeError("simulated Snowflake connection is closed")
        return FixtureSnowflakeCursor(self)

    def close(self) -> None:
        self.closed = True

    def trace_summary(self) -> dict[str, Any]:
        family_counts = Counter(trace.family for trace in self.traces)
        return {
            "source_mode": SOURCE_MODE,
            "live_validation_performed": False,
            "query_count": len(self.traces),
            "families": dict(sorted(family_counts.items())),
            "all_data_queries_parameterized": all(
                trace.parameterized
                for trace in self.traces
                if trace.family not in {"schema_probe", "metadata_probe"}
            ),
            "query_fingerprints": sorted({trace.sql_sha256 for trace in self.traces}),
        }


class FixtureSnowflakeCursor:
    """Query-family dispatcher implementing the DB-API surface fetchers use."""

    def __init__(self, connection: FixtureSnowflakeConnection) -> None:
        self.connection = connection
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[Any, ...]] = []
        self._offset = 0
        self.closed = False

    def _classify(self, sql: str) -> tuple[str, str]:
        upper = sql.upper()
        if upper.startswith("DESC TABLE ") or upper.startswith("DESCRIBE TABLE "):
            return "metadata_probe", upper.rsplit(" ", 1)[-1].rstrip(";")
        if " LIMIT 1" in upper and upper.startswith("SELECT * FROM ") and " WHERE " not in upper:
            return "schema_probe", upper.split(" FROM ", 1)[1].split(" ", 1)[0]
        if DSM_TABLE in upper and "SELECT DISTINCT" in upper:
            return "team_subscriptions", DSM_TABLE
        if DSM_TABLE in upper and "UPPER(BU_NAME) LIKE UPPER(" in upper:
            return "customer_subscription_search", DSM_TABLE
        if ACCOUNT_SUMMARY_TABLE in upper:
            return "account_summary", ACCOUNT_SUMMARY_TABLE
        if CONTRACT_TABLE in upper and "COUNT(" in upper:
            return "contract_aggregate", CONTRACT_TABLE
        if CONTRACT_TABLE in upper:
            return "contracts", CONTRACT_TABLE
        if EXPIRED_ACCOUNTS_TABLE in upper:
            return "recently_expired", EXPIRED_ACCOUNTS_TABLE
        if RENEWAL_TABLE in upper and "COUNT(" in upper:
            return "renewal_aggregate", RENEWAL_TABLE
        if RENEWAL_TABLE in upper:
            return "renewals", RENEWAL_TABLE
        if TASK_TABLE in upper and "0122T000000QHBGQA4" in upper:
            return "action_plans", TASK_TABLE
        if TASK_TABLE in upper and "0122T000000GJFTQAW" in upper:
            return "adoption_barriers", TASK_TABLE
        if PULSE_TABLE in upper and "SUM(" not in upper and "COUNT(" not in upper:
            return "customer_pulse", PULSE_TABLE
        if PRIORITY_TABLE in upper:
            return "success_priorities", PRIORITY_TABLE
        if SUPPORT_TABLE in upper:
            return "support_cases", SUPPORT_TABLE
        raise UnexpectedSimulatedQuery("local Snowflake simulator rejected unknown SQL")

    def _trace(
        self,
        family: str,
        table: str,
        sql: str,
        params: Sequence[Any],
    ) -> None:
        placeholder_count = sql.count("%s")
        parameter_count = len(params)
        parameterized = placeholder_count > 0 and placeholder_count == parameter_count
        if family in {"schema_probe", "metadata_probe"}:
            parameterized = parameter_count == 0
        self.connection.traces.append(
            QueryTrace(
                family=family,
                table=table,
                sql_sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                placeholder_count=placeholder_count,
                parameter_count=parameter_count,
                parameterized=parameterized,
            )
        )
        if family not in {"schema_probe", "metadata_probe"} and not parameterized:
            raise UnexpectedSimulatedQuery(
                "local Snowflake simulator requires exact parameter binding"
            )

    def _selection_values(
        self,
        params: Sequence[Any],
    ) -> tuple[set[str], set[str], set[str]]:
        account_ids = {
            str(value).strip()
            for value in params
            if str(value).strip() in self.connection._account_ids
        }
        owner_emails = {
            str(value).strip().casefold()
            for value in params
            if str(value).strip().casefold() in self.connection._owner_emails
        }
        customer_names = {
            str(value).strip().casefold()
            for value in params
            if str(value).strip().casefold() in self.connection._customer_names
        }
        return account_ids, owner_emails, customer_names

    def execute(
        self,
        sql: object,
        params: Sequence[Any] | None = None,
    ) -> "FixtureSnowflakeCursor":
        if self.closed:
            raise RuntimeError("simulated Snowflake cursor is closed")
        normalized = _normal_sql(sql)
        bound = tuple(params or ())
        family, table = self._classify(normalized)
        self._trace(family, table, normalized, bound)
        if family in self.connection.fail_families:
            raise SimulatedSourceFailure(f"simulated {family} source failure")

        records = [dict(row) for row in self.connection._tables.get(table, [])]
        account_ids, owner_emails, customer_names = self._selection_values(bound)
        upper = normalized.upper()

        if family == "metadata_probe":
            columns = _unique_columns(records)
            self.description = [("name",), ("type",)]
            self._rows = [(column, "TEXT") for column in columns]
        elif family == "schema_probe":
            columns = _unique_columns(records)
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(records[:1], columns)
        elif family == "team_subscriptions":
            match = re.search(r"WHERE\s+([A-Z0-9_]+)\s+IN\s*\(", upper)
            if not match:
                raise UnexpectedSimulatedQuery(
                    "team subscription query omitted its owner column"
                )
            email_column = match.group(1)
            selected = [
                row
                for row in records
                if str(row.get(email_column) or "").strip().casefold() in owner_emails
            ]
            columns = [
                "SUBSCRIPTION_ID",
                "ACCOUNT_ID_C",
                "BU_NAME",
                "TECHNOLOGY_C",
                "SUB_TECHNOLOGY_C",
                "CSSM_EMAIL",
            ]
            projected = []
            seen: set[tuple[Any, ...]] = set()
            for row in selected:
                item = {
                    **row,
                    "CSSM_EMAIL": row.get(email_column),
                }
                key = tuple(item.get(column) for column in columns)
                if key not in seen:
                    seen.add(key)
                    projected.append(item)
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(projected, columns)
        elif family == "customer_subscription_search":
            needle = str(bound[0] if bound else "").strip().strip("%").casefold()
            selected = [
                row
                for row in records
                if not needle or needle in str(row.get("BU_NAME") or "").casefold()
            ]
            try:
                row_limit = max(1, int(bound[-1]))
            except (TypeError, ValueError):
                row_limit = 10
            selected = selected[:row_limit]
            # Return physical DSM columns exactly as the production query
            # requests them; the backend then performs the shared-owner
            # expansion into canonical CSSM_EMAIL rows.
            selected_columns = []
            select_clause = normalized.split(" FROM ", 1)[0].split("SELECT ", 1)[1]
            for expression in select_clause.split(","):
                token = expression.strip().split()[0].strip('"').upper()
                if token in _unique_columns(records) and token not in selected_columns:
                    selected_columns.append(token)
            self.description = [(column,) for column in selected_columns]
            self._rows = _rows_for_columns(selected, selected_columns)
        elif family in {"action_plans", "adoption_barriers"}:
            wanted_record_type = (
                "0122T000000QHBGQA4"
                if family == "action_plans"
                else "0122T000000GJfTQAW"
            ).casefold()
            records = [
                row
                for row in records
                if str(row.get("RECORD_TYPE_ID") or "").strip().casefold()
                == wanted_record_type
            ]
            if account_ids:
                records = [
                    row
                    for row in records
                    if str(row.get("ACCOUNT_ID_C") or "").strip() in account_ids
                ]
            elif owner_emails:
                records = [
                    row
                    for row in records
                    if str(row.get("OWNER_EMAIL") or "").strip().casefold()
                    in owner_emails
                ]
            columns = _unique_columns(records)
            if "RECORD_SOURCE" not in columns:
                columns.append("RECORD_SOURCE")
            label = "Action Plan" if family == "action_plans" else "Adoption Barrier"
            projected = [{**row, "RECORD_SOURCE": label} for row in records]
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(projected, columns)
        elif family == "customer_pulse":
            if account_ids:
                records = [
                    row
                    for row in records
                    if str(row.get("ACCOUNT__C") or "").strip() in account_ids
                ]
            elif owner_emails:
                records = [
                    row
                    for row in records
                    if str(row.get("OWNER_EMAIL") or "").strip().casefold()
                    in owner_emails
                ]
            columns = _unique_columns(records)
            if "RECORD_SOURCE" not in columns:
                columns.append("RECORD_SOURCE")
            projected = [{**row, "RECORD_SOURCE": "Customer Pulse"} for row in records]
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(projected, columns)
        elif family == "success_priorities":
            records = [
                row
                for row in records
                if not customer_names
                or str(row.get("RELATED_CUSTOMER__C") or "").strip().casefold()
                in customer_names
            ]
            columns = _unique_columns(records)
            if "RECORD_SOURCE" not in columns:
                columns.append("RECORD_SOURCE")
            projected = [
                {**row, "RECORD_SOURCE": "Success Priority"} for row in records
            ]
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(projected, columns)
        elif family == "account_summary":
            if account_ids:
                records = [
                    row
                    for row in records
                    if str(row.get("ACCOUNT_ID_C") or "").strip() in account_ids
                ]
            columns = [
                "ACCOUNT_ID_C",
                "BU_ACCOUNT_NAME",
                "RENEWAL_RISK_CATEGORY",
                "CONTRACT_STATUS",
                "CISCO_TIER_RANKING__C",
                "ABC_CATEGORY__C",
            ]
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(records, columns)
        elif family == "contract_aggregate":
            if account_ids:
                records = [
                    row
                    for row in records
                    if str(row.get("ACCOUNT_ID_C") or "").strip() in account_ids
                ]
            cutoff = str(bound[0])[:10] if bound else "9999-12-31"
            today = str(bound[-1])[:10] if bound else "0000-01-01"
            active = [
                row
                for row in records
                if str(row.get("SERVICE_END_DATE") or "")[:10] >= today
            ]
            by_currency: dict[str, list[dict[str, Any]]] = {}
            for row in active:
                currency = str(row.get("CURRENCY_CODE") or "UNKNOWN")
                by_currency.setdefault(currency, []).append(row)
            self.description = [
                ("CCY",),
                ("TOTAL_ACTIVE",),
                ("EXPIRING_COUNT",),
                ("EXPIRING_ARR",),
            ]
            self._rows = []
            for currency, rows in sorted(by_currency.items()):
                expiring = [
                    row
                    for row in rows
                    if str(row.get("SERVICE_END_DATE") or "")[:10] <= cutoff
                ]
                self._rows.append(
                    (
                        currency,
                        len(rows),
                        len(expiring),
                        sum(float(row.get("ARR_AMOUNT") or 0) for row in expiring),
                    )
                )
        elif family == "contracts":
            if account_ids:
                records = [
                    row
                    for row in records
                    if str(row.get("ACCOUNT_ID_C") or "").strip() in account_ids
                ]
            today = str(bound[-1])[:10] if bound else "0000-01-01"
            records = [
                row
                for row in records
                if str(row.get("SERVICE_END_DATE") or "")[:10] >= today
            ]
            records.sort(key=lambda row: str(row.get("SERVICE_END_DATE") or ""))
            columns = [
                "CONTRACT_NUMBER",
                "SERVICE_END_DATE",
                "C_360_SERVICE_TIER_C",
                "ARR_AMOUNT",
                "ACCOUNT_ID_C",
                "CURRENCY_CODE",
            ]
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(records, columns)
        elif family == "recently_expired":
            if account_ids:
                records = [
                    row
                    for row in records
                    if str(row.get("ACCOUNT_ID_C") or "").strip() in account_ids
                ]
            columns = ["NAME", "EXPIRED_DATE", "RENEWAL_ACCOUNT"]
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(records, columns)
        elif family == "renewal_aggregate":
            if account_ids:
                records = [
                    row
                    for row in records
                    if str(row.get("ACCOUNT_ID_C") or "").strip() in account_ids
                ]
            probabilities = [
                float(row["RENEWAL_PROBABILITY"])
                for row in records
                if row.get("RENEWAL_PROBABILITY") is not None
            ]
            self.description = [
                ("TOTAL",),
                ("AVG_PROB",),
                ("MIN_PROB",),
                ("AT_RISK",),
            ]
            self._rows = [
                (
                    len(records),
                    sum(probabilities) / len(probabilities) if probabilities else None,
                    min(probabilities) if probabilities else None,
                    sum(value < 70 for value in probabilities),
                )
            ]
        elif family == "renewals":
            if account_ids:
                records = [
                    row
                    for row in records
                    if str(row.get("ACCOUNT_ID_C") or "").strip() in account_ids
                ]
            records.sort(
                key=lambda row: (
                    float(row.get("RENEWAL_PROBABILITY") or 101),
                    str(row.get("ACCOUNT_ID_C") or ""),
                    str(row.get("CONTRACT_NUMBER") or ""),
                )
            )
            columns = [
                "CONTRACT_NUMBER",
                "ACCOUNT_ID_C",
                "RENEWAL_STATUS",
                "RENEWAL_PROBABILITY",
            ]
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(records, columns)
        elif family == "support_cases":
            records = [
                row
                for row in records
                if not account_ids
                or str(row.get("ACCOUNT_ID") or row.get("ACCOUNT_ID_C") or "").strip()
                in account_ids
            ]
            columns = [
                "CASE_ID",
                "ACCOUNT_ID",
                "SUBJECT",
                "STATUS",
                "CREATED_DATE",
                "CLOSED_DATE",
                "SEVERITY",
                "DESCRIPTION",
                "DESCRIPTION_C",
            ]
            self.description = [(column,) for column in columns]
            self._rows = _rows_for_columns(records, columns)
        else:  # pragma: no cover - family classification is exhaustive
            raise UnexpectedSimulatedQuery("unhandled local query family")

        self._offset = 0
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows = self._rows[self._offset :]
        self._offset = len(self._rows)
        return list(rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def close(self) -> None:
        self.closed = True
