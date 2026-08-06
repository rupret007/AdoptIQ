"""Round 118 / Build 87 -- ACC customer-count secondary-attribution fix.

The live DSM table ``CX_DB.CX_SWSSBST_BR.dsm_assignment_data`` attributes
secondary owners via FIVE numbered slots -- ``DSM_EMAIL1`` .. ``DSM_EMAIL5``
(paired with ``DSM_ASSIGNED_1..5`` / ``DSM_ID1..5``). Pre-R118,
``adoptiq_backend._R82_SECONDARY_DSM_EMAIL_CANDIDATES`` named none of these,
so ``get_subscriptions_for_team`` matched ONLY ``PRIMARY_DSM_EMAIL`` and a
Brian Frazier CSSM who owned an account through slot 2-5 was silently
dropped. The live ACC Comprehensive customer count regressed to 24
(Build 86) with ``team_subs_diag.secondary_rows == 0``.

Two bugs are pinned here:

1. The candidate tuple now includes ``DSM_EMAIL1`` .. ``DSM_EMAIL5`` so the
   R82 UNION fans out to the real secondary slots -- a secondary-only
   customer is counted and ``secondary_rows`` is > 0.
2. ``introspect_dsm_columns``'s ``all_email_like_columns`` escape-hatch
   filter was widened from a ``*_EMAIL`` suffix match (which MISSED the
   digit-suffixed ``DSM_EMAILn`` columns -- exactly why this gap survived
   R82) to any column whose name contains ``EMAIL``.

The contract is live-verified: the secondary columns were discovered by
running ``scripts/r118_dump_dsm_columns.py`` against the live Snowflake DSM
table on VPN.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent.parent

_R118_LIVE_SECONDARY_SLOTS = (
    "DSM_EMAIL1",
    "DSM_EMAIL2",
    "DSM_EMAIL3",
    "DSM_EMAIL4",
    "DSM_EMAIL5",
)


# ---------------------------------------------------------------------------
# Candidate-tuple SSoT shape
# ---------------------------------------------------------------------------


def test_r118_secondary_candidates_include_live_dsm_email_slots():
    """All five live secondary owner-email slots MUST be present in the
    candidate tuple so the R82 UNION query fans out to them."""
    from adoptiq_backend import _R82_SECONDARY_DSM_EMAIL_CANDIDATES

    for slot in _R118_LIVE_SECONDARY_SLOTS:
        assert slot in _R82_SECONDARY_DSM_EMAIL_CANDIDATES, (
            f"{slot} (a live DSM secondary owner column) missing from "
            "_R82_SECONDARY_DSM_EMAIL_CANDIDATES -- the ACC=24 regression "
            "will recur"
        )


def test_r118_secondary_slots_disjoint_from_primary():
    """The new slots MUST NOT collide with the primary set (UNION dedup
    keys on CSSM_EMAIL, but disjoint columns keep the contract simple and
    prevent double-querying the same physical column)."""
    from adoptiq_backend import (
        _R82_PRIMARY_DSM_EMAIL_COLUMNS,
        _R82_SECONDARY_DSM_EMAIL_CANDIDATES,
    )

    primary = set(_R82_PRIMARY_DSM_EMAIL_COLUMNS)
    secondary = set(_R82_SECONDARY_DSM_EMAIL_CANDIDATES)
    assert primary.isdisjoint(secondary)
    for slot in _R118_LIVE_SECONDARY_SLOTS:
        assert slot not in primary


def test_r118_next_action_owner_email_is_NOT_a_candidate():
    """``NEXT_ACTION_OWNER_EMAIL`` is the next-action owner, NOT an
    ownership attribution. Matching it would over-attribute subscriptions,
    so it MUST stay out of the candidate tuple even though it is an
    email-like column on the live table."""
    from adoptiq_backend import _R82_SECONDARY_DSM_EMAIL_CANDIDATES

    assert "NEXT_ACTION_OWNER_EMAIL" not in _R82_SECONDARY_DSM_EMAIL_CANDIDATES


def test_r118_preserves_r82_pre_existing_candidates():
    """R118 EXTENDS the tuple; it MUST NOT drop the conservative R82
    naming patterns other environments may rely on."""
    from adoptiq_backend import _R82_SECONDARY_DSM_EMAIL_CANDIDATES

    for legacy in (
        "SECONDARY_DSM_EMAIL",
        "BACKUP_DSM_EMAIL",
        "DELEGATE_DSM_EMAIL",
        "OWNER_EMAIL_2",
    ):
        assert legacy in _R82_SECONDARY_DSM_EMAIL_CANDIDATES


# ---------------------------------------------------------------------------
# Runtime behavior: secondary-only customer is counted
# ---------------------------------------------------------------------------


def _build_mock_cursor_for_columns(rows_per_query: dict):
    """Mock cursor that returns different rows depending on which email
    column appears in the SQL (mirrors the R82 test harness)."""
    cursor = MagicMock()
    captured_sqls = []
    descr = (
        ("SUBSCRIPTION_ID",),
        ("ACCOUNT_ID_C",),
        ("BU_NAME",),
        ("CSSM_EMAIL",),
    )

    def _execute(sql, params):
        captured_sqls.append((sql, list(params)))
        for col, rows in rows_per_query.items():
            if f" {col} IN " in sql:
                cursor._next_rows = rows
                cursor._next_descr = descr
                return cursor
        cursor._next_rows = []
        cursor._next_descr = descr
        return cursor

    cursor.execute = _execute
    cursor.fetchall = lambda: getattr(cursor, "_next_rows", [])
    cursor.captured_sqls = captured_sqls
    type(cursor).description = property(  # type: ignore
        lambda self: getattr(self, "_next_descr", descr)
    )
    return cursor


def test_r118_secondary_only_customer_is_counted_via_dsm_email2():
    """THE regression: a customer Brian's CSSM owns ONLY via ``DSM_EMAIL2``
    (not the denormalized ``PRIMARY_DSM_EMAIL``) MUST appear in the merged
    subscription frame, and the diag MUST report ``secondary_rows > 0``."""
    import adoptiq_backend
    from adoptiq_backend import get_subscriptions_for_team

    rows_per_query = {
        "PRIMARY_DSM_EMAIL": [
            ("SUB001", "ACC001", "Primary Customer", "alice@cisco.com"),
        ],
        # Brian's CSSM owns this account only via a secondary slot.
        "DSM_EMAIL2": [
            ("SUB099", "ACC099", "Secondary-Only Customer", "alice@cisco.com"),
        ],
    }
    fake_ctx = MagicMock()
    fake_ctx.cursor.return_value = _build_mock_cursor_for_columns(rows_per_query)

    # Mirror the live schema: primary + all five numbered DSM slots present.
    fake_columns = {
        "PRIMARY_DSM_EMAIL",
        "DSM_EMAIL1",
        "DSM_EMAIL2",
        "DSM_EMAIL3",
        "DSM_EMAIL4",
        "DSM_EMAIL5",
        "SUBSCRIPTION_ID",
        "ACCOUNT_ID_C",
        "BU_NAME",
    }

    with patch.object(adoptiq_backend, "is_table_blocked", return_value=False), \
         patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        df = get_subscriptions_for_team(fake_ctx, ["alice@cisco.com"])

    assert isinstance(df, pd.DataFrame)
    assert "Secondary-Only Customer" in set(df["BU_NAME"]), (
        "The secondary-only customer (DSM_EMAIL2) was dropped -- the "
        "ACC=24 regression has recurred"
    )
    assert "Primary Customer" in set(df["BU_NAME"])

    diag = df.attrs.get("_r82_team_subs_diag")
    assert isinstance(diag, dict)
    assert diag["secondary_rows"] >= 1, "secondary_rows MUST be > 0 once a slot matches"
    assert "DSM_EMAIL2" in diag["secondary_email_columns_used"]
    # All five present slots MUST be queried.
    for slot in _R118_LIVE_SECONDARY_SLOTS:
        assert slot in diag["secondary_email_columns_used"]


def test_r118_all_five_slots_queried_with_parameterized_sql():
    """Each present secondary slot MUST be queried with %s placeholders
    (no string-concat injection surface)."""
    import adoptiq_backend
    from adoptiq_backend import get_subscriptions_for_team

    fake_ctx = MagicMock()
    fake_cursor = _build_mock_cursor_for_columns({"PRIMARY_DSM_EMAIL": []})
    fake_ctx.cursor.return_value = fake_cursor
    fake_columns = {"PRIMARY_DSM_EMAIL", *_R118_LIVE_SECONDARY_SLOTS}

    with patch.object(adoptiq_backend, "is_table_blocked", return_value=False), \
         patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        get_subscriptions_for_team(fake_ctx, ["bob@cisco.com"])

    queried_cols = {
        slot for slot in _R118_LIVE_SECONDARY_SLOTS
        if any(f" {slot} IN " in sql for sql, _ in fake_cursor.captured_sqls)
    }
    assert queried_cols == set(_R118_LIVE_SECONDARY_SLOTS)
    for sql, params in fake_cursor.captured_sqls:
        assert "%s" in sql
        assert "bob@cisco.com" in params


# ---------------------------------------------------------------------------
# Diagnostic-filter widening: digit-suffixed email columns surface
# ---------------------------------------------------------------------------


def test_r118_introspect_surfaces_digit_suffixed_email_columns():
    """The widened ``all_email_like_columns`` filter MUST surface the
    digit-suffixed ``DSM_EMAILn`` columns that the pre-R118 ``*_EMAIL``
    suffix match silently skipped -- this is the escape hatch that should
    have caught the gap before a live build."""
    import adoptiq_backend
    from adoptiq_backend import introspect_dsm_columns

    fake_columns = {
        "PRIMARY_DSM_EMAIL",
        "DSM_EMAIL1",
        "DSM_EMAIL2",
        "DSM_EMAIL3",
        "DSM_EMAIL4",
        "DSM_EMAIL5",
        "NEXT_ACTION_OWNER_EMAIL",
        "SUBSCRIPTION_ID",
        "BU_NAME",
    }
    fake_ctx = MagicMock()
    with patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        result = introspect_dsm_columns(fake_ctx)

    assert result["ok"] is True
    surfaced = set(result["all_email_like_columns"])
    for slot in _R118_LIVE_SECONDARY_SLOTS:
        assert slot in surfaced, (
            f"{slot} MUST surface in all_email_like_columns after the R118 "
            "filter widening"
        )
    assert "PRIMARY_DSM_EMAIL" in surfaced
    assert "NEXT_ACTION_OWNER_EMAIL" in surfaced
    # Non-email columns still excluded.
    assert "SUBSCRIPTION_ID" not in surfaced
    assert "BU_NAME" not in surfaced


def test_r118_introspect_secondary_candidates_present_now_matches_slots():
    """With the slots in the candidate tuple, a live-shaped column set MUST
    classify them as secondary candidates present."""
    import adoptiq_backend
    from adoptiq_backend import introspect_dsm_columns

    fake_columns = {"PRIMARY_DSM_EMAIL", *_R118_LIVE_SECONDARY_SLOTS, "SUBSCRIPTION_ID"}
    fake_ctx = MagicMock()
    with patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        result = introspect_dsm_columns(fake_ctx)

    present = set(result["secondary_email_candidates_present"])
    assert set(_R118_LIVE_SECONDARY_SLOTS).issubset(present)


# ---------------------------------------------------------------------------
# Source markers
# ---------------------------------------------------------------------------


def test_round118_source_markers_present_in_adoptiq_backend():
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 118 / Build 87", label='src')
    assert_in_source(src, "DSM_EMAIL1", label='src')
    assert_in_source(src, "DSM_EMAIL5", label='src')
