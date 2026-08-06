"""Round 75 / Phase 3 (B3) -- comprehensive AB scope drops empty-AM rows.

Build 47 acceptance audit caught one AB row leaking into Brian
Frazier's comprehensive ``AB_Detail_All`` sheet that the Leader path
correctly excluded for the same scope:

- ID: ``aGte6000000pAi5CAE``
- Customer: ``CISCO SYSTEMS INC CA`` (an internal Cisco account)
- ``Account Manager`` (``ACCOUNT_MANAGER_C``): ``''`` (empty)
- ``assignee_cssm_email``: ``''`` (no creator attribution)

Root cause: when an internal account ends up in ``team_subs_df`` via
a stale subscription record, ``account_ids`` includes it and
``fetch_adoption_barriers(ctx, account_ids, days)`` pulls every AB
for the account.  The Leader path then runs each per-CSSM frame
through ``_slice_by_owner_or_account``, which naturally drops
barriers neither created by a team CSSM nor on a CSSM-owned account.
The Comprehensive path has no equivalent gate, so the row landed in
the operator-facing report.

R75/B3 fix: in ``run_comprehensive_analysis``, immediately after
``ab_norm = _prepare_ab(ab_scoped, team_subs_df)``, drop rows where
BOTH ``ACCOUNT_MANAGER_C`` AND ``assignee_cssm_email`` are
empty/NaN.  Skip the gate entirely when ``manager_name == "All
Managers"`` (that report is intentionally cross-team).

These tests pin the source-shape contract (the gate exists with the
R75 marker AND the All-Managers short-circuit) and the behavioural
contract (empty-both drops; either-populated survives; All-Managers
no-op).

Round 75 / Phase 3 (B3).  Made-with: Cursor.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import re
from pathlib import Path

import numpy as np
import pandas as pd


_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"


# ---------------------------------------------------------------------------
# Source-shape pin: R75/B3 block exists with marker + short-circuit
# ---------------------------------------------------------------------------


def _b3_block() -> str:
    """Return the source block around the R75/B3 fix."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    idx = src.find("Round 75 / B3")
    assert idx != -1, "Round 75 / B3 marker missing from app_simple.py"
    return src[max(0, idx - 200): idx + 4500]


def test_b3_marker_is_present_in_app_simple():
    """``# Round 75 / B3`` MUST appear in app_simple.py so a future
    grep can map the change back to this round."""
    block = _b3_block()
    assert_in_source(block, "Round 75 / B3", label='block')


def test_b3_gate_short_circuits_for_all_managers_scope():
    """When ``manager_name == 'All Managers'``, the defensive filter
    MUST NOT run -- that report intentionally surfaces every AB
    across teams."""
    block = _b3_block()
    # The gate must be a literal manager_name check, not a derived predicate
    # that could subtly diverge.
    assert re.search(
        r'manager_name\s+and\s+manager_name\s*!=\s*"All Managers"',
        block,
    ) is not None, (
        "R75/B3 gate must short-circuit when manager_name == 'All Managers'; "
        "expected literal `manager_name and manager_name != \"All Managers\"`. "
        "Check the conditional that wraps the defensive scope filter."
    )


def test_b3_gate_requires_both_columns_blank():
    """The drop predicate MUST require BOTH ``ACCOUNT_MANAGER_C`` AND
    ``assignee_cssm_email`` to be blank.  A row with EITHER populated
    is preserved (defensive: better to keep an arguably-in-scope row
    than to silently drop a real barrier)."""
    block = _b3_block()
    # The combined drop mask must AND the two blank-check series.
    assert re.search(
        r"drop_mask\s*=\s*am_blank\s*&\s*cssm_blank",
        block,
    ) is not None, (
        "R75/B3: drop_mask must combine BOTH am_blank AND cssm_blank "
        "(not OR) so a row with EITHER attribution column survives."
    )


def test_b3_logs_dropped_count_and_sample_ids():
    """The fix MUST log the dropped row count + sample IDs so the
    operator can see what was excluded without needing to inspect
    the workbook."""
    block = _b3_block()
    assert "comprehensive AB scope filter for" in block, (
        "R75/B3 log message text drifted; if you change the wording, "
        "update both source and test."
    )
    assert "Sample dropped IDs:" in block, (
        "R75/B3 must log a sample of dropped barrier IDs (capped at 5) "
        "so the operator can verify the gate removed the expected rows."
    )


# ---------------------------------------------------------------------------
# Behavioural pin: synthetic AB frame + the R75/B3 predicate
# ---------------------------------------------------------------------------


def _r75_b3_is_blank(value: object) -> bool:
    """Mirror of the source helper -- kept here for behavioural testing."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    token = str(value).strip().lower()
    return token in {"", "nan", "none", "null"}


def _apply_b3_filter(ab_norm: pd.DataFrame, manager_name: str) -> pd.DataFrame:
    """Inline the R75/B3 source contract for behavioural test."""
    if not (manager_name and manager_name != "All Managers"):
        return ab_norm
    if ab_norm is None or ab_norm.empty:
        return ab_norm
    am_col = ab_norm.get(
        "ACCOUNT_MANAGER_C",
        pd.Series([""] * len(ab_norm), index=ab_norm.index),
    )
    cssm_col = ab_norm.get(
        "assignee_cssm_email",
        pd.Series([""] * len(ab_norm), index=ab_norm.index),
    )
    am_blank = am_col.apply(_r75_b3_is_blank)
    cssm_blank = cssm_col.apply(_r75_b3_is_blank)
    drop_mask = am_blank & cssm_blank
    return ab_norm[~drop_mask].reset_index(drop=True)


def test_b3_drops_the_audit_canonical_leak_row():
    """The exact row from the Build 47 audit (CISCO SYSTEMS INC CA,
    empty AM, empty cssm_email) MUST be dropped for a Brian Frazier
    scoped report."""
    ab_norm = pd.DataFrame([
        {
            "ID": "aGte6000000pAi5CAE",
            "customer_name": "CISCO SYSTEMS INC CA",
            "ACCOUNT_MANAGER_C": "",
            "assignee_cssm_email": "",
            "STATUS_C": "Open",
        },
        {
            "ID": "AB-002",
            "customer_name": "Acme Corp",
            "ACCOUNT_MANAGER_C": "Brian Frazier",
            "assignee_cssm_email": "alice@cisco.com",
            "STATUS_C": "Open",
        },
        {
            "ID": "AB-003",
            "customer_name": "Beta Inc",
            "ACCOUNT_MANAGER_C": "",
            "assignee_cssm_email": "bob@cisco.com",  # creator attribution
            "STATUS_C": "Open",
        },
    ])
    filtered = _apply_b3_filter(ab_norm, manager_name="Brian Frazier")
    surviving_ids = set(filtered["ID"])
    assert "aGte6000000pAi5CAE" not in surviving_ids, (
        "R75/B3 contract: the audit-canonical CISCO leak row MUST be "
        "dropped from a manager-scoped comprehensive AB frame."
    )
    assert surviving_ids == {"AB-002", "AB-003"}, surviving_ids


def test_b3_preserves_row_with_only_account_manager_populated():
    """A row with ACCOUNT_MANAGER_C populated but blank
    assignee_cssm_email MUST survive (defensive: account-manager
    attribution is sufficient)."""
    ab_norm = pd.DataFrame([
        {
            "ID": "AB-001",
            "ACCOUNT_MANAGER_C": "Brian Frazier",
            "assignee_cssm_email": "",
            "STATUS_C": "Open",
        },
    ])
    filtered = _apply_b3_filter(ab_norm, manager_name="Brian Frazier")
    assert len(filtered) == 1, (
        "R75/B3: row with populated ACCOUNT_MANAGER_C must survive "
        "even if assignee_cssm_email is blank."
    )


def test_b3_preserves_row_with_only_assignee_cssm_email_populated():
    """A row with assignee_cssm_email populated but blank
    ACCOUNT_MANAGER_C MUST survive (defensive: creator attribution
    is sufficient -- this is the leader's _slice_by_owner_or_account
    semantics)."""
    ab_norm = pd.DataFrame([
        {
            "ID": "AB-001",
            "ACCOUNT_MANAGER_C": "",
            "assignee_cssm_email": "alice@cisco.com",
            "STATUS_C": "Open",
        },
    ])
    filtered = _apply_b3_filter(ab_norm, manager_name="Brian Frazier")
    assert len(filtered) == 1, (
        "R75/B3: row with populated assignee_cssm_email must survive "
        "even if ACCOUNT_MANAGER_C is blank."
    )


def test_b3_short_circuits_for_all_managers_scope():
    """When ``manager_name == 'All Managers'``, the gate is a no-op:
    the audit-canonical leak row MUST survive (the All-Managers
    report is intentionally cross-team)."""
    ab_norm = pd.DataFrame([
        {
            "ID": "aGte6000000pAi5CAE",
            "ACCOUNT_MANAGER_C": "",
            "assignee_cssm_email": "",
            "STATUS_C": "Open",
        },
    ])
    filtered = _apply_b3_filter(ab_norm, manager_name="All Managers")
    assert len(filtered) == 1, (
        "R75/B3: the All-Managers scope MUST short-circuit the gate; "
        "all rows must survive even if both attribution columns are blank."
    )


def test_b3_treats_nan_and_string_variants_as_blank():
    """NaN, ``None``, the literal strings ``'nan'`` / ``'None'`` /
    ``'null'``, and pure whitespace MUST all be treated as blank by
    the gate -- otherwise a CSConsole export with ``'NaN'`` literal
    strings (a known pandas serialization edge case) will leak."""
    ab_norm = pd.DataFrame([
        {"ID": "AB-1", "ACCOUNT_MANAGER_C": np.nan,  "assignee_cssm_email": np.nan},
        {"ID": "AB-2", "ACCOUNT_MANAGER_C": "nan",   "assignee_cssm_email": "nan"},
        {"ID": "AB-3", "ACCOUNT_MANAGER_C": "None",  "assignee_cssm_email": "None"},
        {"ID": "AB-4", "ACCOUNT_MANAGER_C": "null",  "assignee_cssm_email": "null"},
        {"ID": "AB-5", "ACCOUNT_MANAGER_C": "   ",   "assignee_cssm_email": "   "},
    ])
    filtered = _apply_b3_filter(ab_norm, manager_name="Brian Frazier")
    assert len(filtered) == 0, (
        f"R75/B3: NaN / 'nan' / 'None' / 'null' / whitespace MUST all "
        f"count as blank for the attribution gate; got {len(filtered)} "
        f"surviving rows: {filtered['ID'].tolist()}."
    )
