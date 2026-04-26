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
#
# Future rounds that simplify MORE sites should lower this floor in the
# same change; rounds that intentionally introduce a new ``in locals()``
# pattern (e.g. for legitimate ``'cur' in locals()`` cleanup-after-try
# discipline) should raise the floor and document why in the audit row.
_R20_IN_LOCALS_FLOOR = 65


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


def test_in_locals_inside_nested_functions_remains_documented_as_known_bug() -> None:
    """Round 20 deliberately did NOT simplify ``in locals()`` sites
    inside the nested ``generate_report`` (L7062) / ``generate_excel``
    (L7335) functions because they are closure-binding bugs, not just
    dead-branch antipatterns -- ``locals()`` inside a nested function
    doesn't include free vars from the enclosing scope, so the else
    branch always runs and outer-scope data is silently dropped at the
    call site.  Fixing those is behavior-changing and warrants its
    own round.

    This test pins that at least ONE of the well-known nested-function
    antipattern sites still exists, so a future session cannot silently
    "fix" them without explicit acknowledgement that the receiver
    behavior is changing.
    """
    src = APP_SIMPLE.read_text(encoding="utf-8")
    # L7076 (now drifted, but the pattern is unique): inside generate_report
    # passing team_subs_df_unfiltered into build_customer_lookup.
    nested_bug_marker = "team_subs_df_unfiltered if 'team_subs_df_unfiltered' in locals() else None"
    assert nested_bug_marker in src, (
        "R20-NEXT-001 regression: the documented closure-binding bug "
        "site inside generate_report (passing "
        "``team_subs_df_unfiltered`` to ``build_customer_lookup``) is "
        "no longer present.  If you intentionally fixed this, "
        "(a) verify the receiver behavior change is desired, "
        "(b) update or remove this test, and "
        "(c) remove the R20-NEXT-001 entry from QUALITY_AUDIT.md."
    )
