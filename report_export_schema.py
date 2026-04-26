"""Round 15 / Phase 1 -- Single source of truth for customer-facing export columns.

The pre-Round-15 reports leaked Salesforce / ETL plumbing into the customer
deliverable. Concretely, the gold-standard run that drove this round
exported:

  * AB_Detail_All           272 columns wide, including ``IS_DELETED``,
                            ``MAY_EDIT``, ``IS_LOCKED``, ``RECORD_TYPE_ID``,
                            ``SYSTEM_MODSTAMP``, ``LAST_VIEWED_DATE``,
                            ``LAST_REFERENCED_DATE``,
                            ``CONNECTION_RECEIVED_ID``,
                            ``CONNECTION_SENT_ID``.
  * CSConsole_Customer_Pulse  ETL/SF plumbing (``ETL_ID``, ``DELETE_FLAG``,
                              ``RECURSIVE_FLAG``, ``ISDELETED``, ``MAYEDIT``,
                              ``ISLOCKED``, ``CONNECTIONRECEIVEDID``,
                              ``CONNECTIONSENTID``, ``CREATEDBYID``,
                              ``LASTMODIFIEDBYID``, ``SYSTEMMODSTAMP``,
                              and the ``EDWSF_*`` warehouse columns).
  * External_Incidents      internal markers ``_stale_storage``,
                            ``_from_storage``, ``_window_meta`` plus the
                            Cisco-internal ``publication_id``.
  * CSOne_Detail_All        Salesforce-relationship label oddities --
                            ``'Customer Name: Customer Name'``, literal
                            ``'col_2'`` / ``'col_32'``, duplicated
                            ``Sub Technology`` / ``Sub Tech.``.

This module is the *only* place that decides which columns make it into
the customer-facing workbook. Writers in ``adoptiq_backend``,
``executive_intelligence_formatter``, ``executive_report_builder``, and
``leader_report_generator`` route their per-sheet DataFrames through
``apply_export_schema(df, sheet_name)`` immediately before write.

Design contract:
  * Denylists are *exact-match* / *prefix-match*; no regex. Every entry
    is documented inline.
  * Allowlists (``CURATED_COLUMNS``) are *ordered*; when defined, the
    writer projects to that order, dropping anything not in the list.
  * If a sheet has no curated allowlist entry, the denylist is the only
    filter (so new sheets ship safely without having to extend the SSoT).
  * Renames are applied *before* allow-/deny-listing so the curated
    list can use the human-friendly names.
  * The function is pure and never raises; defensive against non-DataFrame
    inputs so unit tests can pass plain lists/dicts.
"""

from __future__ import annotations

from typing import Iterable, Mapping

# ---------------------------------------------------------------------------
# Denylist -- internal / SF / ETL plumbing that must never reach customers
# ---------------------------------------------------------------------------

#: Salesforce object metadata that is meaningless to customers.
#: ``IS_DELETED`` / ``ISDELETED`` etc. exist for SF row tombstones; the
#: rows are already filtered upstream so the column is just noise.
_SF_PLUMBING_EXACT: frozenset[str] = frozenset(
    {
        # SF audit / system metadata (CamelCase + UPPER_SNAKE variants)
        "IS_DELETED",
        "ISDELETED",
        "MAY_EDIT",
        "MAYEDIT",
        "IS_LOCKED",
        "ISLOCKED",
        "RECORD_TYPE_ID",
        "RECORDTYPEID",
        "SYSTEM_MODSTAMP",
        "SYSTEMMODSTAMP",
        "LAST_VIEWED_DATE",
        "LASTVIEWEDDATE",
        "LAST_REFERENCED_DATE",
        "LASTREFERENCEDDATE",
        "CONNECTION_RECEIVED_ID",
        "CONNECTIONRECEIVEDID",
        "CONNECTION_SENT_ID",
        "CONNECTIONSENTID",
        "CREATED_BY_ID",
        "CREATEDBYID",
        "LAST_MODIFIED_BY_ID",
        "LASTMODIFIEDBYID",
        # SF lifecycle audit timestamps that are *separate* from the
        # business CREATED_DATE_C / CLOSED_DATE_C columns
        "CREATED_DATE",
        "CREATEDDATE",
        "LAST_MODIFIED_DATE",
        "LASTMODIFIEDDATE",
        "LAST_ACTIVITY_DATE",
        "LASTACTIVITYDATE",
        # CurrencyIsoCode (we already disclose currency in business columns)
        "CURRENCY_ISO_CODE",
        "CURRENCYISOCODE",
    }
)

#: ETL warehouse plumbing -- produced by the EDW/Fivetran pipelines.
_ETL_PLUMBING_EXACT: frozenset[str] = frozenset(
    {
        "ETL_ID",
        "DELETE_FLAG",
        "RECURSIVE_FLAG",
        "EDWSF_BATCH_ID",
        "EDWSF_CREATE_DTM",
        "EDWSF_CREATE_USER",
        "EDWSF_UPDATE_DTM",
        "EDWSF_UPDATE_USER",
        "EDWSF_SOURCE_DELETED_FLAG",
        # Fivetran sync columns leaked from the AB warehouse view
        "_FIVETRAN_SYNCED",
        "_FIVETRAN_DELETED",
    }
)

#: Internal pipeline / debug markers exported by AdoptIQ itself.
_INTERNAL_DEBUG_EXACT: frozenset[str] = frozenset(
    {
        "_stale_storage",
        "_from_storage",
        "_window_meta",
        # Internal lookup-id columns; not actionable for a customer
        "publication_id",
        # Case-folded copies used for boolean classification
        "Title_Lower",
        "Desc_Lower",
        # CSOne raw-extract column-position placeholders
        "col_2",
        "col_32",
    }
)

#: Public, exact-match denylist; the union of the buckets above.
INTERNAL_COLUMN_DENYLIST: frozenset[str] = (
    _SF_PLUMBING_EXACT | _ETL_PLUMBING_EXACT | _INTERNAL_DEBUG_EXACT
)

#: Anything starting with one of these prefixes is dropped. ``_``
#: catches our own pipeline markers; ``EDWSF_`` is a defensive belt-and-
#: suspenders because the EDW view occasionally adds new ``EDWSF_*`` columns.
INTERNAL_COLUMN_PREFIXES: tuple[str, ...] = ("_", "EDWSF_")

# ---------------------------------------------------------------------------
# Per-sheet header renames -- applied first
# ---------------------------------------------------------------------------

#: Column-rename map keyed by sheet name. Each inner mapping is
#: ``{raw_header: customer_facing_header}``.
SHEET_HEADER_RENAMES: Mapping[str, Mapping[str, str]] = {
    # Salesforce relationship-label leak: ``Customer Name: Customer Name``
    # is what SOQL renders for a self-joined relationship; collapse to the
    # plain ``Customer`` label customers expect.
    "CSOne_Detail_All": {
        "Customer Name: Customer Name": "Customer",
        "Product: Product Name": "Product",
        "Case Owner: Full Name": "Case Owner",
        "Technology Lookup: Technology Auto Number": "Technology Auto Number",
        "Sub Technology Lookup: Sub Technology Auto Number": "Sub Technology Auto Number",
    },
}

# ---------------------------------------------------------------------------
# Per-sheet curated allowlists -- applied last, in order
# ---------------------------------------------------------------------------

#: ``AB_Detail_All`` curated set -- ~40 customer-facing columns picked
#: from the 272-wide raw view. Order is the order they will appear in
#: the workbook.
_CURATED_AB_DETAIL_ALL: tuple[str, ...] = (
    # Identity / customer
    "ID",
    "NAME",
    "customer_name",
    "ACCOUNT_MANAGER_C",
    "ASSIGNEE_C",
    "assignee_cssm_email",
    "BUSINESS_UNIT_C",
    "THEATER_C",
    "SALES_LEVEL_4_C",
    "SALES_LEVEL_5_C",
    # Barrier description
    "SUBJECT_C",
    "title",
    "DESCRIPTION_C",
    "description",
    "ADOPTION_BARRIER_TYPE_C",
    "ADOPTION_BARRIER_LEVEL_C",
    "AB_CATEGORY_C",
    "ab_category_final",
    "FEATURE_C",
    "PRODUCT_C",
    "PRODUCT_NAME_C",
    "RELATED_PRODUCT_C",
    "sub_technology",
    # State / severity
    "AB_STATUS_C",
    "STATUS_C",
    "status_norm",
    "SEVERITY_C",
    "severity_norm",
    "PRIORITY_C",
    "AB_ESCALATE_C",
    "AB_HOLD_REASON_C",
    "AB_WAITING_FOR_C",
    "AB_WAITING_FOR_DETAIL_C",
    "AB_PARTNER_ISSUE_C",
    "REASON_C",
    "CLOSED_REASON_C",
    "CLOSURE_REASON_C",
    # Dates / age
    "OPEN_DATE_C",
    "open_date",
    "DUE_DATE_C",
    "ORIGINAL_DUE_DATE_C",
    "CLOSED_DATE_C",
    "closed_date",
    "open_age_days",
    "AGE_C",
    "DAYS_IN_STAGE_C",
    "HOLD_DAYS_C",
    # ARR / health
    "AOV_C",
    "PRODUCT_ARR_C",
    "SERVICE_ARR_C",
    "AOV_GROSS_RETENTION_RATE_C",
    "BU_HEALTH_SCORE_C",
    "USE_CASE_HEALTH_SCORE_C",
    "SOLUTION_DOMAIN_HEALTH_SCORE_C",
    # Action plan / next step
    "ACTION_PLAN_TITLE_C",
    "ACTION_C",
    "ACTION_TYPE_C",
    "ACTION_SUB_TYPE_C",
    "NEXT_ACTION_C",
    "NEXT_STEP_C",
    "NEXT_ACTION_OWNER_C",
    "NEXT_ACTION_DUE_DATE_C",
    # Linked TAC / case context
    "TAC_CASE_NUMBER_LINK_C",
    "COUNT_OF_LINKED_CASES_C",
    "LINKED_CASES_C",
    "COUNT_OF_LINKED_CTAS_C",
    "COUNT_OF_LINKED_ACTIVITIES_C",
    "bemscsc_refs",
    # Free-form context kept last so it doesn't push action columns offscreen
    "COMMENTS_C",
    "CURRENT_STATUS_AND_NOTES_C",
    "CLOSURE_COMMENTS_C",
    "FEEDBACK_COMMENTS_C",
)

#: ``CSOne_Detail_All`` curated set. Renames in
#: ``SHEET_HEADER_RENAMES`` apply *before* this list, so we can use the
#: friendly labels here.
_CURATED_CSONE_DETAIL_ALL: tuple[str, ...] = (
    "Customer",
    "customer_name",
    "Subscription Reference Id",
    "SUBSCRIPTION_ID",
    "Product",
    "Tech.",
    "Severity",
    "severity_norm",
    "Service Tier",
    "Highest Priority",
    "case_priority_norm",
    "SR Number",
    "Case Number",
    "Title",
    "Case Status",
    "case_status_norm",
    "is_open",
    "is_closed",
    "case_classification",
    "case_type_class",
    "is_bems",
    "Transaction ID",
    "bemscsc_refs",
    "Date/Time Opened",
    "open_date",
    "Date/Time Closed",
    "closed_date",
    "open_age_days",
    "closed_age_days",
    "Case Owner",
    "Current Contact Email",
    "Case Origin",
    "Problem Code",
    "Resolution Code",
    "Problem Description",
    "Problem Details",
    "CSE Action Plan",
    "Last Cisco Update",
    "Resolution Summary",
    "Customer Activity",
    "# of Case Owner Changes",
)

#: ``External_Bugs`` is already minimal. Order kept stable.
_CURATED_EXTERNAL_BUGS: tuple[str, ...] = (
    "bug_id",
    "title",
    "source",
    "source_url",
    "discovered_at",
)

#: ``External_Incidents`` keepers; the underscore-prefixed leak is also
#: caught by ``INTERNAL_COLUMN_PREFIXES`` but explicit denial is clearer.
_CURATED_EXTERNAL_INCIDENTS: tuple[str, ...] = (
    "id",
    "incident_number",
    "title",
    "status",
    "impact_level",
    "source",
    "link",
    "description",
    "affected_components",
    "locations",
    "published",
    "first_seen",
    "last_seen",
    "resolved_at",
)

#: ``CSConsole_Customer_Pulse`` keepers -- the substantive customer-pulse
#: signal columns plus the BU label, dropping all ETL / SF audit columns.
_CURATED_CSCONSOLE_CUSTOMER_PULSE: tuple[str, ...] = (
    "ID",
    "NAME",
    "BU_NAME",
    "ACCOUNT__C",
    "CUSTOMER_PULSE__C",
    "CUSTOMER_PULSE_COLOR_IMAGE__C",
    "PRODUCT__C",
    "COMMENTS__C",
    "OWNERID",
    "RECORD_SOURCE",
)

#: Public per-sheet curated mapping. Sheets *not* in this dict fall
#: through with denylist + prefix filtering only.
CURATED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "AB_Detail_All": _CURATED_AB_DETAIL_ALL,
    "CSOne_Detail_All": _CURATED_CSONE_DETAIL_ALL,
    "External_Bugs": _CURATED_EXTERNAL_BUGS,
    "External_Incidents": _CURATED_EXTERNAL_INCIDENTS,
    "CSConsole_Customer_Pulse": _CURATED_CSCONSOLE_CUSTOMER_PULSE,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_internal_column(name: object) -> bool:
    """Return True if ``name`` is on the SF/ETL/internal denylist.

    Defensive against non-string inputs; ``None``/``int``/``NaN`` just
    return False so a malformed header doesn't blow up the writer.
    """
    if not isinstance(name, str) or not name:
        return False
    if name in INTERNAL_COLUMN_DENYLIST:
        return True
    for prefix in INTERNAL_COLUMN_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def filter_columns(
    columns: Iterable[object], sheet_name: str | None = None
) -> list[str]:
    """Return ``columns`` projected to the customer-facing set.

    1. drop anything that ``is_internal_column`` flags;
    2. if ``sheet_name`` has a curated allowlist, project to that order
       (preserving only columns that are still present);
    3. otherwise return the surviving columns in input order.
    """
    surviving = [str(c) for c in columns if not is_internal_column(c)]
    if sheet_name and sheet_name in CURATED_COLUMNS:
        present = set(surviving)
        return [c for c in CURATED_COLUMNS[sheet_name] if c in present]
    return surviving


def apply_export_schema(df, sheet_name: str | None = None):
    """Apply renames, denylist, and curation to ``df``.

    Returns a new DataFrame with the customer-facing column projection.
    Falls back to a best-effort no-op when ``df`` doesn't expose the
    pandas DataFrame interface (e.g. tests passing plain lists/dicts).
    """
    try:
        import pandas as pd
    except Exception:
        return df
    if df is None:
        return df
    if not isinstance(df, pd.DataFrame):
        try:
            df = pd.DataFrame(df)
        except Exception:
            return df

    out = df

    rename_map = SHEET_HEADER_RENAMES.get(sheet_name or "", {})
    if rename_map:
        applicable = {src: dst for src, dst in rename_map.items() if src in out.columns}
        if applicable:
            out = out.rename(columns=applicable)

    target = filter_columns(out.columns, sheet_name=sheet_name)
    if not target:
        return out.iloc[:, 0:0]
    return out.loc[:, target]


__all__ = [
    "INTERNAL_COLUMN_DENYLIST",
    "INTERNAL_COLUMN_PREFIXES",
    "CURATED_COLUMNS",
    "SHEET_HEADER_RENAMES",
    "is_internal_column",
    "filter_columns",
    "apply_export_schema",
]
