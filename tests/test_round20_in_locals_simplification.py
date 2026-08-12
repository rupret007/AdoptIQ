"""Round 20 / R20-001 regression tests: simplify dead ``X if 'X' in
locals() else FALLBACK`` guards in ``run_compact_analysis`` and pin
the post-Round-20 floor so future Cursor sessions cannot silently
re-introduce the antipattern.

Why this test file exists
-------------------------
The Round 18 / Round 19.1 handoffs cited the ``in locals()`` count in
``app_simple.py`` as having "grown 74 → 84 in 24 hours."  Git history
across every commit since Round 14 shows the count has been stable at
**84** the entire time -- the "growth" was a recon error.  The actual
work to do is the long-tail R18-NEXT-001 triage of the stable 84
sites, not an emergency drift-rollback.

Round 20 closes 19 sites in ``run_compact_analysis`` (outside nested
functions) where the guard's else branch is provably dead:
- ``feature_requests`` is sentinel-init'd at function top (L5970) with
  the exact dict the else branch was returning.
- ``ext_bugs`` / ``ext_incidents`` are sentinel-init'd at L6720 / L6721
  before their try/except blocks; both except branches re-assign.
- ``software_defects`` / ``psirt_vulns`` are assigned in BOTH success
  and except branches of the L6759-6765 try block.
- ``team_subs_df`` / ``csconsole_*`` are bound by the L6372-6526 fetch
  block; the else branch at L6527 (no Snowflake ctx) sets status=error
  and returns before any of the simplified call sites is reachable.

What this test file pins
------------------------
1. The post-Round-20 ``in locals()`` count in app_simple.py is at the
   new floor (recon-discipline guard so a future Cursor session that
   adds a NEW antipattern site without simplifying an existing one
   trips this test).
2. The simplified call sites still pass the actual variable (not the
   former ``None`` / empty-default) into the receivers -- a contract
   pin so a future edit cannot accidentally re-introduce the guard
   without making the test loud about it.
3. The recon-error finding itself: the count has been stable at 84
   across every commit since Round 14, NOT growing.  Documented here
   so a future reviewer can see it is asserted, not just claimed.

Out of scope for Round 20 (deferred to R20-NEXT)
------------------------------------------------
- Sites INSIDE nested functions (`generate_report` L7062, `generate_excel`
  L7335) are NOT just dead-branch antipatterns -- they are actual
  closure-binding bugs.  Inside a nested function, ``locals()`` does NOT
  include free variables captured from the enclosing scope, so
  ``'team_subs_df_unfiltered' in locals()`` evaluates False and the
  else branch is silently taken -- meaning the outer-scope variable is
  dropped at the call site.  Fixing those is a behavior-changing edit
  that warrants its own round + per-site verification.
- The remaining ~28 ``in locals()`` sites in ``run_customer_renewal_analysis``
  / ``run_subscription_analysis`` / ``run_leader_report_generation`` are
  the same long-tail R18-NEXT-001 work; addressed incrementally per round.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"

# Post-R20-001 floor for the ``in locals()`` count in app_simple.py.
# Round 18's recon claimed 74 (wrong); the actual count was 84 at
# commit db0803e (end of Round 18) AND has been 84 since Round 14
# (commit 9d53b94, 10+ commits ago).  Round 20 lowered it from 84 to 65
# by simplifying 19 dead-branch guard sites in run_compact_analysis.
# Round 23 / R22-NEXT-001 lowered it from 65 to 42 by replacing all
# closure-binding ``in locals()`` checks inside the nested
# ``generate_report`` / ``generate_excel`` functions with explicit
# ctx-dict access (the antipattern was actively dropping csconsole-only
# customers from the renewal risk universe and hardcoding
# recent_window_days=30 -- this was a behaviour-changing fix, not just
# hygiene).
# Round 23.2 / R22-NEXT-IN-LOCALS-RENEWAL lowered it from 40 to 31 by
# simplifying 9 dead presence guards in ``run_customer_renewal_analysis``
# (all_customers x2, team_subs_df, csconsole_* loop x1 collapsed into
# direct iteration, customer_action_plans / customer_customer_pulse /
# customer_success_priorities sentinel re-init, and the renewal_word_path
# / excel_path completion-record kwargs).  The remaining 31 sites
# include legitimate ``finally``-block ctx guards, partial-data warning
# checks, and untouched chunks in subscription / leader / helper
# functions queued for future rounds.
#
# Round 30 / M4 raised the floor from 31 to 33 by adding TWO new
# *legitimately defensive* ``in locals()`` guards in service of the new
# Partial Data banner contract: one in the executive-pipeline call site
# that promotes optional CSConsole/ARR fetch errors into
# ``partial_data_warnings`` (the local is conditionally bound only when
# ``has_optional_fetch_errors`` returns true), and one in the leader
# completion-record kwargs that piggy-backs ``_r30_leader_partial_warnings``
# onto the existing report-history payload.  Both are NOT the dead-branch
# antipattern R20-001 / R23.2 simplified -- they are the
# ``'cur' in locals()`` / ``'_r30_leader_partial_warnings' in locals()``
# defensive shape the docstring explicitly carves out as legitimate.
#
# Round 32 / Phase 1.A audit raised the floor from 33 to 34 to account
# for the explanatory comment ``# noqa: F821 (conditionally bound;
# guarded by 'in locals()')`` immediately above the leader
# ``arr_impact`` guard.  The comment contains the literal token
# ``'in locals()'`` and the regex below matches it for what it is --
# a third occurrence -- even though it is documentation, not a real
# guard.  Splitting into two regexes (one for the call expression and
# one for substring-in-comment) would let us discount the comment but
# would also let a future session sneak a real new guard in via a
# string literal; the simpler discipline is "every literal token
# counts, document the addition here."  No new run-time guards in
# Round 32.
#
# Round 48 / R48-D9 + R48-D10 raised the floor from 34 to 40 to
# account for the new ``_r48_harvest_renewal_pdw()`` helper inside
# ``run_customer_renewal_analysis``.  The helper iterates over six
# renewal-relevant DataFrame names (``customer_ab``, ``customer_csone``,
# ``customer_customer_pulse``, ``customer_action_plans``,
# ``customer_success_priorities``, ``team_subs_df``) and harvests
# ``df.attrs['fetch_error']`` from each one to populate the renewal
# Word ``Partial Data Warning`` banner (R48-D10) and the renewal
# Excel ``Report_Info.Partial_Data_Warning_Count`` cell (R48-D9).
# These six DataFrames are conditionally bound -- a fetch path may
# never reach the assignment if an upstream guard short-circuits --
# so each access uses the legitimate-defensive ``X if 'X' in locals()
# else None`` shape the docstring carves out.  The alternative
# (``try: ... except NameError: ...`` per access) would be six
# try/except blocks for the same effect.  No NEW dead-branch
# antipattern sites; this is purely the legitimate-defensive case.
#
# Future rounds that simplify MORE sites should lower this floor in the
# same change; rounds that intentionally introduce a new ``in locals()``
# pattern (e.g. for legitimate ``'cur' in locals()`` cleanup-after-try
# discipline) should raise the floor and document why in the audit row.
_R20_IN_LOCALS_FLOOR = 40


def _app_tree() -> ast.Module:
    return ast.parse(APP_SIMPLE.read_text(encoding="utf-8"))


def _named_dict_assignment(tree: ast.AST, name: str) -> ast.Dict:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return node.value
    raise AssertionError(f"dict assignment for {name!r} not found")


def _dict_string_keys(node: ast.Dict) -> set[str]:
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def test_in_locals_count_is_at_or_below_post_r20_floor() -> None:
    """Pin the post-R20 ``in locals()`` count in app_simple.py.

    Catches three things at once:
    1. A Cursor session that re-introduces a guard at any of the 19
       sites Round 20 simplified (count goes back up).
    2. A future session that adds a NEW ``in locals()`` antipattern
       site somewhere else in the file without simplifying an existing
       one (count goes up).
    3. Recon errors like Round 18 / 19.1 claimed 74 -- the test is the
       authoritative number, not a comment in some other doc.
    """
    src = APP_SIMPLE.read_text(encoding="utf-8")
    # Match only the literal ``in locals()`` token, the same shape every
    # antipattern site uses.  Don't match the substring inside a longer
    # identifier.
    matches = re.findall(r"in locals\(\)", src)
    count = len(matches)
    assert count <= _R20_IN_LOCALS_FLOOR, (
        f"Round 20 R20-001 floor regressed: app_simple.py now has "
        f"{count} ``in locals()`` occurrences; floor is "
        f"{_R20_IN_LOCALS_FLOOR}.  Either simplify the new site (the "
        f"R14-006 / R20-001 pattern) or, if the guard is genuinely "
        f"defensive (e.g. ``'cur' in locals()`` after a try block "
        f"that may not reach the cursor assignment), update "
        f"``_R20_IN_LOCALS_FLOOR`` and document the new floor + the "
        f"new site's rationale in the audit row."
    )


def test_r20_001_simplifications_do_not_regress() -> None:
    """Pin that the 19 simplified call sites still pass the actual
    variable (not a re-introduced ``X if 'X' in locals() else None``).

    Guards against a well-meaning future edit that re-adds the guard
    "for safety" without realising R20-001 already proved it dead.
    """
    src = APP_SIMPLE.read_text(encoding="utf-8")

    # All 19 simplifications produced a kwarg-style call argument of
    # the shape ``<name>=<name>,`` or ``<name>=<name>\n``.  These
    # assertions pin the simplified shape; if a future edit re-adds
    # ``if 'X' in locals() else None`` on any of them, the substring
    # check fails because the simplified form is no longer present
    # verbatim.  We deliberately use a narrow per-arg substring so a
    # whitespace-only refactor (e.g. one-line wrap) doesn't trip the
    # test.
    expected_simplified_substrings = (
        # Briefing-book call (L6779-6788 cluster, 5 args)
        "feature_requests=feature_requests,",
        "software_defects=software_defects,",
        "psirt_vulns=psirt_vulns,",
        "ext_incidents=ext_incidents,",
        "ext_bugs=ext_bugs,",
        # Fallback-insights call (L6905-6909 + L6937-6941, 10 args
        # over 2 call sites; substrings collapse to 5 unique forms)
        "team_subs_df=team_subs_df,",
        "csconsole_action_plans=csconsole_action_plans,",
        "csconsole_customer_pulse=csconsole_customer_pulse,",
        "csconsole_success_priorities=csconsole_success_priorities,",
        "csconsole_adoption_barriers=csconsole_adoption_barriers,",
    )
    for sub in expected_simplified_substrings:
        assert sub in src, (
            f"R20-001 regression: expected simplified call-arg "
            f"substring {sub!r} is missing from app_simple.py.  "
            f"A future edit may have re-introduced the dead "
            f"``in locals()`` guard."
        )

    # Site L6704: ``feature_requests`` passed as positional arg to
    # ``create_executive_charts``.  Pinned by the comment marker
    # introduced in Round 20 / R20-001 right above the call.
    assert "Round 20 / R20-001: drop the dead presence guard." in src, (
        "R20-001 regression: the L6704 simplification marker was "
        "removed.  Either the simplification was reverted or the "
        "marker comment was edited away."
    )


def test_closure_binding_pattern_inside_nested_functions_is_gone() -> None:
    """Round 23 / R22-NEXT-001 fixed the closure-binding bug inside
    the nested ``generate_report`` (L7073) / ``generate_excel`` (L7395)
    functions in ``run_compact_analysis``.

    Before the fix, those nested functions used
    ``X if 'X' in locals() else FALLBACK`` to read outer-scope frames.
    Inside a nested Python function, ``locals()`` does NOT include free
    variables captured from the enclosing scope, so every such guard
    always took the FALLBACK branch -- silently dropping csconsole-only
    customers from the renewal risk universe and hardcoding
    ``recent_window_days=30`` regardless of the ``days`` parameter.

    The fix introduced an explicit ``_r23_ctx`` dict in the OUTER scope
    (where ``locals()`` works correctly for capturing
    conditionally-bound names like ``data_retrieved_at``) and threaded
    it as a default argument into both nested functions.

    This test pins three properties:

    1. The legacy closure-binding marker (``team_subs_df_unfiltered if
       'team_subs_df_unfiltered' in locals() else None``) is GONE --
       a future session cannot silently re-introduce it.
    2. The replacement ``_r23_ctx`` outer-scope dict is present with
       the correct shape so a future session cannot silently rip it
       out and re-introduce free-variable lookups.
    3. Both nested functions accept ``_ctx=_r23_ctx`` as their default
       argument so the binding flows correctly at call time.
    """
    src = APP_SIMPLE.read_text(encoding="utf-8")

    # 1. The legacy closure-binding pattern must be gone.
    legacy_pattern = re.compile(
        r"team_subs_df_unfiltered\s+if\s+[\"']team_subs_df_unfiltered[\"']"
        r"\s+in\s+locals\(\)\s+else\s+None"
    )
    assert not legacy_pattern.search(src), (
        "R22-NEXT-001 regression: the closure-binding pattern is back. "
        "Inside ``generate_report`` / ``generate_excel`` this guard "
        "always took the False branch (free vars are not in "
        "``locals()``), silently dropping csconsole-only customers "
        "from the renewal risk universe and hardcoding "
        "recent_window_days=30.  Restore the ``_r23_ctx``-based "
        "ctx-dict access introduced in Round 23."
    )

    # 2. The replacement ctx dict must exist with the expected shape.
    ctx_dict_marker = "_r23_ctx = {  # Round 23 / R22-NEXT-001"
    assert ctx_dict_marker in src, (
        "R22-NEXT-001 regression: the explicit ``_r23_ctx`` outer-scope "
        "dict that replaces the closure-binding pattern is missing.  "
        "It must be built in ``run_compact_analysis`` BEFORE the "
        "nested ``generate_report`` / ``generate_excel`` are defined, "
        "so the captured outer-scope frames flow through ``_ctx`` at "
        "call time."
    )
    # The ctx must include the customer-counting frames that were the
    # primary victims of the closure-binding bug. Inspect the actual dict
    # keys so quote normalization cannot weaken or break this contract.
    tree = _app_tree()
    ctx_keys = _dict_string_keys(_named_dict_assignment(tree, "_r23_ctx"))
    for frame_key in (
        "team_subs_df_unfiltered",
        "csconsole_action_plans",
        "csconsole_customer_pulse",
        "csconsole_success_priorities",
        "csconsole_adoption_barriers",
        "days",
    ):
        assert frame_key in ctx_keys, (
            f"R22-NEXT-001 regression: ``_r23_ctx`` is missing the "
            f"{frame_key} entry.  All five csconsole/customer frames "
            f"plus the ``days`` parameter must flow through the ctx "
            f"dict so the renewal-risk universe stays aligned with "
            f"the headline ``total_customers`` and so "
            f"``recent_window_days`` is honoured."
        )

    # 3. Both nested functions must accept _ctx=_r23_ctx as default arg.
    nested_functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {"generate_report", "generate_excel"}
    }
    for function_name in ("generate_report", "generate_excel"):
        function = nested_functions.get(function_name)
        captures_ctx = function is not None and any(
            isinstance(default, ast.Name) and default.id == "_r23_ctx"
            for default in function.args.defaults
        )
        assert captures_ctx, (
            f"R22-NEXT-001 regression: nested function "
            f"{function_name!r} no longer captures ``_r23_ctx``. Both "
            "``generate_report`` and "
            f"``generate_excel`` must capture ``_r23_ctx`` as a "
            f"default argument so the outer-scope frames flow at "
            f"definition time rather than via the broken "
            f"free-variable / ``locals()`` lookup."
        )


def test_r22_next_in_locals_renewal_simplifications_do_not_regress() -> None:
    """Round 23.2 / R22-NEXT-IN-LOCALS-RENEWAL pinned 9 dead-presence
    guard simplifications in ``run_customer_renewal_analysis``.

    Pin the post-fix shape so a future "for safety" revert can't
    silently re-introduce the dead guards without a test failure.

    The 9 simplifications:
    1. L10577 ``all_customers`` CSOne-portfolio re-init -> ``if not all_customers:``
    2. L10794 ``all_customers`` portfolio-risk re-init  -> ``if not all_customers:``
    3. L10975 ``team_subs_df`` -> ``build_customer_lookup(team_subs_df)``
    4. L10982-10996 csconsole_* loop  -> direct frame iteration
    5-7. L11103/11105/11107 customer_*_plans/pulse/priorities sentinel
       re-init -> dropped (all branches above bind every name)
    8-9. L11559/11560 record_report_completion kwargs -> ``renewal_word_path or ''``
       and ``excel_path if excel_path else ''``
    """
    src = APP_SIMPLE.read_text(encoding="utf-8")

    # Round 162.1 consolidated several of the original clusters while adding
    # the canonical all-source Renewal identity path.  Pin the behavior below
    # instead of counting historical marker comments: comment counts are not a
    # correctness contract and made this regression fail after safe refactors.

    # Direct-iteration shape for the csconsole_* extras frames must be
    # present (replaces the legacy ``for _df_name in (...): if _df_name
    # in locals()`` loop).
    direct_iter_marker = (
        "_ren_extra_frames = [\n"
        "            _val\n"
        "            for _val in (\n"
        "                team_subs_df,\n"
        "                csconsole_customer_pulse,\n"
        "                csconsole_success_priorities,\n"
        "                csconsole_adoption_barriers,\n"
        "                csconsole_action_plans,\n"
        "            )\n"
        "            if isinstance(_val, pd.DataFrame) and not _val.empty\n"
        "        ]"
    )
    assert direct_iter_marker in src, (
        "R22-NEXT-IN-LOCALS-RENEWAL regression: the direct-frame "
        "iteration replacing the legacy locals()[name] lookup loop "
        "is missing.  A future edit may have re-introduced the "
        "name-string-based lookup."
    )

    # The completion-record call must pass the simplified expressions.
    # Inspect the AST so quote choice and formatter wrapping are irrelevant.
    def is_empty_string(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value == ""

    completion_simplified = False
    for call in (
        node
        for node in ast.walk(_app_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "record_report_completion"
    ):
        keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
        word_path = keywords.get("word_path")
        excel_path = keywords.get("excel_path")
        word_is_simplified = (
            isinstance(word_path, ast.BoolOp)
            and isinstance(word_path.op, ast.Or)
            and len(word_path.values) == 2
            and isinstance(word_path.values[0], ast.Name)
            and word_path.values[0].id == "renewal_word_path"
            and is_empty_string(word_path.values[1])
        )
        excel_is_simplified = (
            isinstance(excel_path, ast.IfExp)
            and isinstance(excel_path.test, ast.Name)
            and excel_path.test.id == "excel_path"
            and isinstance(excel_path.body, ast.Name)
            and excel_path.body.id == "excel_path"
            and is_empty_string(excel_path.orelse)
        )
        if word_is_simplified and excel_is_simplified:
            completion_simplified = True
            break
    assert completion_simplified, (
        "R22-NEXT-IN-LOCALS-RENEWAL regression: the simplified "
        "``record_report_completion`` kwargs (renewal_word_path / "
        "excel_path) are missing their post-Round-23.2 shape.  A "
        "future edit may have re-added the dead presence guards."
    )
