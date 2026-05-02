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
# Round 45 / Phase 5 -- Friendly Snowflake column-name labels (cross-sheet)
# ---------------------------------------------------------------------------

#: Raw Snowflake / Salesforce ``_C`` / ``__C`` / BU_NAME / customer_name
#: column names mapped to director-friendly labels.  Applied AFTER the
#: per-sheet ``SHEET_HEADER_RENAMES`` and the curated-allowlist projection,
#: so we never disturb the upstream pipeline (filtering, sorting, joining,
#: cell-comment generation) -- only the visible Excel column header is
#: changed.
#:
#: Round 44 friendlied the Word-side narrative source-citation italics
#: but explicitly did NOT touch the Excel sidecars, which still leaked
#: 9 raw headers in Comprehensive (``AB_Detail_All`` /
#: ``CSConsole_Customer_Pulse``), 16 in Renewal
#: (``Customer_Adoption_Barriers`` / ``Customer_Action_Plans``) and 21
#: in Leader (``Action_Plans`` / ``Adoption_Barriers`` /
#: ``Customer_Pulse`` / ``Subscriptions``) per the 2026-04-28 build-20
#: artifact audit.  This map closes that gap with a single SSoT
#: cross-sheet rename so future writers automatically inherit the
#: friendly labels.
#:
#: Engineers who need the underlying Snowflake column for traceability
#: can find the raw schema in ``QUALITY_AUDIT.md`` (Round 7 row-contract
#: aliases section) and in the per-sheet curated tuples above.
_FRIENDLY_HEADER_LABELS: Mapping[str, str] = {
    # Customer / account identity
    "BU_NAME": "Customer Name",
    "customer_name": "Customer Name",
    "ACCOUNT_ID_C": "Account ID",
    "ACCOUNT__C": "Account ID",
    # Status / severity / priority
    "AB_STATUS_C": "Adoption Barrier Status",
    "STATUS_C": "Status",
    "status_norm": "Status (Normalized)",
    "SEVERITY_C": "Severity",
    "severity_norm": "Severity (Normalized)",
    "PRIORITY_C": "Priority",
    "case_priority_norm": "Priority (Normalized)",
    "case_status_norm": "Case Status (Normalized)",
    # Dates -- BUSINESS dates only.  SF audit timestamps
    # (CREATED_DATE / LAST_MODIFIED_DATE) are already on the
    # ``_SF_PLUMBING_EXACT`` denylist and never reach this stage.
    "OPEN_DATE_C": "Open Date",
    "DUE_DATE_C": "Due Date",
    "ORIGINAL_DUE_DATE_C": "Original Due Date",
    "CLOSED_DATE_C": "Closed Date",
    "open_age_days": "Open Age (Days)",
    "closed_age_days": "Closed Age (Days)",
    "AGE_C": "Age (Days)",
    "DAYS_IN_STAGE_C": "Days in Stage",
    "HOLD_DAYS_C": "Hold Days",
    # Free-form text / comments
    "COMMENTS_C": "Comments",
    "COMMENTS__C": "Comments",
    "CURRENT_STATUS_AND_NOTES_C": "Current Status / Notes",
    "CLOSURE_COMMENTS_C": "Closure Comments",
    "FEEDBACK_COMMENTS_C": "Feedback Comments",
    "DESCRIPTION_C": "Description",
    "SUBJECT_C": "Subject",
    # Pulse-specific (CSConsole)
    "PULSE_RATING__C": "Pulse Rating",
    "CUSTOMER_PULSE__C": "Customer Pulse",
    "CUSTOMER_PULSE_COLOR_IMAGE__C": "Customer Pulse Color",
    "PRODUCT__C": "Product",
    # Ownership / assignment
    "ACCOUNT_MANAGER_C": "Account Manager",
    "ASSIGNEE_C": "Assignee",
    "OWNERID": "Owner",
    "OWNER_C": "Owner",
    # Product / classification
    "PRODUCT_C": "Product",
    "PRODUCT_NAME_C": "Product Name",
    "RELATED_PRODUCT_C": "Related Product",
    "FEATURE_C": "Feature",
    "ADOPTION_BARRIER_TYPE_C": "Barrier Type",
    "ADOPTION_BARRIER_LEVEL_C": "Barrier Level",
    "AB_CATEGORY_C": "Barrier Category",
    "ab_category_final": "Barrier Category (Final)",
    "BUSINESS_UNIT_C": "Business Unit",
    "THEATER_C": "Theater",
    "SALES_LEVEL_4_C": "Sales Level 4",
    "SALES_LEVEL_5_C": "Sales Level 5",
    # Reasons / classification
    "REASON_C": "Reason",
    "CLOSED_REASON_C": "Closed Reason",
    "CLOSURE_REASON_C": "Closure Reason",
    "AB_ESCALATE_C": "Escalated",
    "AB_HOLD_REASON_C": "Hold Reason",
    "AB_WAITING_FOR_C": "Waiting For",
    "AB_WAITING_FOR_DETAIL_C": "Waiting For Detail",
    "AB_PARTNER_ISSUE_C": "Partner Issue",
    # ARR / health
    "AOV_C": "Annual Order Value",
    "PRODUCT_ARR_C": "Product ARR",
    "SERVICE_ARR_C": "Service ARR",
    "AOV_GROSS_RETENTION_RATE_C": "Gross Retention Rate",
    "BU_HEALTH_SCORE_C": "BU Health Score",
    "USE_CASE_HEALTH_SCORE_C": "Use Case Health Score",
    "SOLUTION_DOMAIN_HEALTH_SCORE_C": "Solution Domain Health Score",
    # Action plan / next step
    "ACTION_PLAN_TITLE_C": "Action Plan Title",
    "ACTION_C": "Action",
    "ACTION_TYPE_C": "Action Type",
    "ACTION_SUB_TYPE_C": "Action Sub-Type",
    "NEXT_ACTION_C": "Next Action",
    "NEXT_STEP_C": "Next Step",
    "NEXT_ACTION_OWNER_C": "Next Action Owner",
    "NEXT_ACTION_DUE_DATE_C": "Next Action Due Date",
    # Linked TAC context
    "TAC_CASE_NUMBER_LINK_C": "TAC Case Number",
    "COUNT_OF_LINKED_CASES_C": "Linked Cases (Count)",
    "LINKED_CASES_C": "Linked Cases",
    "COUNT_OF_LINKED_CTAS_C": "Linked CTAs (Count)",
    "COUNT_OF_LINKED_ACTIVITIES_C": "Linked Activities (Count)",
}


def friendly_header(name: object) -> str:
    """Round 45 / Phase 5: return the director-friendly label for a raw
    Snowflake / SF column name, or ``str(name)`` unchanged when the
    column is not in the friendly-label SSoT.

    Defensive against non-string inputs so a malformed header (None,
    int, NaN) never breaks the writer.
    """
    if name is None:
        return ""
    key = str(name)
    return _FRIENDLY_HEADER_LABELS.get(key, key)


# ---------------------------------------------------------------------------
# Round 45 / Phase 8 + 9 -- body-cell sanitization (Markdown chrome + None)
# ---------------------------------------------------------------------------

#: Columns whose values are FREE-FORM TEXT (operator-typed comments,
#: meeting notes, descriptions) where leftover Markdown control chars
#: and literal ``None`` strings consistently leak into the Excel
#: artifact.  Names below are the FRIENDLY labels (post-Phase-5 rename)
#: so the SSoT here matches what the workbook actually shows.  Values
#: outside this set are NOT touched -- categorical columns that
#: legitimately contain the literal string ``"None"`` (e.g.
#: ``hold_reason="None"`` meaning "not on hold") keep their value.
_BODY_TEXT_COLUMNS_FRIENDLY: frozenset[str] = frozenset(
    {
        "Comments",
        "Closure Comments",
        "Feedback Comments",
        "Current Status / Notes",
        "Description",
        "Subject",
        "Action Plan Title",
        "Next Action",
        "Next Step",
        # CSOne_Detail_All free-text columns (already friendly via
        # SHEET_HEADER_RENAMES).
        "Title",
        "Problem Description",
        "Problem Details",
        "Resolution Summary",
        "Customer Activity",
        "CSE Action Plan",
        "Last Cisco Update",
    }
)


def _r45_clean_excel_body_cell(value: object) -> object:
    """Round 45 / Phase 8 + 9: scrub a single free-form Excel body cell.

    * Markdown chrome: ``**bold**`` / ``__italic__`` runs collapse to
      the inner text.  Pattern matches the leader-side helper at
      ``leader_report_generator._strip_markdown_chrome`` so the SSoT
      stays consistent across Word and Excel paths.
    * Literal ``None`` and pandas NA / NaN: coerce to empty string.
      We do NOT use an em-dash here because pandas' Excel writer
      already renders bare empty cells correctly and customers
      expressed a preference for blank cells over filler glyphs.

    Pure / safe: returns ``value`` unchanged on any error so a regex
    regression cannot break the report build.
    """
    # Round 45 / Phase 9: pandas NA / NaN handling.  These compare
    # ``!= self`` (the standard NaN identity check) so we use a
    # cheap try-except wrapper rather than importing pandas at
    # module top-level (apply_export_schema already does that lazily).
    try:
        if value is None:
            return ""
        # NaN check without importing math at module top-level.
        if isinstance(value, float) and value != value:  # noqa: PLR0124
            return ""
        # pandas NA / numpy NA -- detect via the ``isna`` interface.
        try:
            import pandas as _pd  # local import: this helper is hot
            if _pd.isna(value):  # type: ignore[arg-type]
                return ""
        except Exception:  # noqa: BLE001
            # If pandas is unavailable we already handled None and NaN
            # above; categorical NA types degrade to their str repr.
            pass
        s = str(value)
    except Exception:
        return value

    if not s:
        return ""

    # Round 45 / Phase 9: literal "None" string (case-sensitive --
    # only the Python-stringified None, not the categorical value
    # "none" which legitimately appears in some hold/reason columns).
    if s == "None" or s == "nan" or s == "NaN" or s == "<NA>":
        return ""

    # Round 45 / Phase 8: Markdown chrome scrub (mirrors
    # leader_report_generator._strip_markdown_chrome).  Local re
    # import keeps the helper lean for cells that don't need cleaning.
    try:
        import re as _re_local
        # Bold / strong indicator: runs of >=2 stars at any anchor.
        s = _re_local.sub(r"\*{2,}", "", s)
        # Italic indicator: runs of >=2 underscores at word boundary,
        # narrow match so internal identifiers (snake_case, __C) are
        # preserved.
        s = _re_local.sub(r"(?<!\w)_{2,}(?=\w)|(?<=\w)_{2,}(?!\w)", "", s)
        # Collapse runs of whitespace introduced by stripping.
        s = _re_local.sub(r"\s{2,}", " ", s).strip()
    except Exception:  # noqa: BLE001
        pass
    return s


def _r45_clean_body_columns(df, columns: frozenset[str]) -> None:
    """Round 45 / Phase 8 + 9: in-place cell cleanup for the named
    free-form text columns.  Rows are mutated only when the column is
    present in ``df``.  Used by ``apply_export_schema`` after the
    friendly-rename pass.

    Mutates ``df`` in place AND also returns ``None`` so callers don't
    accidentally rely on a chained return.
    """
    try:
        for col in columns:
            if col in df.columns:
                df[col] = df[col].map(_r45_clean_excel_body_cell)
    except Exception:  # noqa: BLE001 -- never let cleanup break the
        # writer; the worst case is body cells with leftover chrome
        # (same as pre-Round-45).
        return None
    return None

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
    # Round 49 / F-RP-AB-RAW-HEADERS: include both raw customer-name
    # columns the upstream pipeline may emit (``BU_NAME`` from
    # team_subs merges, ``customer_name`` lowercase from normalized
    # frames) so the renewal / compact AB sheets keep their
    # customer-name column when projected through this allowlist.
    # ``apply_export_schema`` always runs the friendly-rename pass
    # AFTER projection, so whichever raw form is present collapses
    # to the single customer-facing ``Customer Name`` header.
    "BU_NAME",
    "customer_name",
    "ACCOUNT_ID_C",
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
#:
#: Round 49 / F-RP-AB-RAW-HEADERS: register the three live AB sheet
#: names that the writers actually emit -- ``Customer_Adoption_Barriers``
#: (renewal portfolio), ``All_Adoption_Barriers`` (compact), and
#: ``Adoption_Barriers`` (comprehensive / leader) -- against the same
#: ``_CURATED_AB_DETAIL_ALL`` projection that ``AB_Detail_All`` already
#: uses.  Pre-Round-49 only ``AB_Detail_All`` was in this mapping, so
#: the live renewal / compact xlsx leaked raw Snowflake headers
#: (``AB_COMPETITOR_C``, ``AB_SOLUTION_ATTEMPT_C``, ``CSDF_SYNC_ID_C``,
#: ``CSS_COMMENTS_C``, ``GS_C_360_SUCCESS_PRIORITY_C`` etc.) because the
#: friendly-label rename pass only touches columns present in
#: ``_FRIENDLY_HEADER_LABELS``, and those raw names were not in that
#: SSoT.  Routing the renewal / compact / leader AB sheets through the
#: curated allowlist drops the 230+ ungoverned columns down to the
#: ~60-column director-friendly set, AND because every ``_C`` column in
#: the curated set IS already in ``_FRIENDLY_HEADER_LABELS``, the
#: visible header row drops the raw ``_C`` suffix without any
#: per-column work in the writers.
#: Round 67 / Build 41 (B7): curated allowlist for the Compact /
#: comprehensive ``Action_Plans`` sheet. Pre-R67 the Compact XLSX
#: dumped the full 243-column Snowflake ``C360_CS_TASK_C_VW`` row
#: shape (every ``_C`` column the view exposes), making the sheet
#: unreadable for operators. The curated set keeps the ~30 columns
#: the Action-Plan operator workflow actually uses (ID + customer
#: identity, AP title / description, status / stage / priority,
#: dates, owner / assignee, and the ``_adoptiq_*`` provenance
#: marker columns from the R65/C-2 empty-fallback row).
_CURATED_ACTION_PLANS: tuple[str, ...] = (
    # Identity / customer
    "ID",
    "NAME",
    "BU_NAME",
    "DSM_BU_NAME",
    "customer_name",
    "ACCOUNT_ID_C",
    "ACCOUNT_MANAGER_C",
    # AP description
    "SUBJECT_C",
    "ACTION_PLAN_TITLE_C",
    "DESCRIPTION_C",
    "ACTION_C",
    "ACTION_TYPE_C",
    "ACTION_SUB_TYPE_C",
    # State / lifecycle
    "STATUS_C",
    "STAGE_C",
    "STATE_C",
    "PRIORITY_C",
    "AB_HOLD_REASON_C",
    "AB_WAITING_FOR_C",
    # Dates / age
    # Round 67 / B7: ``CREATED_DATE`` (the bare SF system column) is on
    # ``INTERNAL_COLUMN_DENYLIST`` because it duplicates the customer-
    # facing ``CREATED_DATE_C`` value while leaking the internal SF
    # audit timestamp. We carry only ``CREATED_DATE_C`` here so the
    # Round 15 SSoT validators (``test_phase_1_curated_*``) stay green.
    "OPEN_DATE_C",
    "CREATED_DATE_C",
    "DUE_DATE_C",
    "ORIGINAL_DUE_DATE_C",
    "CLOSED_DATE_C",
    "AGE_C",
    "DAYS_IN_STAGE_C",
    # Owner / assignee
    "OwnerId",
    "ASSIGNEE_C",
    "assignee_cssm_email",
    "NEXT_ACTION_C",
    "NEXT_STEP_C",
    "NEXT_ACTION_OWNER_C",
    "NEXT_ACTION_DUE_DATE_C",
    # Free-form context (kept last so it doesn't push action columns offscreen)
    "COMMENTS_C",
    "CURRENT_STATUS_AND_NOTES_C",
    "CLOSURE_COMMENTS_C",
    # Round 65 / C-2 provenance markers are intentionally NOT listed
    # here. They live on the global ``INTERNAL_COLUMN_DENYLIST`` and
    # the empty-fallback row construction in ``app_simple.py`` writes
    # them BEFORE the curated projection runs, so the
    # ``apply_export_schema`` call simply drops the marker columns
    # from the projected workbook -- the operator sees the action
    # plan rows as columns, and the empty-state provenance row
    # surfaces through the dedicated ``Report_Info`` sheet.
)

CURATED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "AB_Detail_All": _CURATED_AB_DETAIL_ALL,
    "Customer_Adoption_Barriers": _CURATED_AB_DETAIL_ALL,
    "All_Adoption_Barriers": _CURATED_AB_DETAIL_ALL,
    "Adoption_Barriers": _CURATED_AB_DETAIL_ALL,
    # Round 67 / Build 41 (B7): the Compact XLSX's
    # ``Critical_Adoption_Barriers`` sheet is an AB filter, so the
    # AB curation set is the right projection (drops 200+ raw
    # Snowflake columns down to ~60 customer-facing ones).
    "Critical_Adoption_Barriers": _CURATED_AB_DETAIL_ALL,
    "CSOne_Detail_All": _CURATED_CSONE_DETAIL_ALL,
    "External_Bugs": _CURATED_EXTERNAL_BUGS,
    "External_Incidents": _CURATED_EXTERNAL_INCIDENTS,
    "CSConsole_Customer_Pulse": _CURATED_CSCONSOLE_CUSTOMER_PULSE,
    # Round 67 / Build 41 (B7): Compact + comprehensive
    # ``Action_Plans`` curation (drops 243-col Snowflake dump down
    # to ~30 customer-facing columns).
    "Action_Plans": _CURATED_ACTION_PLANS,
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
    """Apply renames, denylist, curation, and friendly-label polish to ``df``.

    Returns a new DataFrame with the customer-facing column projection.
    Falls back to a best-effort no-op when ``df`` doesn't expose the
    pandas DataFrame interface (e.g. tests passing plain lists/dicts).

    Order of operations:

    1. Per-sheet ``SHEET_HEADER_RENAMES`` -- e.g. CSOne SOQL relationship
       label collapse.
    2. ``filter_columns`` -- denylist / prefix-list filter, plus
       per-sheet curated allowlist when defined.
    3. Round 45 / Phase 5: ``_FRIENDLY_HEADER_LABELS`` post-projection
       rename so visible headers drop ``_C`` / ``__C`` / ``BU_NAME`` /
       ``customer_name`` raw Snowflake names.  Applied LAST so the
       curated allowlists (which use raw names matching the upstream
       DataFrames) keep working unchanged.
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
    out = out.loc[:, target]

    # Round 45 / Phase 5: post-projection friendly-header rename.
    # Cross-sheet SSoT in ``_FRIENDLY_HEADER_LABELS`` -- only renames
    # columns whose source name is present, and skips any rename that
    # would collide with an already-friendly column name in ``out``
    # (so a workbook that already has both ``BU_NAME`` and
    # ``Customer Name`` keeps the original ``Customer Name`` and only
    # renames the raw side).
    friendly_map: dict[str, str] = {}
    existing = set(out.columns)
    for raw, friendly in _FRIENDLY_HEADER_LABELS.items():
        if raw in existing and friendly not in existing:
            friendly_map[raw] = friendly
    if friendly_map:
        out = out.rename(columns=friendly_map)

    # Round 45 / Phase 8 + 9: scrub Markdown chrome and literal None
    # cells from the named free-form text columns.  Runs AFTER the
    # friendly-header rename so the SSoT key set matches what's in the
    # workbook.  ``_BODY_TEXT_COLUMNS_FRIENDLY`` is conservative -- it
    # never touches categorical / numeric columns where the literal
    # string "None" might be a real value.
    try:
        # ``out`` is a fresh projection (loc[:, target] returns a view
        # when target is a Python list, which it always is here -- but
        # pandas is moving toward .copy() returning a CoW frame).
        # Defensive .copy() ensures the cleanup mutation never raises
        # the SettingWithCopyWarning regardless of pandas version.
        out = out.copy()
        _r45_clean_body_columns(out, _BODY_TEXT_COLUMNS_FRIENDLY)
    except Exception:  # noqa: BLE001
        # Never let cleanup break the writer; pre-Round-45 behavior
        # was to ship the chrome through unchanged.
        pass

    return out


__all__ = [
    "INTERNAL_COLUMN_DENYLIST",
    "INTERNAL_COLUMN_PREFIXES",
    "CURATED_COLUMNS",
    "SHEET_HEADER_RENAMES",
    "is_internal_column",
    "filter_columns",
    "apply_export_schema",
    "friendly_header",
]
