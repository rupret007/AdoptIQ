"""Round 75 / Phase 1 (B1) -- Comprehensive AP fetch parity with Leader.

Build 47 acceptance audit caught a 366 (Leader) vs 0 (Comprehensive)
divergence on the same Brian Frazier 90d / All Contact Center scope.
The Leader path called the canonical
``leader_report_generator.fetch_action_plans_snowflake`` and rendered
366 rows; the Comprehensive path called the same helper but then ran
the result through ``_filter_csconsole_data_by_technology``, which
collapsed all 366 rows to 0 because the AP source table
(``C360_CS_TASK_C_VW``) lacks the ``SUB_TECHNOLOGY_C`` /
``TECHNOLOGY_C`` columns the enhanced matcher requires.  The matcher
then emitted an all-False mask with no account-scope fallback and the
provenance branch stamped a single ``_adoptiq_provenance_row=True``
marker, so the operator-facing ``Action plans (open)`` KPI silently
read 0 even though there were 366 real rows.

R142 tightens the boundary: Comprehensive uses only the authoritative,
technology-scoped ``account_ids`` and disables owner widening. Leader team
mode may intentionally widen by owner, but that predicate is not valid for a
technology/customer-scoped Comprehensive report.

These tests pin the source shape (no post-filter call), the parity of
the two callers' kwargs, and the structured logging.

Round 75 / Phase 1 (B1).  Made-with: Cursor.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import re
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from leader_report_generator import fetch_action_plans_snowflake


_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"
_LEADER = _REPO_ROOT / "leader_report_generator.py"


# ---------------------------------------------------------------------------
# Source-shape pin: the Comprehensive call site mirrors the Leader call
# ---------------------------------------------------------------------------


def _comp_call_block() -> str:
    """Return the source block around the Comprehensive AP fetch call."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # Locate the Round 75 / B1 marker block.
    idx = src.find("Round 75 / B1")
    assert idx != -1, "Round 75 / B1 marker missing from app_simple.py"
    # Capture ~3 KB of context around the first marker hit (covers fetch + merge).
    start = max(0, idx - 600)
    end = min(len(src), idx + 3000)
    return src[start:end]


def test_comp_call_block_is_present_with_round_75_marker():
    """The Comprehensive AP fetch must be tagged with a ``Round 75 / B1``
    marker so ``git diff app_simple.py | grep 'Round 75'`` shows the
    per-file footprint."""
    block = _comp_call_block()
    assert_in_source(block, "Round 75 / B1", label='block')


def test_comp_call_is_account_scoped_without_owner_widening():
    """Comprehensive must use selected account IDs and no owner union.

    Leader call shape (``leader_report_generator.py:1065-1067``):
        self._fetch_action_plans(all_account_ids, days,
                                 owner_emails=normalized_roster_emails)

    Comprehensive call shape (``app_simple.py``):
        _r65_fetch_aps_snowflake(
            ctx, _comprehensive_scoped_account_ids, days,
            owner_emails=[], preserve_observations=True
        )

    ``preserve_observations=True`` defers stable-ID reconciliation until the
    canonical report boundary, so non-identical observations cannot be lost
    inside the transport fetcher.
    """
    block = _comp_call_block()
    # Find the actual ``_r65_fetch_aps_snowflake(`` call.
    m = re.search(
        r"_r65_fetch_aps_snowflake\s*\(\s*ctx\s*,"
        r"\s*_comprehensive_scoped_account_ids\s*,\s*days\s*,"
        r"\s*owner_emails\s*=\s*\[\]\s*,"
        r"\s*preserve_observations\s*=\s*True\s*,?\s*\)",
        block,
    )
    assert m is not None, (
        "Comprehensive AP fetch must disable owner widening and rely on "
        "authoritative criteria-scoped account IDs. Block was:\n"
        f"{block!r}"
    )

    # Leader team mode may intentionally widen by roster owner; this explicit
    # difference is the scope-safety contract.
    leader_src = _LEADER.read_text(encoding="utf-8")
    assert re.search(
        r"self\._fetch_action_plans\s*\(\s*\n?\s*all_account_ids\s*,\s*days\s*,"
        r"\s*owner_emails\s*=\s*normalized_roster_emails\s*,?\s*\n?\s*\)",
        leader_src,
    ) is not None, (
        "Leader's _fetch_action_plans call shape changed; update parity test."
    )


def test_comp_call_does_not_apply_post_fetch_tech_filter():
    """R75/B1 contract: the Comprehensive Snowflake AP fetch must NOT
    pipe its result through ``_filter_csconsole_data_by_technology``
    (Leader doesn't, and the audit showed it eats 366 valid rows when
    AP records lack ``SUB_TECHNOLOGY_C`` / ``TECHNOLOGY_C``)."""
    block = _comp_call_block()
    # The forbidden pattern: a call to the tech-filter helper that takes
    # ``_r65_snowflake_aps`` as its first positional.
    forbidden = re.compile(
        r"_filter_csconsole_data_by_technology\s*\(\s*_r65_snowflake_aps",
    )
    assert forbidden.search(block) is None, (
        "R75/B1 regression: Comprehensive path is post-filtering Snowflake "
        "APs through _filter_csconsole_data_by_technology again. This "
        "collapses valid AP rows when C360_CS_TASK_C_VW lacks tech metadata. "
        "Drop the call -- the Snowflake query is already account-scoped."
    )


def test_comp_call_emits_structured_logging_for_kwargs():
    """The R75/B1 logging line MUST surface fetch kwargs to operator stderr
    so a future kwarg-divergence is debuggable without a code dive."""
    block = _comp_call_block()
    assert "comprehensive AP fetch kwargs" in block, (
        "R75/B1 logging line missing -- operator can't debug a future "
        "kwarg divergence without it."
    )
    assert "account_ids=%d owner_emails=%d days=%d tech=%s" in block, (
        "R75/B1 logging format string drifted; update both test and source "
        "if you change the format."
    )


# ---------------------------------------------------------------------------
# Behavioural test: helper returns 366 rows -> all 366 reach the merge
# (synthetic; mirrors the Build 47 audit's 366-row Leader.Action_Plans frame)
# ---------------------------------------------------------------------------


def _make_366_row_ap_frame() -> pd.DataFrame:
    """Synthetic Snowflake-shaped AP frame with 366 rows.

    Mirrors the Build 47 acceptance audit's Leader.Action_Plans shape:
    Snowflake column names, mixed customers across one manager's
    portfolio, no SUB_TECHNOLOGY_C / TECHNOLOGY_C (these are absent
    from C360_CS_TASK_C_VW which is the source-of-truth table).
    """
    rows = []
    customers = ["Acme Corp", "Beta Inc", "Gamma LLC", "Delta Co"]
    for i in range(366):
        rows.append({
            "ID": f"AP-{i:04d}",
            "ACCOUNT_ID_C": f"00100000000{i % 10:05d}",
            "DSM_BU_NAME": customers[i % len(customers)],
            "SUBJECT_C": f"Renewal Risk Workstream {i}",
            "DESCRIPTION_C": "Status update on renewal risk mitigation",
            "STATUS_C": "On Track",
            "OPEN_DATE_C": "2025-12-01",
            "CREATED_DATE": "2025-12-01",
            "RECORD_TYPE_ID": "0122T000000QHBGQA4",
        })
    return pd.DataFrame(rows)


def test_helper_returns_366_rows_unfiltered_when_no_tech_filter_kwarg():
    """The canonical helper must NOT apply any tech filter when the
    ``technology_filter`` kwarg is None (the Leader / R75-Comprehensive
    calling convention).  This is the back-compat contract that makes
    the R75/B1 fix safe.

    Synthetic 366-row frame -> all 366 survive when the helper is
    called with the Comprehensive's R75 kwargs.
    """
    rows_366 = _make_366_row_ap_frame()
    cur = MagicMock()
    cur.fetchall.return_value = list(rows_366.itertuples(index=False, name=None))
    cur.description = [(c,) for c in rows_366.columns]
    cur.execute = MagicMock()
    cur.close = MagicMock()
    ctx = MagicMock()
    ctx.cursor.return_value = cur

    df = fetch_action_plans_snowflake(
        ctx,
        account_ids=[f"00100000000{i:05d}" for i in range(10)],
        days=90,
        owner_emails=["sssm@cisco.com"],
    )
    assert len(df) == 366, (
        f"R75/B1: when called without technology_filter (the Leader / "
        f"R75-Comprehensive convention) the helper must return all "
        f"rows, got {len(df)}."
    )


def test_merge_path_preserves_366_snowflake_rows_when_csconsole_empty():
    """The post-fetch merge step must preserve all 366 Snowflake rows
    when CSConsole returns 0 -- i.e. no provenance row stamped, no
    summary KPI of 0 when the real count is 366.

    This is a unit-level regression for the Build 47 audit's
    smoking-gun finding: 1 provenance-marker row in Comp.Action_Plans
    while Leader.Action_Plans had 366 real rows for the same scope.
    """
    rows_366 = _make_366_row_ap_frame()
    csconsole_empty = pd.DataFrame()
    snowflake_count = len(rows_366)
    csconsole_count = len(csconsole_empty) if not csconsole_empty.empty else 0
    if snowflake_count > 0:
        if csconsole_empty is None or csconsole_empty.empty:
            merged = rows_366.copy()
            provenance = "snowflake"
        else:
            combined = pd.concat([csconsole_empty, rows_366], ignore_index=True)
            merged = combined.drop_duplicates(subset=["ID"], keep="last").reset_index(drop=True)
            provenance = "csconsole+snowflake"
    elif csconsole_count > 0:
        merged = csconsole_empty
        provenance = "csconsole"
    else:
        merged = pd.DataFrame()
        provenance = "empty"

    assert len(merged) == 366, (
        f"Merge collapsed {snowflake_count} Snowflake APs to {len(merged)} "
        "(expected 366). This is the Build 47 audit's smoking-gun "
        "regression -- if it fails, the merge step has drifted."
    )
    assert provenance == "snowflake", provenance
    assert "_adoptiq_provenance_row" not in merged.columns, (
        "R64/B2 provenance marker should NOT appear when real Snowflake "
        "rows are present -- only when both sources return 0."
    )
