"""Phase 5 / Phase 1.1 regression test.

Before Phase 1.1, ``run_leader_report_generation`` never invoked
``raise_validation_error_if_invalid``, so a Snowflake outage or
empty team-subscription roster could ship a leader report full of
zeros.  This test pins the contract by inspecting the source of
the leader-report generator path: the function must call the
validator with ``report_type='leader'`` and a ``required_sources``
list that includes both ``'snowflake'`` and ``'team_subscriptions'``.
"""
from __future__ import annotations

import inspect

import app_simple


def test_run_leader_report_generation_calls_validator() -> None:
    src = inspect.getsource(app_simple.run_leader_report_generation)
    assert "raise_validation_error_if_invalid" in src, (
        "Leader report path must call raise_validation_error_if_invalid "
        "(Phase 1.1).  Without it, a Snowflake outage silently produces "
        "a leader report full of zeros."
    )
    assert "report_type='leader'" in src or 'report_type="leader"' in src, (
        "The validator call must pass report_type='leader' so the "
        "appropriate required-source policy is applied."
    )
    assert "team_subscriptions" in src, (
        "Leader required_sources list must include 'team_subscriptions' "
        "so an empty roster blocks the report."
    )
    assert "snowflake" in src.lower(), (
        "Leader required_sources list must include 'snowflake' so a "
        "missing connection blocks the report."
    )


def test_subscription_path_calls_validator() -> None:
    """Phase 1.1 also wired the validator into the subscription
    analysis path.  Pin the contract the same way."""
    src = inspect.getsource(app_simple.run_subscription_analysis)
    assert "raise_validation_error_if_invalid" in src, (
        "Subscription analysis path must call validator (Phase 1.1)."
    )
