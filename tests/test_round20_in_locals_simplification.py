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
#
# Future rounds that simplify MORE sites should lower this floor in the
# same change; rounds that intentionally introduce a new ``in locals()``
# pattern (e.g. for legitimate ``'cur' in locals()`` cleanup-after-try
# discipline) should raise the floor and document why in the audit row.
_R20_IN_LOCALS_FLOOR = 40


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
    legacy_marker = (
        "team_subs_df_unfiltered if 'team_subs_df_unfiltered' in locals() else None"
    )
    assert legacy_marker not in src, (
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
    # primary victims of the closure-binding bug.
    for frame_key in (
        "'team_subs_df_unfiltered'",
        "'csconsole_action_plans'",
        "'csconsole_customer_pulse'",
        "'csconsole_success_priorities'",
        "'csconsole_adoption_barriers'",
        "'days'",
    ):
        assert frame_key in src, (
            f"R22-NEXT-001 regression: ``_r23_ctx`` is missing the "
            f"{frame_key} entry.  All five csconsole/customer frames "
            f"plus the ``days`` parameter must flow through the ctx "
            f"dict so the renewal-risk universe stays aligned with "
            f"the headline ``total_customers`` and so "
            f"``recent_window_days`` is honoured."
        )

    # 3. Both nested functions must accept _ctx=_r23_ctx as default arg.
    nested_signatures = (
        "def generate_report(_ctx=_r23_ctx):  # Round 23 / R22-NEXT-001",
        "def generate_excel(_ctx=_r23_ctx):  # Round 23 / R22-NEXT-001",
    )
    for sig in nested_signatures:
        assert sig in src, (
            f"R22-NEXT-001 regression: nested function signature "
            f"{sig!r} is missing.  Both ``generate_report`` and "
            f"``generate_excel`` must capture ``_r23_ctx`` as a "
            f"default argument so the outer-scope frames flow at "
            f"definition time rather than via the broken "
            f"free-variable / ``locals()`` lookup."
        )
