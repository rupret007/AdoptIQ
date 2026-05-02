"""Round 66 / Pass 1 (B2) -- defense-in-depth scope filter on AP fetch.

Round 65 / Phase 1 (C-2) extracted ``fetch_action_plans_snowflake`` from
``LeaderReportGenerator._fetch_action_plans`` so the comprehensive flow
can call the same canonical Snowflake query. The comprehensive caller
(app_simple ~L14897) currently runs the result through
``_filter_csconsole_data_by_technology`` AFTER the fetch to scope to the
manager+technology slice -- but that's a contract not enforced by
``fetch_action_plans_snowflake`` itself, so a future caller (or a
refactor) could leak cross-technology rows into a scoped report.

R66/B2 closes this by adding optional ``technology_filter`` and
``customer_names`` parameters to ``fetch_action_plans_snowflake``. When
supplied, the function pipes the post-fetch result through
``_filter_csconsole_data_by_technology`` itself as defense-in-depth.
The Leader path (``technology_filter=None``) is unaffected by design --
Leader reports are intentionally cross-technology.

These tests pin both the new parameter shape AND the post-fetch filter
behavior using a 3-customer fixture where 2 are out of scope.

Round 66 / Pass 1.  Made-with: Cursor.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pandas as pd

from leader_report_generator import fetch_action_plans_snowflake


def _make_fake_ctx(rows: list[tuple], cols: list[str]) -> MagicMock:
    ctx = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.description = [(c,) for c in cols]
    cur.execute = MagicMock()
    cur.close = MagicMock()
    ctx.cursor.return_value = cur
    return ctx


# ---------------------------------------------------------------------------
# B2 — signature: optional technology_filter / customer_names
# ---------------------------------------------------------------------------


def test_fetch_aps_signature_includes_technology_filter_and_customer_names():
    """The R66/B2 helper signature MUST expose the new optional kwargs."""
    sig = inspect.signature(fetch_action_plans_snowflake)
    assert "technology_filter" in sig.parameters, sig
    assert "customer_names" in sig.parameters, sig
    # Both are keyword-only (the bare * before them in the source).
    assert sig.parameters["technology_filter"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["customer_names"].kind == inspect.Parameter.KEYWORD_ONLY
    # Both default to None for back-compat with R65/C-2 callers.
    assert sig.parameters["technology_filter"].default is None
    assert sig.parameters["customer_names"].default is None


def test_fetch_aps_default_signature_unchanged_no_filter_applied():
    """When ``technology_filter`` is None (the R65/C-2 default), the
    function must NOT apply any post-fetch scope filter -- back-compat
    with the Leader path which is cross-technology by design."""
    rows = [
        ("AP1", "0011", "Acme Corp", "Webex Meetings", "OPEN"),
        ("AP2", "0012", "Beta Inc",  "Contact Center", "OPEN"),
        ("AP3", "0013", "Gamma LLC", "Webex Meetings", "OPEN"),
    ]
    cols = ["ID", "ACCOUNT_ID_C", "BU_NAME", "SUB_TECHNOLOGY_C", "STATUS_C"]
    ctx = _make_fake_ctx(rows, cols)
    df = fetch_action_plans_snowflake(
        ctx, account_ids=["0011", "0012", "0013"], days=90
    )
    assert len(df) == 3, f"all 3 rows must survive when no scope filter is applied; got {len(df)}"


# ---------------------------------------------------------------------------
# B2 — post-fetch filter: 3-customer fixture, 2 out of scope
# ---------------------------------------------------------------------------


def test_fetch_aps_with_technology_filter_drops_out_of_scope_rows():
    """3-customer fixture: only the in-scope row survives the post-fetch filter.

    Fixture:
      AP1 / Acme Corp / Webex Meetings  (OUT OF SCOPE for "All Contact Center")
      AP2 / Beta Inc  / Contact Center  (IN SCOPE)
      AP3 / Gamma LLC / Webex Meetings  (OUT OF SCOPE)
    """
    rows = [
        ("AP1", "0011", "Acme Corp", "Webex Meetings", "OPEN"),
        ("AP2", "0012", "Beta Inc",  "Contact Center", "OPEN"),
        ("AP3", "0013", "Gamma LLC", "Webex Meetings", "OPEN"),
    ]
    cols = ["ID", "ACCOUNT_ID_C", "BU_NAME", "SUB_TECHNOLOGY_C", "STATUS_C"]
    ctx = _make_fake_ctx(rows, cols)

    df = fetch_action_plans_snowflake(
        ctx,
        account_ids=["0011", "0012", "0013"],
        days=90,
        technology_filter="All Contact Center",
        customer_names=["Acme Corp", "Beta Inc", "Gamma LLC"],
    )

    # Only the Contact Center row survives.
    assert len(df) == 1, (
        f"expected exactly 1 in-scope row for tech='All Contact Center'; "
        f"got {len(df)}: {df}"
    )
    assert df.iloc[0]["ID"] == "AP2"
    assert df.iloc[0]["BU_NAME"] == "Beta Inc"


def test_fetch_aps_with_all_technologies_keeps_all_rows():
    """The sentinel ``technology_filter='All Technologies'`` is a no-op.

    This mirrors ``_filter_csconsole_data_by_technology`` behavior --
    the ``"All"`` / ``"All Technologies"`` labels short-circuit the
    technology filter so callers can pass the user's selected label
    verbatim without an explicit None check.
    """
    rows = [
        ("AP1", "0011", "Acme Corp", "Webex Meetings", "OPEN"),
        ("AP2", "0012", "Beta Inc",  "Contact Center", "OPEN"),
        ("AP3", "0013", "Gamma LLC", "Cloud Calling", "OPEN"),
    ]
    cols = ["ID", "ACCOUNT_ID_C", "BU_NAME", "SUB_TECHNOLOGY_C", "STATUS_C"]
    ctx = _make_fake_ctx(rows, cols)

    df = fetch_action_plans_snowflake(
        ctx,
        account_ids=["0011", "0012", "0013"],
        days=90,
        technology_filter="All Technologies",
        customer_names=["Acme Corp", "Beta Inc", "Gamma LLC"],
    )

    assert len(df) == 3, f"All Technologies sentinel should be a no-op; got {len(df)}"


def test_fetch_aps_filter_failure_does_not_crash_returns_unfiltered():
    """If the post-fetch filter raises, return the unfiltered frame (degrade gracefully).

    The defense-in-depth filter MUST NOT take down the entire fetch
    if the upstream filter helper changes shape. This pins the
    failure semantics: log + return unfiltered, never raise.
    """
    rows = [
        ("AP1", "0011", "Acme Corp", "Webex Meetings", "OPEN"),
    ]
    cols = ["ID", "ACCOUNT_ID_C", "BU_NAME", "SUB_TECHNOLOGY_C", "STATUS_C"]
    ctx = _make_fake_ctx(rows, cols)

    with patch(
        "adoptiq_backend._filter_csconsole_data_by_technology",
        side_effect=RuntimeError("simulated filter failure"),
    ):
        df = fetch_action_plans_snowflake(
            ctx,
            account_ids=["0011"],
            days=90,
            technology_filter="All Contact Center",
            customer_names=["Acme Corp"],
        )

    # Should NOT raise; should return the unfiltered DataFrame.
    assert len(df) == 1
    assert df.iloc[0]["ID"] == "AP1"


def test_fetch_aps_filter_with_empty_customer_names_still_works():
    """Filter applied with empty ``customer_names`` still uses tech-only scoping.

    Edge case: if the caller passes ``technology_filter`` but no
    ``customer_names`` (e.g., the manager scope is being computed
    and not yet known), the function still applies the technology
    filter via account_ids alone. This avoids leaking cross-tech
    rows just because the caller didn't compute customer_names yet.
    """
    rows = [
        ("AP1", "0011", "Acme Corp", "Webex Meetings", "OPEN"),
        ("AP2", "0011", "Acme Corp", "Contact Center", "OPEN"),
    ]
    cols = ["ID", "ACCOUNT_ID_C", "BU_NAME", "SUB_TECHNOLOGY_C", "STATUS_C"]
    ctx = _make_fake_ctx(rows, cols)

    df = fetch_action_plans_snowflake(
        ctx,
        account_ids=["0011"],
        days=90,
        technology_filter="All Contact Center",
        customer_names=None,  # explicitly None
    )

    # Tech filter should still apply (drop the Webex Meetings row).
    assert len(df) == 1, df
    assert df.iloc[0]["SUB_TECHNOLOGY_C"] == "Contact Center"


# ---------------------------------------------------------------------------
# B2 — back-compat: existing R65 callers (no kwargs) continue to work
# ---------------------------------------------------------------------------


def test_fetch_aps_back_compat_no_new_kwargs_match_r65_behavior():
    """The R65 calling convention must continue to work without modification."""
    rows = [
        ("AP1", "0011", "Acme Corp", "Webex Meetings", "OPEN"),
        ("AP2", "0012", "Beta Inc",  "Contact Center", "OPEN"),
    ]
    cols = ["ID", "ACCOUNT_ID_C", "BU_NAME", "SUB_TECHNOLOGY_C", "STATUS_C"]
    ctx = _make_fake_ctx(rows, cols)

    # The R65 calling pattern from app_simple.py / leader_report_generator.py.
    df = fetch_action_plans_snowflake(
        ctx,
        account_ids=["0011", "0012"],
        days=90,
        owner_emails=["sssm@cisco.com"],
        chunk_size=900,
    )

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2  # All rows survive (no scope filter applied).
