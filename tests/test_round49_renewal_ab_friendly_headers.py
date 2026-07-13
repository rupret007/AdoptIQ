"""Round 49 / F-RP-AB-RAW-HEADERS regression tests.

Build25 audit caught the renewal portfolio
``Customer_Adoption_Barriers`` Excel sheet emitting raw Snowflake
``_C`` headers (``AB_COMPETITOR_C``, ``AB_SOLUTION_ATTEMPT_C``,
``CSDF_SYNC_ID_C``, ``CSS_COMMENTS_C``, ``CX_TASK_ID_C``,
``CREATED_DATE_C``, ``DISPLAY_ORDER_C``, ``DISPUTE_TELEMETRY_C``,
``EXIT_CRITERIA_NAME_C``, ``GS_C_360_SUCCESS_PRIORITY_C``,
``GS_CASE_C``, ``GS_CONTACT_C``, ``GS_SOLUTION_C``,
``GS_USE_CASE_C``, ``IS_ASSIGNEE_C`` etc.) -- 259 columns total
with most ungoverned -- because the writer routed through
``apply_export_schema`` but no curated allowlist was registered
for the ``Customer_Adoption_Barriers`` sheet name (only the
comprehensive ``AB_Detail_All`` sheet had one).

Round 49 fix registers ``Customer_Adoption_Barriers`` /
``All_Adoption_Barriers`` / ``Adoption_Barriers`` against the same
``_CURATED_AB_DETAIL_ALL`` projection that ``AB_Detail_All`` uses, so:

* the curated allowlist drops the 200+ ungoverned ``_C`` columns,
* the post-projection friendly-label pass renames the surviving
  ``_C`` columns (``AB_STATUS_C`` -> "Adoption Barrier Status",
  ``ACCOUNT_ID_C`` -> "Account ID", etc.) so the visible header row
  shows zero ``_C`` suffixes.

Tests pin both layers (curated allowlist + friendly-label rename)
plus the renewal-shape end-to-end check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from report_export_schema import (  # noqa: E402
    CURATED_COLUMNS,
    _CURATED_AB_DETAIL_ALL,
    _FRIENDLY_HEADER_LABELS,
    apply_export_schema,
)


# ---------------------------------------------------------------------------
# Layer 1 -- curated mapping registration
# ---------------------------------------------------------------------------


def test_renewal_customer_adoption_barriers_registered_in_curated_columns() -> None:
    """The renewal portfolio writes a ``Customer_Adoption_Barriers``
    sheet (vs the comprehensive ``AB_Detail_All``).  The sheet name
    MUST be in ``CURATED_COLUMNS`` so the projection drops raw ``_C``
    columns that are not in the allowlist."""
    assert "Customer_Adoption_Barriers" in CURATED_COLUMNS, (
        "Round 49 / F-RP-AB-RAW-HEADERS regression: renewal AB sheet "
        "name not registered in CURATED_COLUMNS -- raw _C headers will "
        "leak into the customer-facing xlsx."
    )
    assert CURATED_COLUMNS["Customer_Adoption_Barriers"] is _CURATED_AB_DETAIL_ALL, (
        "Round 49 / F-RP-AB-RAW-HEADERS regression: renewal AB sheet "
        "MUST share the SSoT curated set with AB_Detail_All so a single "
        "curation tweak applies everywhere."
    )


def test_compact_all_adoption_barriers_registered_in_curated_columns() -> None:
    """Compact xlsx writes ``All_Adoption_Barriers`` -- same shape,
    same curation."""
    assert "All_Adoption_Barriers" in CURATED_COLUMNS, (
        "Round 49 / F-RP-AB-RAW-HEADERS regression: compact AB sheet "
        "name not registered in CURATED_COLUMNS."
    )
    assert CURATED_COLUMNS["All_Adoption_Barriers"] is _CURATED_AB_DETAIL_ALL


def test_leader_comprehensive_adoption_barriers_registered_in_curated_columns() -> None:
    """Leader / comprehensive xlsx writes ``Adoption_Barriers``."""
    assert "Adoption_Barriers" in CURATED_COLUMNS, (
        "Round 49 / F-RP-AB-RAW-HEADERS regression: leader/comprehensive "
        "AB sheet name not registered in CURATED_COLUMNS."
    )
    assert CURATED_COLUMNS["Adoption_Barriers"] is _CURATED_AB_DETAIL_ALL


# ---------------------------------------------------------------------------
# Layer 2 -- curated set must NOT include the audit-flagged raw _C names
# ---------------------------------------------------------------------------


_AUDIT_FLAGGED_RAW_HEADERS = (
    "AB_COMPETITOR_C",
    "AB_SOLUTION_ATTEMPT_C",
    "CSDF_SYNC_ID_C",
    "CSS_COMMENTS_C",
    "CSS_PRE_UNLINK_TECHNOLOGY_NAME_C",
    "CX_TASK_ID_C",
    "CREATED_DATE_C",
    "DISPLAY_ORDER_C",
    "DISPUTE_TELEMETRY_C",
    "EXIT_CRITERIA_NAME_C",
    "GS_C_360_SUCCESS_PRIORITY_C",
    "GS_CASE_C",
    "GS_CONTACT_C",
    "GS_SOLUTION_C",
    "GS_USE_CASE_C",
    "IS_ASSIGNEE_C",
)


def test_curated_set_excludes_audit_flagged_raw_columns() -> None:
    """The curated allowlist MUST NOT include any of the audit-flagged
    ``_C`` columns -- they are internal Salesforce plumbing that
    customers don't read.  Pinning here so a future "add column"
    audit accidentally re-introducing one of these names triggers a
    clear regression."""
    for raw in _AUDIT_FLAGGED_RAW_HEADERS:
        assert raw not in _CURATED_AB_DETAIL_ALL, (
            f"Round 49 / F-RP-AB-RAW-HEADERS regression: raw header {raw!r} "
            f"is in _CURATED_AB_DETAIL_ALL.  These are the columns we are "
            f"explicitly trying to drop."
        )


# ---------------------------------------------------------------------------
# Layer 3 -- end-to-end renewal-shape exercise: simulate live xlsx schema
# ---------------------------------------------------------------------------


def _build_live_renewal_ab_frame() -> pd.DataFrame:
    """Mimic the 259-column-wide renewal AB DataFrame the live writer
    receives -- a mix of the curated set, friendly-label-mapped raw
    columns, and the audit-flagged raw leak set."""
    sample = {
        # Identity (curated)
        "ID": ["aGte01"],
        "NAME": ["Data Privacy Concerns"],
        "BU_NAME": ["Acme Corp"],
        "ACCOUNT_ID_C": ["0013400001Y4HuyAAF"],
        "ACCOUNT_MANAGER_C": ["jdoe@cisco.com"],
        "ASSIGNEE_C": ["assignee@cisco.com"],
        # Status (curated, friendly-labelled)
        "AB_STATUS_C": ["Open"],
        "STATUS_C": ["Open"],
        "SEVERITY_C": ["P2"],
        "PRIORITY_C": ["High"],
        "AB_ESCALATE_C": [False],
        "AB_HOLD_REASON_C": [None],
        "AB_PARTNER_ISSUE_C": [None],
        "AB_WAITING_FOR_C": [None],
        "AB_WAITING_FOR_DETAIL_C": [None],
        # Description (curated)
        "SUBJECT_C": ["Data privacy"],
        "DESCRIPTION_C": ["Customer concern."],
        "ADOPTION_BARRIER_TYPE_C": ["Technical"],
        # Dates (curated)
        "OPEN_DATE_C": ["2026-01-15"],
        "DUE_DATE_C": ["2026-02-15"],
        "CLOSED_DATE_C": [None],
        # Comments (curated)
        "COMMENTS_C": ["See SE notes."],
        # === Audit-flagged raw _C columns that MUST be filtered out ===
        "AB_COMPETITOR_C": [None],
        "AB_SOLUTION_ATTEMPT_C": [None],
        "CSDF_SYNC_ID_C": [None],
        "CSS_COMMENTS_C": [None],
        "CSS_PRE_UNLINK_TECHNOLOGY_NAME_C": [None],
        "CX_TASK_ID_C": [None],
        "CREATED_DATE_C": ["2026-01-15"],
        "DISPLAY_ORDER_C": [0],
        "DISPUTE_TELEMETRY_C": [None],
        "EXIT_CRITERIA_NAME_C": [None],
        "GS_C_360_SUCCESS_PRIORITY_C": [None],
        "GS_CASE_C": [None],
        "GS_CONTACT_C": [None],
        "GS_SOLUTION_C": [None],
        "GS_USE_CASE_C": [None],
        "IS_ASSIGNEE_C": [False],
    }
    return pd.DataFrame(sample)


def test_renewal_ab_sheet_drops_audit_flagged_raw_headers() -> None:
    """Round 49 acceptance criterion: the renewal AB xlsx header row
    must contain ZERO ``_C``-suffixed column names from the
    audit-flagged set."""
    df = _build_live_renewal_ab_frame()
    out = apply_export_schema(df, sheet_name="Customer_Adoption_Barriers")
    cols = list(out.columns)

    for raw in _AUDIT_FLAGGED_RAW_HEADERS:
        assert raw not in cols, (
            f"Round 49 / F-RP-AB-RAW-HEADERS regression: header {raw!r} "
            f"still leaks into the renewal Customer_Adoption_Barriers "
            f"sheet.  Visible columns: {cols!r}"
        )


def test_renewal_ab_sheet_emits_friendly_labels() -> None:
    """Round 49 acceptance: the surviving columns MUST appear under
    their friendly-label form -- ``Customer Name`` (not ``BU_NAME``),
    ``Account ID`` (not ``ACCOUNT_ID_C``), ``Adoption Barrier Status``
    (not ``AB_STATUS_C``), etc."""
    df = _build_live_renewal_ab_frame()
    out = apply_export_schema(df, sheet_name="Customer_Adoption_Barriers")
    cols = list(out.columns)

    expected_friendly = (
        "Customer Name",
        "Account ID",
        "Adoption Barrier Status",
        "Severity",
        "Open Date",
        "Comments",
    )
    for friendly in expected_friendly:
        assert friendly in cols, (
            f"Round 49 / F-RP-AB-RAW-HEADERS regression: expected friendly "
            f"header {friendly!r} not present.  Visible columns: {cols!r}"
        )


def test_compact_all_ab_sheet_drops_audit_flagged_raw_headers() -> None:
    """Same projection on the compact ``All_Adoption_Barriers`` sheet."""
    df = _build_live_renewal_ab_frame()
    out = apply_export_schema(df, sheet_name="All_Adoption_Barriers")
    cols = list(out.columns)

    for raw in _AUDIT_FLAGGED_RAW_HEADERS:
        assert raw not in cols


def test_no_underscore_C_suffix_remains_after_projection() -> None:
    """Stronger sweep: no surviving column may end with ``_C`` (raw
    Salesforce single-suffix marker) or ``__C`` (Snowflake double).
    Pre-Round-49 the renewal AB sheet had ~50 such columns."""
    df = _build_live_renewal_ab_frame()
    out = apply_export_schema(df, sheet_name="Customer_Adoption_Barriers")
    cols = list(out.columns)

    leaks = [c for c in cols if isinstance(c, str) and (c.endswith("_C") or c.endswith("__C"))]
    assert leaks == [], (
        f"Round 49 / F-RP-AB-RAW-HEADERS regression: {len(leaks)} columns "
        f"still end with raw _C / __C suffix: {leaks!r}"
    )


# ---------------------------------------------------------------------------
# Layer 4 -- friendly-label SSoT covers the renewal AB curated columns
# ---------------------------------------------------------------------------


_RENEWAL_AB_CURATED_C_COLUMNS = (
    "ACCOUNT_ID_C",
    "ACCOUNT_MANAGER_C",
    "ASSIGNEE_C",
    "AB_STATUS_C",
    "STATUS_C",
    "SEVERITY_C",
    "PRIORITY_C",
    "AB_ESCALATE_C",
    "AB_HOLD_REASON_C",
    "AB_PARTNER_ISSUE_C",
    "AB_WAITING_FOR_C",
    "AB_WAITING_FOR_DETAIL_C",
    "SUBJECT_C",
    "DESCRIPTION_C",
    "ADOPTION_BARRIER_TYPE_C",
    "OPEN_DATE_C",
    "DUE_DATE_C",
    "CLOSED_DATE_C",
    "COMMENTS_C",
)


def test_curated_C_columns_have_friendly_labels() -> None:
    """Every ``_C`` column the renewal AB curated set keeps MUST have
    a friendly-label entry, otherwise it would survive the projection
    but still appear as a raw header in the Excel sheet."""
    for raw in _RENEWAL_AB_CURATED_C_COLUMNS:
        assert raw in _FRIENDLY_HEADER_LABELS, (
            f"Round 49 / F-RP-AB-RAW-HEADERS regression: curated column "
            f"{raw!r} has no friendly-label mapping -- it would survive "
            f"projection but still display as a raw _C header."
        )


# ---------------------------------------------------------------------------
# Layer 5 -- wiring pin: live writer keeps using apply_export_schema
# ---------------------------------------------------------------------------


def test_renewal_excel_writer_routes_through_apply_export_schema() -> None:
    """Pin that the live renewal Excel writer in app_simple.py still
    routes its sheet writes through ``apply_export_schema`` -- if a
    future refactor bypasses the schema helper the registration above
    becomes inert."""
    src = (_REPO_ROOT / "app_simple.py").read_text()
    assert "_r15_apply_export_schema(df_clean, sheet_name=sheet_name)" in src, (
        "Round 49 / F-RP-AB-RAW-HEADERS wiring pin: renewal Excel writer "
        "no longer routes sheet DataFrames through apply_export_schema. "
        "Adding the curated allowlist registration alone is not enough."
    )
