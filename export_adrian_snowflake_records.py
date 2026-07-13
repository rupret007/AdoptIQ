#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import math
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import snowflake.connector
from dotenv import load_dotenv

if sys.platform == "win32":
    load_dotenv(Path(os.environ.get("APPDATA", str(Path.home()))) / "AdoptIQ" / ".env")
elif sys.platform == "darwin":
    load_dotenv(Path.home() / "Library" / "Application Support" / "AdoptIQ" / ".env")
else:
    load_dotenv(Path.home() / ".adoptiq" / ".env")

from adoptiq_backend import DSM_TABLE, _column_or_default_expr, _connect_with_keeper, _get_table_columns
from config import Config


LOGGER = logging.getLogger("adrian_export")

TASK_TABLE = "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW"
CUSTOMER_PULSE_TABLE = "EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C"
ADOPTION_BARRIER_RECORD_TYPE = "0122T000000GJfTQAW"
ACTION_PLAN_RECORD_TYPE = "0122T000000QHBGQA4"

TASK_RECORD_URL_TEMPLATE = "https://ciscosales.lightning.force.com/lightning/r/C360_CS_Task__c/{record_id}/view"
CUSTOMER_PULSE_URL_TEMPLATE = "https://ciscosales.lightning.force.com/lightning/r/ESA_C360_CUSTOMER_PULSE__c/{record_id}/view"
CUSTOMER_PULSE_LINK_STATUS = "inferred_from_object_name_not_app_validated"
TASK_LINK_STATUS = "validated_existing_app_pattern"

TARGET_EMAIL = "adrdeleo@cisco.com"
TARGET_NAME = "Adrian De Leon"

OWNER_ROLE_PATTERNS: list[tuple[str, str]] = [
    ("OWNER", "owner"),
    ("ASSIGNEE", "assignee"),
    ("CREATEDBY", "creator"),
    ("CREATED_BY", "creator"),
    ("LASTMODIFIEDBY", "last_modifier"),
    ("LAST_MODIFIED_BY", "last_modifier"),
    ("MODIFIEDBY", "last_modifier"),
    ("MODIFIED_BY", "last_modifier"),
    ("CSSM", "cssm"),
    ("CSM", "csm"),
]

NORMALIZED_EXPORT_COLUMNS = [
    "entity_type",
    "source_table",
    "record_id",
    "record_link",
    "record_link_status",
    "matched_column",
    "matched_value",
    "match_type",
    "account_id",
    "customer_name",
    "bu_name",
    "subject_or_title",
    "status",
    "severity_or_rating",
    "description_or_comments",
    "created_date",
    "open_date",
    "last_modified_date",
    "owner_email",
    "assignee_email",
    "assignee_name",
    "owner_name",
    "cssm_email",
    "cssm_name",
    "cssm_manager",
]


def _quote_identifier(identifier: str) -> str:
    return '"' + str(identifier or "").replace('"', '""') + '"'


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalize_alnum(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_text(value))


def _looks_owner_like(column_name: str) -> tuple[bool, str]:
    upper_name = str(column_name or "").upper()
    for pattern, role in OWNER_ROLE_PATTERNS:
        if pattern in upper_name:
            return True, role
    return False, ""


def _owner_candidate_columns(columns: Iterable[str]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for column in sorted(set(columns)):
        include, role = _looks_owner_like(column)
        if include:
            candidates.append(
                {
                    "column_name": column,
                    "role_guess": role,
                }
            )
    return candidates


def _column_supports_id_match(column_name: str) -> bool:
    upper_name = str(column_name or "").upper()
    return (
        upper_name.endswith("ID")
        or upper_name.endswith("_ID")
        or upper_name.endswith("PROFILE_C")
        or upper_name in {"PLAN_OWNER_C", "OWNERID", "CREATEDBYID", "LASTMODIFIEDBYID"}
    )


def _build_owner_match_clause(
    candidate_columns: list[dict[str, str]],
    email: str,
    name: str,
    known_user_ids: Iterable[str] | None = None,
) -> tuple[str, list[Any]]:
    normalized_email = _normalize_text(email)
    normalized_name = _normalize_alnum(name)
    name_tokens = _normalize_text(name).split()
    reversed_name = _normalize_alnum(" ".join(reversed(name_tokens))) if name_tokens else normalized_name
    normalized_user_ids = sorted({str(value).strip().upper() for value in (known_user_ids or []) if str(value or "").strip()})

    clauses: list[str] = []
    params: list[Any] = []
    for meta in candidate_columns:
        column = meta["column_name"]
        ident = _quote_identifier(column)
        if normalized_user_ids and _column_supports_id_match(column):
            placeholders = ", ".join(["%s"] * len(normalized_user_ids))
            clauses.append(f"UPPER(TO_VARCHAR({ident})) IN ({placeholders})")
            params.extend(normalized_user_ids)
        else:
            clauses.append(
                f"(LOWER(TO_VARCHAR({ident})) LIKE %s "
                f"OR REGEXP_REPLACE(LOWER(TO_VARCHAR({ident})), '[^a-z0-9]+', '') IN (%s, %s))"
            )
            params.extend([f"%{normalized_email}%", normalized_name, reversed_name])
    if not clauses:
        return "1=0", []
    return " OR ".join(clauses), params


def _query_dataframe(ctx, sql: str, params: list[Any] | tuple[Any, ...]) -> pd.DataFrame:
    cursor = None
    try:
        cursor = ctx.cursor(snowflake.connector.DictCursor)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)
    finally:
        if cursor is not None:
            cursor.close()


def _schema_columns_for_table(ctx, table_name: str) -> list[str]:
    return sorted(_get_table_columns(ctx, table_name))


def _match_details_for_row(
    row: pd.Series,
    candidate_columns: list[dict[str, str]],
    email: str,
    name: str,
    known_user_ids: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    email_norm = _normalize_text(email)
    target_name_norm = _normalize_alnum(name)
    reverse_name_norm = _normalize_alnum(" ".join(reversed(_normalize_text(name).split())))
    known_user_ids_upper = {str(value).strip().upper() for value in (known_user_ids or []) if str(value or "").strip()}
    matches: list[dict[str, str]] = []

    for meta in candidate_columns:
        column = meta["column_name"]
        if column not in row.index:
            continue
        raw_value = row.get(column)
        if raw_value is None or (isinstance(raw_value, float) and math.isnan(raw_value)):
            continue

        raw_text = str(raw_value).strip()
        lowered = raw_text.lower()
        alnum = _normalize_alnum(raw_text)
        match_reason = ""
        if known_user_ids_upper and raw_text.upper() in known_user_ids_upper:
            match_reason = "user_id_inferred_from_task_records"
        elif email_norm and email_norm in lowered:
            match_reason = "email_contains"
        elif alnum and alnum in {target_name_norm, reverse_name_norm}:
            match_reason = "name_normalized_exact"

        if match_reason:
            matches.append(
                {
                    "column_name": column,
                    "matched_value": raw_text,
                    "match_type": f'{meta["role_guess"]}:{match_reason}',
                }
            )
    return matches


def _first_present(row: pd.Series, columns: Iterable[str], default: Any = "") -> Any:
    for column in columns:
        if column in row.index:
            value = row.get(column)
            if value is None:
                continue
            if isinstance(value, float) and math.isnan(value):
                continue
            if str(value).strip() == "":
                continue
            return value
    return default


def _coerce_excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    safe_df = df.copy()
    for col in safe_df.columns:
        series = safe_df[col]
        try:
            if isinstance(series.dtype, pd.DatetimeTZDtype):
                safe_df[col] = series.dt.tz_localize(None)
                continue
        except Exception:
            pass
        if series.dtype == object:
            try:
                safe_df[col] = series.apply(
                    lambda v: v.replace(tzinfo=None)
                    if isinstance(v, datetime) and v.tzinfo is not None
                    else v
                )
            except Exception:
                continue
    safe_df = safe_df.replace([np.inf, -np.inf], np.nan)
    for col in safe_df.columns:
        if safe_df[col].dtype == object:
            safe_df[col] = safe_df[col].apply(
                lambda v: "'" + v if isinstance(v, str) and v and v[0] in ("=", "+", "-", "@") else v
            )
    return safe_df


def _apply_match_metadata(
    df: pd.DataFrame,
    entity_type: str,
    source_table: str,
    candidate_columns: list[dict[str, str]],
    link_builder,
    link_status: str,
    known_user_ids: Iterable[str] | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    working = df.copy()
    matched_columns: list[str] = []
    matched_values: list[str] = []
    match_types: list[str] = []
    for _, row in working.iterrows():
        matches = _match_details_for_row(row, candidate_columns, TARGET_EMAIL, TARGET_NAME, known_user_ids=known_user_ids)
        matched_columns.append("; ".join(match["column_name"] for match in matches))
        matched_values.append("; ".join(match["matched_value"] for match in matches))
        match_types.append("; ".join(match["match_type"] for match in matches))

    working["entity_type"] = entity_type
    working["source_table"] = source_table
    working["record_id"] = working.apply(lambda row: _first_present(row, ["ID", "AP_ID", "RECORD_ID"], ""), axis=1)
    working["record_link"] = working["record_id"].apply(link_builder)
    working["record_link_status"] = link_status
    working["matched_column"] = matched_columns
    working["matched_value"] = matched_values
    working["match_type"] = match_types
    working["account_id"] = working.apply(lambda row: _first_present(row, ["ACCOUNT_ID_C", "ACCOUNT__C"], ""), axis=1)
    working["customer_name"] = working.apply(
        lambda row: _first_present(
            row,
            [
                "BU_NAME",
                "CUSTOMER_NAME__C",
                "CUSTOMER_BU_NAME__C",
                "RELATED_CUSTOMER__C",
                "ACCOUNT_NAME_C",
                "ACCOUNT_NAME",
                "DSM_BU_NAME",
            ],
            "",
        ),
        axis=1,
    )
    working["bu_name"] = working.apply(lambda row: _first_present(row, ["BU_NAME", "DSM_BU_NAME"], ""), axis=1)
    working["subject_or_title"] = working.apply(
        lambda row: _first_present(
            row,
            ["SUBJECT_C", "ACTION_PLAN_TITLE_C", "SUBJECT", "TITLE", "NAME", "COMMENTS__C"],
            "",
        ),
        axis=1,
    )
    working["status"] = working.apply(
        lambda row: _first_present(row, ["AB_STATUS_C", "STATUS_C", "STATUS__C", "STATUS"], ""),
        axis=1,
    )
    working["severity_or_rating"] = working.apply(
        lambda row: _first_present(row, ["SEVERITY_C", "PULSE_RATING__C", "SCORE__C", "SCORE_C"], ""),
        axis=1,
    )
    working["description_or_comments"] = working.apply(
        lambda row: _first_present(row, ["DESCRIPTION_C", "COMMENTS__C", "DESCRIPTION", "COMMENTS"], ""),
        axis=1,
    )
    working["created_date"] = working.apply(
        lambda row: _first_present(row, ["CREATED_DATE", "CREATEDDATE", "CREATED_DATE_C"], ""),
        axis=1,
    )
    working["open_date"] = working.apply(lambda row: _first_present(row, ["OPEN_DATE_C"], ""), axis=1)
    working["last_modified_date"] = working.apply(
        lambda row: _first_present(
            row,
            ["LAST_MODIFIED_DATE", "LASTMODIFIEDDATE", "LAST_MODIFIED_DATE_C", "LAST_EDITED_DATE"],
            "",
        ),
        axis=1,
    )
    working["owner_email"] = working.apply(
        lambda row: _first_present(row, ["OWNER_EMAIL", "OWNEREMAIL", "CREATEDBYEMAIL", "LASTMODIFIEDBYEMAIL"], ""),
        axis=1,
    )
    working["assignee_email"] = working.apply(lambda row: _first_present(row, ["ASSIGNEE_EMAIL"], ""), axis=1)
    working["assignee_name"] = working.apply(lambda row: _first_present(row, ["ASSIGNEE_C", "ASSIGNEE"], ""), axis=1)
    working["owner_name"] = working.apply(
        lambda row: _first_present(row, ["OWNER", "OWNER_NAME", "CREATEDBYNAME", "LASTMODIFIEDBYNAME"], ""),
        axis=1,
    )
    working["cssm_email"] = working.apply(lambda row: _first_present(row, ["CSSM_EMAIL", "DSM_CSSM_EMAIL"], ""), axis=1)
    working["cssm_name"] = working.apply(lambda row: _first_present(row, ["CSSM_NAME", "DSM_CSSM_NAME"], ""), axis=1)
    working["cssm_manager"] = working.apply(
        lambda row: _first_present(row, ["CSSM_MANAGER", "DSM_CSSM_MANAGER"], ""),
        axis=1,
    )
    return working


def _build_task_link(record_id: Any) -> str:
    record_text = str(record_id or "").strip()
    return TASK_RECORD_URL_TEMPLATE.format(record_id=record_text) if record_text else ""


def _build_customer_pulse_link(record_id: Any) -> str:
    record_text = str(record_id or "").strip()
    return CUSTOMER_PULSE_URL_TEMPLATE.format(record_id=record_text) if record_text else ""


def _query_task_entity(
    ctx,
    entity_type: str,
    record_type_id: str,
    candidate_columns: list[dict[str, str]],
    cutoff_date,
) -> pd.DataFrame:
    where_clause, params = _build_owner_match_clause(candidate_columns, TARGET_EMAIL, TARGET_NAME)
    sql = f"""
        SELECT *
        FROM {TASK_TABLE}
        WHERE RECORD_TYPE_ID = %s
          AND DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C)) >= %s
          AND ({where_clause})
    """
    df = _query_dataframe(ctx, sql, [record_type_id, cutoff_date, *params])
    return _apply_match_metadata(
        df=df,
        entity_type=entity_type,
        source_table=TASK_TABLE,
        candidate_columns=candidate_columns,
        link_builder=_build_task_link,
        link_status=TASK_LINK_STATUS,
    )


def _query_customer_pulse(
    ctx,
    candidate_columns: list[dict[str, str]],
    cutoff_date,
    known_user_ids: Iterable[str] | None = None,
) -> pd.DataFrame:
    where_clause, params = _build_owner_match_clause(candidate_columns, TARGET_EMAIL, TARGET_NAME, known_user_ids=known_user_ids)
    sql = f"""
        SELECT *
        FROM {CUSTOMER_PULSE_TABLE}
        WHERE DATE(CREATEDDATE) >= %s
          AND ({where_clause})
    """
    df = _query_dataframe(ctx, sql, [cutoff_date, *params])
    return _apply_match_metadata(
        df=df,
        entity_type="Customer Pulse",
        source_table=CUSTOMER_PULSE_TABLE,
        candidate_columns=candidate_columns,
        link_builder=_build_customer_pulse_link,
        link_status=CUSTOMER_PULSE_LINK_STATUS,
        known_user_ids=known_user_ids,
    )


def _looks_like_salesforce_id(value: Any) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9]{15,18}", str(value or "").strip()))


def _extract_salesforce_id(value: Any) -> str:
    raw_text = str(value or "").strip()
    if _looks_like_salesforce_id(raw_text):
        return raw_text.upper()
    match = re.search(r"/([A-Za-z0-9]{15,18})(?:[/?\"']|$)", raw_text)
    if match:
        return match.group(1).upper()
    return ""


def _infer_user_ids_from_task_records(*frames: pd.DataFrame) -> list[str]:
    pairings = [
        ("ASSIGNEE_C", "ASSIGNEE_PROFILE_C"),
        ("PLAN_OWNER_NAME_FORMULA_C", "PLAN_OWNER_C"),
        ("PLAN_OWNER_NAME_FORMULA_C", "CREATED_BY_ID"),
    ]
    inferred_ids: set[str] = set()
    for frame in frames:
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            for display_col, id_col in pairings:
                if display_col not in row.index or id_col not in row.index:
                    continue
                display_value = row.get(display_col)
                if not _match_details_for_row(
                    pd.Series({display_col: display_value}),
                    [{"column_name": display_col, "role_guess": "owner"}],
                    TARGET_EMAIL,
                    TARGET_NAME,
                ):
                    continue
                id_value = _extract_salesforce_id(row.get(id_col))
                if id_value:
                    inferred_ids.add(id_value)
    return sorted(inferred_ids)


def _fetch_account_enrichment(ctx, account_ids: list[str]) -> pd.DataFrame:
    clean_account_ids = sorted({str(value).strip() for value in account_ids if str(value or "").strip()})
    if not clean_account_ids:
        return pd.DataFrame()

    available_columns = _get_table_columns(ctx, DSM_TABLE)
    select_exprs = [
        "ACCOUNT_ID_C",
        _column_or_default_expr(available_columns, "BU_NAME", "''"),
        _column_or_default_expr(available_columns, "CSSM_EMAIL", "''"),
        _column_or_default_expr(available_columns, "CSSM_NAME", "''"),
        _column_or_default_expr(available_columns, "CSSM_MANAGER", "''"),
        _column_or_default_expr(available_columns, "CSSM_MANAGER_EMAIL", "''"),
    ]
    select_exprs = [expr for expr in select_exprs if expr]
    placeholders = ", ".join(["%s"] * len(clean_account_ids))
    sql = f"""
        SELECT {", ".join(select_exprs)}
        FROM {DSM_TABLE}
        WHERE ACCOUNT_ID_C IN ({placeholders})
    """
    df = _query_dataframe(ctx, sql, clean_account_ids)
    if df.empty:
        return df

    for col in ["ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL", "CSSM_NAME", "CSSM_MANAGER", "CSSM_MANAGER_EMAIL"]:
        if col not in df.columns:
            df[col] = ""
    df = df.drop_duplicates(subset=["ACCOUNT_ID_C"])
    return df


def _merge_account_enrichment(df: pd.DataFrame, enrichment_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or enrichment_df.empty or "account_id" not in df.columns:
        return df
    merge_cols = ["ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL", "CSSM_NAME", "CSSM_MANAGER", "CSSM_MANAGER_EMAIL"]
    renamed = enrichment_df[merge_cols].rename(
        columns={
            "ACCOUNT_ID_C": "account_id",
            "BU_NAME": "DSM_BU_NAME",
            "CSSM_EMAIL": "DSM_CSSM_EMAIL",
            "CSSM_NAME": "DSM_CSSM_NAME",
            "CSSM_MANAGER": "DSM_CSSM_MANAGER",
            "CSSM_MANAGER_EMAIL": "DSM_CSSM_MANAGER_EMAIL",
        }
    )
    merged = df.merge(renamed, on="account_id", how="left")
    fill_pairs = [
        ("customer_name", "DSM_BU_NAME"),
        ("bu_name", "DSM_BU_NAME"),
        ("cssm_email", "DSM_CSSM_EMAIL"),
        ("cssm_name", "DSM_CSSM_NAME"),
        ("cssm_manager", "DSM_CSSM_MANAGER"),
    ]
    for target_col, source_col in fill_pairs:
        if target_col in merged.columns and source_col in merged.columns:
            merged[target_col] = merged[target_col].where(merged[target_col].astype(str).str.strip() != "", merged[source_col])
    return merged


def _schema_audit_rows(
    table_name: str,
    candidate_columns: list[dict[str, str]],
    matched_df: pd.DataFrame,
    link_status: str,
    link_object: str,
    inferred_user_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    matched_columns = set()
    if not matched_df.empty and "matched_column" in matched_df.columns:
        for cell in matched_df["matched_column"].fillna("").astype(str):
            for name in [part.strip() for part in cell.split(";") if part.strip()]:
                matched_columns.add(name)
    for meta in candidate_columns:
        rows.append(
            {
                "table_name": table_name,
                "candidate_column": meta["column_name"],
                "role_guess": meta["role_guess"],
                "used_for_filter": True,
                "matched_any_rows": meta["column_name"] in matched_columns,
                "matched_row_count": int(
                    matched_df["matched_column"].fillna("").astype(str).str.contains(re.escape(meta["column_name"])).sum()
                )
                if not matched_df.empty and "matched_column" in matched_df.columns
                else 0,
                "link_status": link_status,
                "link_object_api_name": link_object,
                "inferred_user_ids": "; ".join(sorted({str(v) for v in (inferred_user_ids or []) if str(v).strip()})),
            }
        )
    if not candidate_columns:
        rows.append(
            {
                "table_name": table_name,
                "candidate_column": "(none found)",
                "role_guess": "",
                "used_for_filter": False,
                "matched_any_rows": False,
                "matched_row_count": 0,
                "link_status": link_status,
                "link_object_api_name": link_object,
                "inferred_user_ids": "; ".join(sorted({str(v) for v in (inferred_user_ids or []) if str(v).strip()})),
            }
        )
    return rows


def _normalized_union(datasets: list[pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for df in datasets:
        if df.empty:
            continue
        frames.append(df.reindex(columns=NORMALIZED_EXPORT_COLUMNS))
    if not frames:
        return pd.DataFrame(columns=NORMALIZED_EXPORT_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    combined["_sort_created_date"] = pd.to_datetime(combined["created_date"], errors="coerce", utc=True).dt.tz_localize(None)
    combined = combined.sort_values(
        by=["entity_type", "customer_name", "_sort_created_date", "record_id"],
        ascending=[True, True, False, True],
        na_position="last",
    ).drop(columns=["_sort_created_date"])
    return combined


def _concat_non_empty_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    usable_frames = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable_frames:
        return pd.DataFrame()
    return pd.concat(usable_frames, ignore_index=True)


def _set_worksheet_layout(worksheet, df: pd.DataFrame) -> None:
    worksheet.freeze_panes(1, 0)
    if not df.empty:
        worksheet.autofilter(0, 0, len(df), max(len(df.columns) - 1, 0))
    for idx, column in enumerate(df.columns):
        values = df[column].astype(str).fillna("")
        max_len = max([len(column), *(len(value) for value in values.head(200))]) if not values.empty else len(column)
        worksheet.set_column(idx, idx, min(max(max_len + 2, 14), 60))


def _write_hyperlinks(worksheet, df: pd.DataFrame) -> None:
    if "record_link" not in df.columns:
        return
    link_col_idx = df.columns.get_loc("record_link")
    for row_idx, value in enumerate(df["record_link"].fillna("").astype(str), start=1):
        if value.startswith("https://"):
            worksheet.write_url(row_idx, link_col_idx, value, string=value)


def _write_workbook(output_path: Path, sheets: dict[str, pd.DataFrame]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        for sheet_name, df in sheets.items():
            safe_df = _coerce_excel_safe(df)
            safe_df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            worksheet = writer.sheets[sheet_name[:31]]
            _set_worksheet_layout(worksheet, safe_df)
            _write_hyperlinks(worksheet, safe_df)
    return output_path


def _build_output_path(custom_output: str | None) -> Path:
    if custom_output:
        return Path(custom_output)
    # Round 30 / L1: stamp the export filename in UTC so the
    # timestamp matches ``run_export``'s ``cutoff_date`` (which uses
    # ``datetime.now(UTC)``) and the report-data-retrieved-at
    # contract used by every other AdoptIQ surface.  ``datetime.now()``
    # without a tz silently picks up the host's local timezone, which
    # made cross-export filename comparisons unreliable when the
    # export runs from a host whose clock drifted relative to UTC.
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return Path(Config.OUTPUT_FOLDER) / f"Adrian_De_Leon_Snowflake_Records_{timestamp}.xlsx"


def run_export(output_path: str | None = None, years: int = 3, schema_only: bool = False) -> dict[str, Any]:
    ctx = _connect_with_keeper()
    try:
        cutoff_date = (datetime.now(UTC) - timedelta(days=365 * years)).date()

        task_columns = _schema_columns_for_table(ctx, TASK_TABLE)
        pulse_columns = _schema_columns_for_table(ctx, CUSTOMER_PULSE_TABLE)
        task_candidate_columns = _owner_candidate_columns(task_columns)
        pulse_candidate_columns = _owner_candidate_columns(pulse_columns)

        adoption_barriers = pd.DataFrame()
        action_plans = pd.DataFrame()
        customer_pulse = pd.DataFrame()
        inferred_user_ids: list[str] = []

        if not schema_only:
            adoption_barriers = _query_task_entity(
                ctx=ctx,
                entity_type="Adoption Barrier",
                record_type_id=ADOPTION_BARRIER_RECORD_TYPE,
                candidate_columns=task_candidate_columns,
                cutoff_date=cutoff_date,
            )
            action_plans = _query_task_entity(
                ctx=ctx,
                entity_type="Action Plan",
                record_type_id=ACTION_PLAN_RECORD_TYPE,
                candidate_columns=task_candidate_columns,
                cutoff_date=cutoff_date,
            )
            inferred_user_ids = _infer_user_ids_from_task_records(adoption_barriers, action_plans)
            customer_pulse = _query_customer_pulse(
                ctx=ctx,
                candidate_columns=pulse_candidate_columns,
                cutoff_date=cutoff_date,
                known_user_ids=inferred_user_ids,
            )

            all_account_ids = []
            for frame in (adoption_barriers, action_plans, customer_pulse):
                if not frame.empty and "account_id" in frame.columns:
                    all_account_ids.extend(frame["account_id"].dropna().astype(str).tolist())
            enrichment_df = _fetch_account_enrichment(ctx, all_account_ids)
            adoption_barriers = _merge_account_enrichment(adoption_barriers, enrichment_df)
            action_plans = _merge_account_enrichment(action_plans, enrichment_df)
            customer_pulse = _merge_account_enrichment(customer_pulse, enrichment_df)
        else:
            enrichment_df = pd.DataFrame()

        schema_audit = pd.DataFrame(
            _schema_audit_rows(
                table_name=TASK_TABLE,
                candidate_columns=task_candidate_columns,
                matched_df=_concat_non_empty_frames([adoption_barriers, action_plans]) if not schema_only else pd.DataFrame(),
                link_status=TASK_LINK_STATUS,
                link_object="C360_CS_Task__c",
                inferred_user_ids=inferred_user_ids,
            )
            + _schema_audit_rows(
                table_name=CUSTOMER_PULSE_TABLE,
                candidate_columns=pulse_candidate_columns,
                matched_df=customer_pulse if not schema_only else pd.DataFrame(),
                link_status=CUSTOMER_PULSE_LINK_STATUS,
                link_object="ESA_C360_CUSTOMER_PULSE__c",
                inferred_user_ids=inferred_user_ids,
            )
        )

        if schema_only:
            return {
                "task_candidate_columns": task_candidate_columns,
                "pulse_candidate_columns": pulse_candidate_columns,
                "schema_audit": schema_audit,
            }

        all_records = _normalized_union([adoption_barriers, action_plans, customer_pulse])
        sheets = {
            "All_Records": all_records,
            "Adoption_Barriers": adoption_barriers,
            "Customer_Pulse": customer_pulse,
            "Action_Plans": action_plans,
            "Schema_Audit": schema_audit,
        }
        final_output_path = _write_workbook(_build_output_path(output_path), sheets)
        return {
            "output_path": str(final_output_path),
            "row_counts": {
                "Adoption Barriers": int(len(adoption_barriers)),
                "Customer Pulse": int(len(customer_pulse)),
                "Action Plans": int(len(action_plans)),
                "All Records": int(len(all_records)),
            },
            "task_candidate_columns": task_candidate_columns,
            "pulse_candidate_columns": pulse_candidate_columns,
            "schema_audit": schema_audit,
            "enrichment_rows": int(len(enrichment_df)),
            "inferred_user_ids": inferred_user_ids,
            "sample_links": {
                "adoption_barrier": adoption_barriers["record_link"].iloc[0] if not adoption_barriers.empty else "",
                "action_plan": action_plans["record_link"].iloc[0] if not action_plans.empty else "",
                "customer_pulse": customer_pulse["record_link"].iloc[0] if not customer_pulse.empty else "",
            },
        }
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Adrian De Leon Snowflake records to Excel.")
    parser.add_argument("--output", help="Output Excel path. Defaults to outputs/Adrian_De_Leon_Snowflake_Records_<timestamp>.xlsx")
    parser.add_argument("--years", type=int, default=3, help="How many years of history to export.")
    parser.add_argument("--schema-only", action="store_true", help="Inspect candidate owner columns without exporting records.")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args()
    result = run_export(output_path=args.output, years=args.years, schema_only=args.schema_only)
    if args.schema_only:
        print("Task-table owner-like columns:")
        for item in result["task_candidate_columns"]:
            print(f'  - {item["column_name"]} ({item["role_guess"]})')
        print("Customer Pulse owner-like columns:")
        for item in result["pulse_candidate_columns"]:
            print(f'  - {item["column_name"]} ({item["role_guess"]})')
        return 0

    print(f'Workbook written: {result["output_path"]}')
    for label, count in result["row_counts"].items():
        print(f"{label}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
