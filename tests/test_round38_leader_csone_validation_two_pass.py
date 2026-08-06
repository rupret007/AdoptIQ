"""Round 38 - Leader Report `csone missing or empty` two-pass validation.

Why this file exists
--------------------
Pre-Round-38 the leader-report background worker (``run_leader_report_generation``
in ``app_simple.py``) called ``raise_validation_error_if_invalid`` BEFORE the
CSOne file was actually loaded.  When the OneDrive autodiscovery fallback
(``get_latest_csone_from_folder``) returned ANY ``.xlsx``, the worker set
``csone_file_provided=True`` and passed an empty ``pd.DataFrame()`` for
``csone_data``.  That combination promoted ``csone`` to the required-source
set inside the validator, hit the empty-data branch
(``data_source_validator.py:248-249``), and aborted the leader report with::

    Leader report cannot be generated: csone missing or empty.
    See logs for details.

even though the CSOne file existed and would have loaded fine if the
worker had simply opened it before validating.

Round 14 / Phase 2.4 unintentionally exposed this (the prior bare-name
``locals()`` membership check was always-False dead code that masked the
load-order bug); Round 38 fixes the load-order itself by switching the
leader path to a two-pass design:

* **Pass 1** (early, before the CSOne file is loaded): validate ONLY
  Snowflake + ``team_subscriptions``.  CSOne is intentionally NOT
  promoted to required at this stage regardless of whether a path was
  resolved upstream.
* **CSOne load relocated** up to immediately after Pass 1 so the worker
  has a real ``csone_df`` to validate.
* **Pass 2** (later, after load): fail loud ONLY when the operator
  EXPLICITLY uploaded a CSOne file via the ``/start_leader_report``
  endpoint.  An autodiscovered file that produced zero scoped rows is
  logged + emitted as a ``partial_data_warnings`` entry but does not
  abort the report.

This file pins all of those properties via source-level inspection of
``app_simple.run_leader_report_generation`` and the ``/start_leader_report``
endpoint so a future "ruff-clean" rewrite can't silently re-introduce the
load-order bug a third time.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source, index_in_source

import inspect
import re

import app_simple


_LEADER_WORKER_SRC = inspect.getsource(app_simple.run_leader_report_generation)
_LEADER_ENDPOINT_SRC = inspect.getsource(app_simple.start_leader_report)


def test_pass1_validator_excludes_csone_from_required_sources() -> None:
    """Round 38 / Phase 2: Pass 1 must validate Snowflake +
    team_subscriptions ONLY.  ``csone`` MUST NOT appear in the Pass 1
    required-sources list, otherwise the load-order bug returns.

    The leader worker now uses an explicit named list
    ``_leader_required_pass1 = ['snowflake', 'team_subscriptions']``
    (or equivalent literal) for the early call.  Pin the literal so a
    future edit that adds ``'csone'`` to the early required set fails
    loudly here instead of in production.
    """
    # The early required list must contain snowflake + team_subscriptions
    # and MUST NOT contain csone.
    assert_in_source(_LEADER_WORKER_SRC, "_leader_required_pass1", label='_LEADER_WORKER_SRC')
    pass1_list_match = re.search(
        r"_leader_required_pass1\s*=\s*\[(.*?)\]",
        _LEADER_WORKER_SRC,
        re.DOTALL,
    )
    assert pass1_list_match is not None, (
        "Round 38 / Phase 2 regression: ``_leader_required_pass1`` "
        "must be a literal list assignment so this test can verify "
        "its contents at the source level."
    )
    pass1_contents = pass1_list_match.group(1)
    assert "'snowflake'" in pass1_contents or '"snowflake"' in pass1_contents, (
        "Round 38 / Phase 2 regression: Pass 1 must require "
        "``snowflake`` -- without it a Snowflake outage silently "
        "produces a leader report full of zeros."
    )
    assert (
        "'team_subscriptions'" in pass1_contents
        or '"team_subscriptions"' in pass1_contents
    ), (
        "Round 38 / Phase 2 regression: Pass 1 must require "
        "``team_subscriptions`` -- without it an empty roster ships "
        "a leader report with no team to analyse."
    )
    assert "'csone'" not in pass1_contents and '"csone"' not in pass1_contents, (
        "Round 38 / Phase 2 regression: Pass 1 must NOT promote "
        "``csone`` to required.  At Pass 1 the CSOne file has not "
        "been loaded yet and ``csone_data`` is the empty placeholder, "
        "so requiring ``csone`` re-introduces the false-positive "
        "``csone missing or empty`` error Round 38 fixed."
    )


def test_pass1_validator_passes_csone_file_provided_false() -> None:
    """Round 38 / Phase 2: Pass 1 must explicitly pass
    ``csone_file_provided=False``.  Round 14 / Phase 2.4's
    ``locals().get('csone_file')`` heuristic is what regressed the
    load-order behaviour in the first place; replacing it with a hard
    literal removes any ambiguity about what value is being computed
    at Pass 1.
    """
    # The Pass 1 call must contain the literal ``csone_file_provided=False``.
    pass1_call_match = re.search(
        r"raise_validation_error_if_invalid\s*\(\s*"
        r"report_type=['\"]leader['\"],\s*"
        r"snowflake_ctx=ctx,\s*"
        r"team_subs_df=team_subs_df,\s*"
        r"ab_data=pd\.DataFrame\(\),\s*"
        r"csone_data=pd\.DataFrame\(\),\s*"
        r"required_sources=_leader_required_pass1,\s*"
        r"(?:#[^\n]*\n\s*)*"
        r"csone_file_provided=False",
        _LEADER_WORKER_SRC,
        re.DOTALL,
    )
    assert pass1_call_match is not None, (
        "Round 38 / Phase 2 regression: the Pass 1 validator call no "
        "longer hard-codes ``csone_file_provided=False`` against an "
        "empty placeholder ``csone_data=pd.DataFrame()``.  Either the "
        "two-pass split was reverted, or someone re-introduced the "
        "Round 14 / Phase 2.4 ``locals().get('csone_file')`` "
        "heuristic.  Both reintroduce the ``csone missing or empty`` "
        "false-positive Round 38 fixed."
    )


def test_pass2_runs_only_when_csone_file_was_uploaded() -> None:
    """Round 38 / Phase 2: Pass 2 must be guarded by
    ``status.get('csone_file_was_uploaded')`` so it fires ONLY when the
    operator explicitly uploaded a CSOne file via the endpoint.

    OneDrive-autodiscovered hits MUST NOT raise from Pass 2; that's
    what the autodiscovered_empty_after_scope partial_data_warning
    branch is for (pinned in a separate test below).
    """
    # The Pass 2 raising call must be inside an ``if`` block keyed on
    # ``csone_file_was_uploaded``.  We pin both the guard variable
    # name and the ``required_sources=['csone']`` shape -- a future
    # edit that drops the guard would let autodiscovery hits raise
    # again.
    assert_in_source(_LEADER_WORKER_SRC, "_csone_was_uploaded", label='_LEADER_WORKER_SRC')
    assert (
        "csone_file_was_uploaded" in _LEADER_WORKER_SRC
    ), (
        "Round 38 / Phase 2 regression: the Pass 2 guard variable "
        "is no longer derived from "
        "``status.get('csone_file_was_uploaded')``.  Restore the "
        "endpoint-driven provenance flag."
    )
    # The Pass 2 validator call must use required_sources=['csone'].
    # Locate the ``if _csone_was_uploaded:`` block and assert both the
    # validator call AND its csone-only required-sources literal are
    # inside it.
    guard_pos = _LEADER_WORKER_SRC.find("if _csone_was_uploaded:")
    assert guard_pos > 0, (
        "Round 38 / Phase 2 regression: the worker no longer guards "
        "Pass 2 with ``if _csone_was_uploaded:``.  Restore the guard "
        "so autodiscovery hits cannot fail loud."
    )
    # The matching elif (autodiscovery soft-fail) marks the end of the
    # Pass 2 guarded block.
    elif_pos = _LEADER_WORKER_SRC.find(
        "elif csone_df.empty and csone_path:",
        guard_pos,
    )
    assert elif_pos > guard_pos, (
        "Round 38 / Phase 2 regression: the autodiscovery soft-fail "
        "``elif`` branch is missing AFTER the ``if _csone_was_uploaded:`` "
        "guard.  Without it, autopicked-but-empty CSOne files have "
        "nowhere to land -- they would either be silently dropped or "
        "(if the if-guard regresses) re-introduce the original "
        "fail-loud false-positive."
    )
    pass2_block = _LEADER_WORKER_SRC[guard_pos:elif_pos]
    assert_in_source(pass2_block, "raise_validation_error_if_invalid", label='pass2_block')
    assert (
        "required_sources=['csone']" in pass2_block
        or 'required_sources=["csone"]' in pass2_block
    ), (
        f"Round 38 / Phase 2 regression: Pass 2 validator call no "
        f"longer uses ``required_sources=['csone']``.  Widening this "
        f"list re-checks Snowflake/team_subs at Pass 2 (already "
        f"validated at Pass 1) and shrinking it to empty silently "
        f"accepts empty explicit uploads.  Pass 2 block was:\n{pass2_block!r}"
    )
    assert_in_source(pass2_block, "csone_file_provided=True", label='pass2_block')
    assert_in_source(pass2_block, "csone_data=csone_df", label='pass2_block')


def test_autodiscovered_empty_after_scope_emits_partial_data_warning() -> None:
    """Round 38 / Phase 2: when the OneDrive autodiscovery fallback
    picked up a CSOne file but the loaded DataFrame is empty (zero
    scoped rows), the worker must NOT raise.  Instead it must:

    1. Log a warning naming the autodiscovered file.
    2. Append a ``partial_data_warnings`` entry tagged
       ``kind='autodiscovered_empty_after_scope'`` so the report
       banner is honest about the degraded TAC sections.
    3. Continue with the report.

    Pin all three properties so the autodiscovery soft-fail path
    cannot silently regress to the pre-Round-38 fail-loud behaviour.
    """
    # The elif branch must exist with the right shape.
    elif_match = re.search(
        r"elif\s+csone_df\.empty\s+and\s+csone_path:",
        _LEADER_WORKER_SRC,
    )
    assert elif_match is not None, (
        "Round 38 / Phase 2 regression: the "
        "``elif csone_df.empty and csone_path:`` autodiscovery "
        "soft-fail branch is missing.  Without it, an autodiscovered "
        "file that scoped to zero rows would either raise from Pass "
        "2 (if the guard inversion regression returns) or silently "
        "produce a degraded report with no honest banner."
    )
    # The branch must add a partial_data_warnings entry of the
    # correct kind.
    assert_in_source(_LEADER_WORKER_SRC, "_r30_leader_partial_warnings.append", label='_LEADER_WORKER_SRC')
    assert_in_source(_LEADER_WORKER_SRC, "'autodiscovered_empty_after_scope'", label='_LEADER_WORKER_SRC')
    # And the warning log must exist so operators can audit which
    # autodiscovered file produced the empty result.
    assert_in_source(_LEADER_WORKER_SRC, "autodiscovered CSOne file", label='_LEADER_WORKER_SRC')


def test_csone_load_block_runs_before_pass2() -> None:
    """Round 38 / Phase 3: the CSOne file load block (path resolution
    + ``load_csone_excel`` + scope filter) must execute BEFORE the
    Pass 2 validation call.  Pre-Round-38 the load happened *after*
    validation, which is the load-order bug Round 38 fixes.

    Pin the ordering by source-byte position: ``load_csone_excel``
    must appear in the worker source before the
    ``required_sources=['csone']`` Pass 2 call.
    """
    load_pos = index_in_source(_LEADER_WORKER_SRC, "load_csone_excel(csone_path)")
    pass2_pos = index_in_source(_LEADER_WORKER_SRC, 'required_sources=["csone"]')
    assert load_pos > 0, (
        "Round 38 / Phase 3 regression: the CSOne load call "
        "``load_csone_excel(csone_path)`` is missing from the leader "
        "worker source -- the load was relocated up in Round 38 / "
        "Phase 3 and must remain present."
    )
    assert pass2_pos > 0, (
        "Round 38 / Phase 2 regression: the Pass 2 validator call "
        "(``required_sources=['csone']``) is missing from the leader "
        "worker source.  Without Pass 2, explicit uploads that scoped "
        "to zero rows ship a silent zero-TAC leader report -- the "
        "exact fail-quiet regression Round 2 / Phase 4.3 was "
        "originally introduced to prevent."
    )
    assert load_pos < pass2_pos, (
        f"Round 38 / Phase 3 regression: ``load_csone_excel`` "
        f"appears at byte offset {load_pos} in the leader worker "
        f"source, but the Pass 2 validator call appears at "
        f"{pass2_pos}.  The load MUST happen before Pass 2 so the "
        f"validator sees the real loaded ``csone_df``, not the empty "
        f"placeholder.  This is the load-order bug Round 38 fixed."
    )


def test_endpoint_persists_csone_file_was_uploaded_flag() -> None:
    """Round 38 / Phase 1: the ``/start_leader_report`` endpoint must
    write ``csone_file_was_uploaded`` into the analysis status dict so
    the worker's Pass 2 guard can read it.

    Pin the endpoint shape so a future edit that drops the
    provenance write breaks here instead of breaking Pass 2 silently.
    """
    assert_in_source(_LEADER_ENDPOINT_SRC, "csone_file_explicit", label='_LEADER_ENDPOINT_SRC')
    assert_in_source(_LEADER_ENDPOINT_SRC, "csone_file_autopicked", label='_LEADER_ENDPOINT_SRC')
    assert_in_source(_LEADER_ENDPOINT_SRC, "'csone_file_was_uploaded': bool(csone_file_explicit)", label='_LEADER_ENDPOINT_SRC')
