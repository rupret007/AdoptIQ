"""Round 23.1 / R22-NEXT-LEADER — Leader-report mock harness.

Mission (per the Round 23 plan, Phase B / R22-NEXT-LEADER):

    ``leader_report_generator.generate_leader_report`` requires a live
    Snowflake ``ctx`` plus a ``team_roster`` of ``(manager_name,
    cssm_name, cssm_email)`` tuples. The ``LeaderReportGenerator``
    class queries Snowflake directly via ``self.ctx.cursor()`` in
    five fetch helpers (``_get_subscriptions_for_cssm``,
    ``_fetch_action_plans``, ``_fetch_adoption_barriers``,
    ``_fetch_customer_pulse``, ``_fetch_success_priorities``) and
    instantiates an ``EnhancedSnowflakeInsights(ctx)`` at __init__
    time.

    To drive the Leader path against the Round 19 golden fixture in
    CI without touching Snowflake, this module builds:

        1. A fake ``ctx`` (``MagicMock``) that satisfies the
           constructor's ``if ctx is None`` guard.
        2. A fixture-aligned ``team_roster`` whose CSSM emails are
           the keys we'll use to wire the Round 19 customers (AB +
           CSOne + extras = 5 customers) into ONE direct report so
           the Leader summary's grand-total row can be asserted
           against ``EXPECTED_KPIS`` directly.
        3. A ``patch_leader_generator_with_round19_fixture()``
           helper that monkeypatches the five Snowflake fetch
           methods on a generator instance to return DataFrames
           shaped from the Round 19 golden fixture rows.

This harness intentionally monkeypatches at the ``_fetch_*`` /
``_get_subscriptions_for_cssm`` level rather than at the
``cursor.execute()`` SQL layer because:

    - The cursor-level mock would have to encode the actual SQL
      shape and join semantics (DSM_ASSIGNMENT_DATA join, owner-
      email expansion, etc.), which makes the mock as brittle as
      the schema it's mocking.
    - The existing ``tests/test_leader_report.py`` tests already use
      the ``_fetch_*`` monkeypatch pattern (e.g.
      ``test_collect_team_data_captures_external_account_action_plans``)
      so this is the project's established Snowflake-mocking idiom.
    - Round 23.1 follow-up (R23-NEXT-INTEGRATION-SNAPSHOT) can
      replace this with a true cursor-level mock once the Leader
      path stabilises; the harness is designed so that change is a
      drop-in replacement of the patch helper.

Re-export the ``team_roster`` and the patch helper so test files can
import them directly without recreating the wiring. The harness is
self-contained: it does NOT import test infrastructure, so it can be
imported by CI driver scripts as well as pytest test cases.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

import pandas as pd

# When this module lives at ``tests/fixtures/round19/`` the parent dir
# might not be on ``sys.path`` if the test runner imports us via a path
# the same way ``test_round21_1_formatter_render_diff.py`` does. Both
# end up with this directory on ``sys.path`` already, so we can import
# the golden module directly.
from golden import (  # noqa: E402
    make_ab_df,
    make_csone_df,
    make_extra_frames,
    make_pulse_df,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Manager + single direct-report wiring. We intentionally collapse the
# whole Round 19 fixture universe into a single CSSM so the Leader
# summary's TOTAL row equals the registry KPIs (Total Customers = 5,
# Adoption Barriers = 10, etc.). Multi-CSSM splits are useful for the
# attribution tests in ``test_leader_report.py`` but not for the
# render-diff KPI parity test.
LEADER_MANAGER_NAME: str = "Manager Round23"
LEADER_CSSM_NAME: str = "Brandon Round23"
LEADER_CSSM_EMAIL: str = "brandon.round23@example.com"


def make_leader_team_roster() -> List[Tuple[str, str, str]]:
    """Return the ``(manager_name, cssm_name, cssm_email)`` tuples the
    Leader-report path expects.

    The ``LeaderReportGenerator._get_direct_reports`` method scans this
    list for entries whose first element matches the requested manager
    and emits ``{name, email}`` dicts in roster order. With a single
    direct report we get a deterministic team_data shape with one
    member who owns all 5 fixture customers.
    """
    return [
        (LEADER_MANAGER_NAME, LEADER_CSSM_NAME, LEADER_CSSM_EMAIL),
    ]


def make_leader_mock_ctx() -> mock.MagicMock:
    """Return a fake Snowflake ``ctx`` suitable for the
    ``LeaderReportGenerator`` constructor.

    The constructor only checks ``if ctx is None: raise ValueError``,
    instantiates ``EnhancedSnowflakeInsights(ctx)`` (which stores the
    ctx but does not query at __init__ time), and exposes
    ``self.ctx.cursor()`` to the fetch helpers. Because we monkeypatch
    the fetch helpers at the method level (see
    ``patch_leader_generator_with_round19_fixture``), the cursor is
    never actually exercised. The ``MagicMock`` keeps the surface
    flexible should a future Snowflake call escape the fetch helpers.
    """
    return mock.MagicMock(name="leader_mock_ctx")


# ---------------------------------------------------------------------------
# Round 19 fixture -> Leader-report DataFrame shaping
# ---------------------------------------------------------------------------
#
# The Leader path's fetch helpers expect specific column shapes
# (``ACCOUNT_ID_C``, ``BU_NAME``, ``CSSM_EMAIL``, etc.) that the AB /
# CSOne / Pulse golden fixtures don't carry. Below we build minimal
# Leader-shaped frames keyed off the fixture's ``customer_name`` /
# ``RELATED_CUSTOMER__C`` columns so the Round 19 customer universe
# (AcmeCorp + BetaInc + GammaLLC + DeltaCo + EpsilonInc = 5) flows into
# the single direct report's bucket exactly once.


def _customer_to_account_id(customer: str) -> str:
    """Synthesise a deterministic ACCOUNT_ID_C per fixture customer.

    The Leader path treats ``ACCOUNT_ID_C`` as opaque; the only
    constraint is that the same customer maps to the same ID across
    all fetch helpers so the per-CSSM slicing's ``isin(account_ids)``
    join works.
    """
    return f"ACC_{customer.upper()}"


def _round19_customer_universe() -> List[str]:
    """The 5 customers in the Round 19 multi-source universe.

    Mirrors ``EXPECTED_KPIS['total_customers']`` (= 5). Order matches
    the registry-pinned narrative (AB + CSOne first, then extras).
    """
    return ["AcmeCorp", "BetaInc", "GammaLLC", "DeltaCo", "EpsilonInc"]


def _round19_subscriptions_df(cssm_email: str) -> pd.DataFrame:
    """Build a Leader-shaped subscriptions frame.

    Columns: ``[SUBSCRIPTION_ID, ACCOUNT_ID_C, BU_NAME, CSSM_EMAIL]``.
    One row per fixture customer, all owned by the same CSSM email so
    the entire universe lands in one direct report's bucket.
    """
    rows: List[Dict[str, Any]] = []
    for idx, customer in enumerate(_round19_customer_universe(), start=1):
        rows.append(
            {
                "SUBSCRIPTION_ID": f"SUB-{idx:04d}",
                "ACCOUNT_ID_C": _customer_to_account_id(customer),
                "BU_NAME": customer,
                "CSSM_EMAIL": cssm_email,
            }
        )
    return pd.DataFrame(rows)


def _round19_action_plans_df() -> pd.DataFrame:
    """Build a Leader-shaped Action Plans frame.

    Round 19 doesn't include APs as a primary fixture (they live only
    in the ``make_extra_frames`` csconsole_action_plans bundle, keyed
    by ``RELATED_CUSTOMER__C`` not ``ACCOUNT_ID_C``). Return an empty
    frame for now; the Leader summary's ``Action Plans`` column will
    read 0, which is still deterministic and assertable. A future
    expansion (R23-NEXT-INTEGRATION-SNAPSHOT) can wire the csconsole
    AP rows in once the cursor-level mock lands.
    """
    return pd.DataFrame(
        columns=["ID", "ACCOUNT_ID_C", "SUBJECT_C", "OWNER_EMAIL", "DSM_BU_NAME"]
    )


def _round19_adoption_barriers_df() -> pd.DataFrame:
    """Reshape the Round 19 AB fixture into the Leader-path's expected
    columns.

    The fixture has 10 rows for AcmeCorp(4) + BetaInc(3) + GammaLLC(3).
    We keep the row count and severity distribution so the Leader's
    ``Adoption Barriers`` column equals 10 (= ``EXPECTED_KPIS[
    'total_barriers']``).
    """
    ab_df = make_ab_df()
    rows: List[Dict[str, Any]] = []
    for idx, ab in enumerate(ab_df.to_dict(orient="records"), start=1):
        customer = ab["customer_name"]
        rows.append(
            {
                "ID": ab["ID"],
                "ACCOUNT_ID_C": _customer_to_account_id(customer),
                "SUBJECT_C": ab["SUBJECT_C"],
                "SEVERITY_C": ab["SEVERITY_C"],
                "severity_norm": ab["severity_norm"],
                "AB_STATUS_C": ab["AB_STATUS_C"],
                "case_status_norm": ab["case_status_norm"],
                "BU_NAME": customer,
                "DSM_BU_NAME": customer,
            }
        )
    return pd.DataFrame(rows)


def _round19_customer_pulse_df() -> pd.DataFrame:
    """Reshape the Round 19 Pulse fixture into the Leader-path's
    expected columns.

    The Leader fetcher returns rows keyed off ``ACCOUNT__C``; the
    ``_collect_team_data`` rename converts that to ``ACCOUNT_ID_C``
    before the per-CSSM slice. We emit ``ACCOUNT__C`` here so the
    rename path is exercised. 8 fixture rows -> 8 Leader rows.
    """
    pulse_df = make_pulse_df()
    rows: List[Dict[str, Any]] = []
    for idx, pulse in enumerate(pulse_df.to_dict(orient="records"), start=1):
        customer = pulse["RELATED_CUSTOMER__C"]
        rows.append(
            {
                "ID": f"CP{idx:03d}",
                "ACCOUNT__C": _customer_to_account_id(customer),
                "SCORE__C": pulse["SCORE__C"],
                "BU_NAME": customer,
                "DSM_BU_NAME": customer,
            }
        )
    return pd.DataFrame(rows)


def _round19_success_priorities_df() -> pd.DataFrame:
    """Build a Leader-shaped Success Priorities frame from extras.

    The Round 19 fixture's ``make_extra_frames()`` includes a
    csconsole_success_priorities frame keyed by ``RELATED_CUSTOMER__C``.
    Mirror that here so the Leader path's success-priorities slice has
    deterministic content.
    """
    extras = make_extra_frames()
    sp_extra = extras[2]
    if sp_extra is None or sp_extra.empty:
        return pd.DataFrame(columns=["ID", "RELATED_CUSTOMER__C"])
    return sp_extra.copy()


# ---------------------------------------------------------------------------
# Patch helper
# ---------------------------------------------------------------------------


def patch_leader_generator_with_round19_fixture(
    monkeypatch,
    generator,
    cssm_email: str = LEADER_CSSM_EMAIL,
) -> None:
    """Replace the five Snowflake fetch helpers + EnhancedSnowflakeInsights
    with Round 19 fixture-shaped responses.

    ``monkeypatch`` is the pytest fixture (or a compatible API).
    ``generator`` is a fully-constructed ``LeaderReportGenerator``
    instance whose ``ctx`` was built via :func:`make_leader_mock_ctx`.

    After this call, ``generator._collect_team_data(...)`` returns a
    deterministic team_data dict with one CSSM owning the 5 Round 19
    customers (10 ABs, 8 Pulse rows, 0 APs).
    """
    subscriptions_df = _round19_subscriptions_df(cssm_email)
    action_plans_df = _round19_action_plans_df()
    adoption_barriers_df = _round19_adoption_barriers_df()
    customer_pulse_df = _round19_customer_pulse_df()
    success_priorities_df = _round19_success_priorities_df()

    monkeypatch.setattr(
        generator,
        "_get_subscriptions_for_cssm",
        lambda emails: subscriptions_df.copy(),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_action_plans",
        lambda account_ids, days, owner_emails=None: action_plans_df.copy(),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_adoption_barriers",
        lambda account_ids, days, owner_emails=None: adoption_barriers_df.copy(),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_customer_pulse",
        lambda account_ids, days, owner_emails=None: customer_pulse_df.copy(),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_success_priorities",
        lambda customers, days: success_priorities_df.copy(),
    )

    # ``EnhancedSnowflakeInsights.get_comprehensive_customer_insights``
    # is called inside ``_add_customer_enhanced_insights`` during the
    # per-customer narrative pass. Return an empty dict so the renderer
    # takes the "no enhanced insights available" path without hitting
    # Snowflake.
    if getattr(generator, "enhanced_insights", None) is not None:
        monkeypatch.setattr(
            generator.enhanced_insights,
            "get_comprehensive_customer_insights",
            lambda customer, days=None: {},
        )


def make_leader_csone_df_for_tac_integration() -> pd.DataFrame:
    """Round 19 CSOne frame, reshaped for ``add_tac_cases_from_csone``.

    The Leader wrapper (``leader_report_generator.generate_leader_report``,
    module-level) calls ``add_tac_cases_from_csone(team_data, csone_df,
    days)`` after the initial render to populate the TAC column. The
    Round 19 csone_df has 20 rows for AcmeCorp(11) + BetaInc(9) so the
    Leader's ``TAC cases (total)`` cell can be asserted against
    ``EXPECTED_KPIS['total_cases']``.

    ``add_tac_cases_from_csone`` searches for a customer column by
    looking for tokens ``customer name``, ``account name``, ``bu_name``
    (case-insensitive substring match). The Round 19 fixture's column
    is ``customer_name`` (underscore), which fails that match, so we
    rename it to ``BU_NAME`` here. We also stamp a ``Created Date`` so
    the date-window filter recognises and accepts the rows (the helper
    silently drops the frame to all-empty if no date column is found
    and no customer column matches).
    """
    df = make_csone_df().copy()
    # Round 23.1 / R22-NEXT-LEADER: rename to a token the helper recognises.
    df = df.rename(columns={"customer_name": "BU_NAME"})
    # Round 23.1 / R22-NEXT-LEADER: stamp recent UTC timestamps so the
    # ``days`` window filter (CREATE_DATE >= now() - days) keeps every
    # fixture row regardless of how long-running the test session is.
    from datetime import datetime, timezone

    df["Date Opened"] = pd.Timestamp(datetime.now(timezone.utc))
    return df


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------

__all__: Sequence[str] = (
    "LEADER_MANAGER_NAME",
    "LEADER_CSSM_NAME",
    "LEADER_CSSM_EMAIL",
    "make_leader_team_roster",
    "make_leader_mock_ctx",
    "patch_leader_generator_with_round19_fixture",
    "make_leader_csone_df_for_tac_integration",
)
