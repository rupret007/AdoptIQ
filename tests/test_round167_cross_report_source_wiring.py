"""Cross-report source-window wiring regressions.

The retained pre-Round-167 artifacts showed three different TAC universes for
the same manager/team/window: one route used the host clock, two retained the
full history, and Leader used the explicit report clock.  These tests pin the
production orchestration contract rather than relying only on artifact parity
inside each individual report.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from typing import Any

import app_simple


def _calls(function: Callable[..., Any], called_name: str) -> list[ast.Call]:
    tree = ast.parse(inspect.getsource(function))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == called_name
    ]


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next(
        (keyword.value for keyword in call.keywords if keyword.arg == name),
        None,
    )


def test_all_decision_report_routes_use_the_strict_csone_boundary() -> None:
    report_workers = (
        app_simple.run_compact_analysis,
        app_simple.run_comprehensive_analysis,
        app_simple.run_customer_renewal_analysis,
        app_simple.run_leader_report_generation,
    )

    for worker in report_workers:
        calls = _calls(worker, "_r162_apply_strict_csone_report_scope")
        assert calls, f"{worker.__name__} does not use the shared strict CSOne boundary"


def test_all_decision_report_csone_calls_pin_window_and_clock() -> None:
    report_workers = (
        app_simple.run_compact_analysis,
        app_simple.run_comprehensive_analysis,
        app_simple.run_customer_renewal_analysis,
        app_simple.run_leader_report_generation,
    )

    for worker in report_workers:
        calls = _calls(worker, "_r162_apply_strict_csone_report_scope")
        for call in calls:
            include_all = _keyword(call, "include_all_cases")
            assert isinstance(include_all, ast.Constant)
            assert include_all.value is False
            assert _keyword(call, "as_of") is not None
