"""Round 42 / Phase 3 + Phase 4: leader build_portfolio_metrics signature.

Pre-Round-42, ``app_simple.run_leader_report_generation`` called
``cm.build_portfolio_metrics`` with two kwargs that do not exist on the
canonical signature::

    _leader_portfolio_metrics = cm.build_portfolio_metrics(
        customer_subs=_team_subs_unfiltered_for_pm,    # NOT a kwarg
        ab_df=agg_ab,
        csone_df=agg_tac,
        customer_pulse_df=agg_pulse if not agg_pulse.empty else None,  # NOT a kwarg
        risk_scale=cm.RISK_SCALE_0_TO_10,
        extra_customer_frames=_leader_extra_frames or None,
        account_to_customer=_leader_a2c or None,
    )

This ALWAYS raised ``TypeError: build_portfolio_metrics() got an
unexpected keyword argument 'customer_subs'`` (the first invalid kwarg
in the call), which a broad ``except Exception`` swallowed at
``logger.debug`` level and set ``_leader_portfolio_metrics = None``.
The downstream ``validate_report_consistency`` call then ran with
``portfolio_metrics=None``, and the ``if portfolio_metrics:`` block at
``report_consistency.py:280`` was skipped entirely -- meaning the
leader path NEVER exercised the validator's PM parity gates.  Compact /
EI / renewal all enforce the same gates; only leader silently bypassed
them.

This test pins both halves of the fix:

1.  **Signature parity**: building ``portfolio_metrics`` via the now-
    valid kwargs (``ab_df=``, ``csone_df=``, ``risk_scale=``,
    ``extra_customer_frames=``, ``account_to_customer=``) returns a
    real dict, not None.

2.  **Validator round-trip**: passing that dict into
    ``validate_report_consistency`` on the Round-42 reference shape
    actually exercises the ``if portfolio_metrics:`` branch -- the
    validator's metrics dict reflects the canonical ``total_barriers``
    rather than dropping straight into the bypass path.
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

import canonical_metrics as cm
from report_consistency import validate_report_consistency


def _leader_shape_ab_fixture() -> pd.DataFrame:
    """71-row AB frame with 67 distinct IDs (the Round 42 reference shape)."""

    rng = np.random.default_rng(seed=2526)
    ids = [f"AB-{i:04d}" for i in range(67)]
    extra = list(rng.choice(ids, size=4, replace=False))
    rows = [{"ID": _id, "Account": f"Account-{_id}"} for _id in ids + extra]
    return pd.DataFrame(rows)


def test_build_portfolio_metrics_signature_does_not_accept_old_leader_kwargs() -> None:
    """The signature must NOT contain ``customer_subs`` or ``customer_pulse_df``.

    If a future refactor accidentally re-adds those kwargs to the
    canonical signature, the leader call would silently start working
    again -- but with semantics that have not been validated against
    the existing canonical helpers.  Pin the negative shape so any such
    addition is a deliberate decision, not a drift.
    """

    sig = inspect.signature(cm.build_portfolio_metrics)
    assert "customer_subs" not in sig.parameters, (
        "Round 42 / Phase 3: ``customer_subs`` is NOT a valid kwarg of "
        "``cm.build_portfolio_metrics``.  Pre-Round-42 the leader path "
        "passed it anyway and got silent ``TypeError`` -> "
        "``_leader_portfolio_metrics = None`` -> validator bypass.  If "
        "you are adding it now, also update the leader call in "
        "``app_simple.py`` and remove this assertion deliberately."
    )
    assert "customer_pulse_df" not in sig.parameters, (
        "Round 42 / Phase 3: ``customer_pulse_df`` is NOT a valid kwarg "
        "of ``cm.build_portfolio_metrics``.  Same as the "
        "``customer_subs`` note above."
    )


def test_leader_build_portfolio_metrics_with_valid_kwargs_returns_dict() -> None:
    """Leader's now-valid kwargs build a non-None portfolio_metrics dict."""

    ab = _leader_shape_ab_fixture()
    csone = pd.DataFrame()
    extra_frame = pd.DataFrame({"customer_name": ["TeamSubsCust1", "TeamSubsCust2"]})
    pm = cm.build_portfolio_metrics(
        ab_df=ab,
        csone_df=csone,
        risk_scale=cm.RISK_SCALE_0_TO_10,
        extra_customer_frames=[extra_frame],
        account_to_customer={"ACC1": "TeamSubsCust1"},
    )
    assert pm is not None, (
        "Round 42 / Phase 3: build_portfolio_metrics with the leader's "
        "actual (now-valid) kwarg set must return a dict.  None means "
        "the leader path is silently bypassing PM validation again."
    )
    assert isinstance(pm, dict), (
        f"Expected dict, got {type(pm).__name__}: {pm}"
    )
    assert pm.get("total_barriers") == 67, (
        "Round 42 / Phase 3 + Phase 1 cross-check: the leader path's "
        "PM ``total_barriers`` must use the canonical (distinct-ID) "
        f"helper.  Got {pm.get('total_barriers')!r}; expected 67."
    )


def test_leader_validator_roundtrip_actually_exercises_pm_branch() -> None:
    """Validator's PM branch fires (no bypass) on the leader-shape PM."""

    ab = _leader_shape_ab_fixture()
    csone = pd.DataFrame()
    pulse = pd.DataFrame({"BU_NAME": ["PulseCust"]})
    extra_frame = pd.DataFrame({"customer_name": ["ExtraCust"]})
    pm = cm.build_portfolio_metrics(
        ab_df=ab,
        csone_df=csone,
        risk_scale=cm.RISK_SCALE_0_TO_10,
        extra_customer_frames=[extra_frame],
        account_to_customer=None,
    )
    result = validate_report_consistency(
        ab,
        csone,
        portfolio_metrics=pm,
        pulse_df=pulse,
        extra_frames=[extra_frame],
    )
    assert result["is_valid"] is True, (
        "Round 42 / Phase 3 + Phase 1: validator must accept the "
        "leader-shape PM (no spurious total_barriers mismatch).  "
        f"Errors: {result.get('errors')}.  Warnings: {result.get('warnings')}."
    )
    # Pin that the canonical total_barriers actually flows through the
    # validator's metrics dict -- this proves the ``if portfolio_metrics:``
    # branch fired (pre-Round-42 it would skip silently because pm was
    # None, and result["metrics"]["total_barriers"] would still come
    # from the rowcount path = 71, not the canonical 67).
    assert int(result["metrics"]["total_barriers"]) == 67, (
        "Round 42 / Phase 1: when pm is wired through correctly, the "
        "validator's metrics['total_barriers'] must be the canonical "
        "distinct-ID count (67), not the rowcount (71).  Current value: "
        f"{result['metrics']['total_barriers']}.  This is the smoking-"
        "gun assertion that catches the silent-bypass regression."
    )
