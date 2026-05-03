"""Round 71 / Phase 4 (#21) -- leader fetch_action_plans uses policy cursor.

Pre-R71 ``leader_report_generator.fetch_action_plans_snowflake`` called
``ctx.cursor()`` directly.  The raw cursor bypassed the
``snowflake_table_policy.guard_sql`` check that
``enhanced_snowflake_insights._PolicyEnforcingCursor`` wraps every SQL
execution with -- meaning a Snowflake table newly added to
``_BLOCKED_CANONICAL`` would still get hit from this code path with no
runtime trip-wire.

Round 71 / Phase 4 (#21) wraps the cursor with ``_PolicyEnforcingCursor``
so the policy guard runs uniformly.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_leader() -> str:
    return (REPO_ROOT / "leader_report_generator.py").read_text(encoding="utf-8", errors="replace")


def test_round71_leader_fetch_aps_imports_policy_cursor() -> None:
    """The leader fetch site MUST import ``_PolicyEnforcingCursor``."""
    src = _read_leader()
    assert "from enhanced_snowflake_insights import _PolicyEnforcingCursor as _R71_PolicyCursor" in src, (
        "Round 71 / Phase 4 (#21): leader_report_generator must import "
        "_PolicyEnforcingCursor (aliased as _R71_PolicyCursor) so the "
        "fetch_action_plans_snowflake cursor passes the table-policy guard."
    )


def test_round71_leader_fetch_aps_wraps_cursor() -> None:
    """The leader fetch site MUST wrap ``ctx.cursor()`` with
    ``_R71_PolicyCursor``."""
    src = _read_leader()
    assert "cur = _R71_PolicyCursor(_r71_raw_cur, context=\"leader_fetch_action_plans\")" in src, (
        "Round 71 / Phase 4 (#21): leader_report_generator must wrap "
        "ctx.cursor() with _R71_PolicyCursor so the table-policy guard "
        "runs on the AP fetch."
    )


def test_round71_leader_fetch_aps_falls_back_safely_on_import_failure() -> None:
    """If ``_PolicyEnforcingCursor`` import fails (e.g. bare CLI run),
    the fetch site MUST fall through to the raw cursor (degraded but
    functional) rather than crash."""
    src = _read_leader()
    # The fallback line.
    assert "cur = _r71_raw_cur" in src, (
        "Round 71 / Phase 4 (#21): leader fetch site must have a "
        "fallback (cur = _r71_raw_cur) when the policy cursor import "
        "fails so a bare CLI run still completes."
    )


def test_round71_leader_fetch_aps_assigns_raw_cursor_first() -> None:
    """The raw cursor MUST be obtained before the policy wrap, so the
    finally / cleanup path can close it."""
    src = _read_leader()
    # The raw cursor must precede the policy wrap.
    raw_idx = src.find("_r71_raw_cur = ctx.cursor()")
    wrap_idx = src.find("cur = _R71_PolicyCursor(")
    assert 0 < raw_idx < wrap_idx, (
        "Round 71 / Phase 4 (#21): leader fetch site must obtain the "
        "raw cursor BEFORE the policy wrap so cleanup can target it."
    )
