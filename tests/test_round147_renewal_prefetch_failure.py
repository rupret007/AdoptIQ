"""Renewal prefetch failures must remain unavailable through report scoping."""

from __future__ import annotations

import ast
from pathlib import Path

import canonical_metrics as cm
from app_simple import (
    _empty_df_preserving_source_attrs,
    _empty_df_with_fetch_marker,
)


def test_empty_scope_preserves_failed_source_state() -> None:
    failed = _empty_df_with_fetch_marker(
        "csconsole_action_plans",
        "renewal_csconsole_prefetch_failed",
    )
    failed.attrs["fetch_error_kind"] = "fetch_failed"

    scoped = _empty_df_preserving_source_attrs(failed)

    assert scoped.attrs == failed.attrs
    assert cm.source_data_state(scoped) == {
        "state": "failed",
        "detail": "renewal_csconsole_prefetch_failed",
    }


def test_renewal_prefetch_exception_marks_all_sources_and_emits_bundle_warning() -> None:
    source_path = Path(__file__).resolve().parents[1] / "app_simple.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    renewal = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run_customer_renewal_analysis"
    )
    handler = next(
        handler
        for handler in ast.walk(renewal)
        if isinstance(handler, ast.ExceptHandler)
        and (segment := ast.get_source_segment(source, handler))
        and "Renewal CSConsole prefetch failed" in segment
    )
    handler_source = ast.get_source_segment(source, handler) or ""
    marked_datasets = {
        call.args[0].value
        for call in ast.walk(handler)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_empty_df_with_fetch_marker"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    }

    assert marked_datasets == {
        "csconsole_action_plans",
        "csconsole_customer_pulse",
        "csconsole_success_priorities",
        "csconsole_adoption_barriers",
    }
    assert "'dataset': 'csconsole_bundle'" in handler_source
    assert "'kind': 'fetch_failed'" in handler_source
    assert "unavailable, not zero" in handler_source
