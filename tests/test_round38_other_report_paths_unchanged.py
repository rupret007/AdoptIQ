"""Round 38 - audit pin: other report paths still pass real-loaded
``csone_data`` to ``raise_validation_error_if_invalid``.

Round 38's two-pass-validation fix is intentionally scoped to the
**leader** report only.  The audit performed during plan-time
verified that the other four report paths
(compact / comprehensive / renewal / subscription) load CSOne BEFORE
they validate, so the load-order bug Round 38 fixes is not reachable
on those paths -- the validator already sees the real DataFrame and
not an empty placeholder.

This test file exists to pin that audit verdict by source-level
inspection: a future refactor that accidentally re-introduces the
load-order bug on any of those paths (e.g. by hoisting a validation
call above its CSOne load) trips here, NOT in production.

Specifically, this file pins:

* ``run_compact_analysis``       -> ``csone_data=csone_df``       (real loaded)
* ``run_comprehensive_analysis`` -> ``csone_data=csone_df``       (real loaded)
* ``run_customer_renewal_analysis`` -> ``csone_data=customer_csone`` (real loaded)
* ``run_subscription_analysis``  -> ``csone_data=pd.DataFrame()`` BUT
                                    ``required_sources=['team_subscriptions']``
                                    (no csone in required, so the empty
                                    placeholder is harmless)

The leader call is the **only** one allowed to pass
``csone_data=pd.DataFrame()`` paired with ``csone_file_provided=False``
into Pass 1 (because Pass 2 covers the real check after the load).
"""
from __future__ import annotations

import inspect
import re

import app_simple


def _required_sources_in(call_text: str) -> list[str]:
    """Extract the literal contents of ``required_sources=[...]`` from
    a single validator call, returning ``[]`` if the kwarg is absent
    (which means the validator defaults to its built-in policy)."""
    match = re.search(
        r"required_sources\s*=\s*(?:validation_required_sources|\[(.*?)\])",
        call_text,
        re.DOTALL,
    )
    if match is None:
        return []
    if match.group(1) is None:
        # Hit the ``validation_required_sources`` literal-name branch;
        # caller pins that one separately by inspecting the local var.
        return ["__indirect__"]
    raw = match.group(1)
    return re.findall(r"['\"]([^'\"]+)['\"]", raw)


def _get_validator_call(func_src: str) -> str:
    """Return the source text of the FIRST
    ``raise_validation_error_if_invalid(...)`` call in ``func_src``.

    Round 38 introduced two such calls in the leader worker (Pass 1
    early, Pass 2 after load).  Other report paths still have exactly
    one; this helper returns the first match for both cases so the
    audit assertions below can reason about the early validation
    shape uniformly.
    """
    match = re.search(
        r"raise_validation_error_if_invalid\s*\((.*?)\n\s{0,16}\)",
        func_src,
        re.DOTALL,
    )
    assert match is not None, (
        "expected at least one raise_validation_error_if_invalid call "
        "in the function source"
    )
    return match.group(0)


def test_compact_validator_call_uses_real_csone_df() -> None:
    """``run_compact_analysis`` must pass ``csone_data=csone_df`` (the
    loaded DataFrame), NOT ``csone_data=pd.DataFrame()``.  The compact
    path loads CSOne earlier in the function and the validator sees
    the real frame at validation time -- the audit pin guards against
    a future refactor that hoists this call above the load.
    """
    src = inspect.getsource(app_simple.run_compact_analysis)
    call = _get_validator_call(src)
    assert "csone_data=csone_df" in call, (
        f"Round 38 audit regression: ``run_compact_analysis`` no "
        f"longer passes ``csone_data=csone_df`` to the validator.  "
        f"Either the load was moved BELOW the validator (re-introducing "
        f"the load-order bug Round 38 fixed on the leader path) or "
        f"the call signature changed.  Captured call:\n{call!r}"
    )
    assert "csone_data=pd.DataFrame()" not in call, (
        "Round 38 audit regression: ``run_compact_analysis`` is now "
        "passing an empty placeholder to the validator.  This is "
        "exactly the load-order bug Round 38 fixed on the leader "
        "path; do NOT propagate it to compact -- restore the "
        "real-loaded ``csone_df`` argument."
    )


def test_comprehensive_validator_call_uses_real_csone_df() -> None:
    """Same audit as compact: ``run_comprehensive_analysis`` must pass
    the loaded ``csone_df``.
    """
    src = inspect.getsource(app_simple.run_comprehensive_analysis)
    call = _get_validator_call(src)
    assert "csone_data=csone_df" in call, (
        f"Round 38 audit regression: ``run_comprehensive_analysis`` "
        f"no longer passes ``csone_data=csone_df`` to the validator. "
        f"Captured call:\n{call!r}"
    )
    assert "csone_data=pd.DataFrame()" not in call, (
        "Round 38 audit regression: ``run_comprehensive_analysis`` "
        "is now passing an empty placeholder to the validator.  See "
        "the compact-test docstring for details on the load-order bug."
    )


def test_renewal_validator_call_uses_real_customer_csone() -> None:
    """Renewal path uses ``customer_csone`` (a per-customer CSOne
    slice loaded above the call).  Audit-pin that the call still
    reads ``csone_data=customer_csone`` so a hoist would trip here.
    """
    src = inspect.getsource(app_simple.run_customer_renewal_analysis)
    call = _get_validator_call(src)
    assert "csone_data=customer_csone" in call, (
        f"Round 38 audit regression: ``run_customer_renewal_analysis`` "
        f"no longer passes ``csone_data=customer_csone`` to the "
        f"validator.  The renewal path's load happens above this call "
        f"site (see ``customer_csone`` initialization); a future "
        f"refactor that hoists the validator above the load would "
        f"replicate the leader-report load-order bug.  Captured call:\n{call!r}"
    )
    assert "csone_data=pd.DataFrame()" not in call, (
        "Round 38 audit regression: renewal validator call is now "
        "passing an empty placeholder.  Restore ``customer_csone``."
    )


def test_subscription_validator_does_not_require_csone() -> None:
    """Subscription path is the one exception that intentionally
    passes ``csone_data=pd.DataFrame()`` to the validator -- BUT only
    because its ``required_sources`` list does NOT include ``csone``,
    so the empty placeholder is never compared against the
    "required-but-empty" branch in the validator.

    Pin both halves of that contract: if a future edit adds
    ``'csone'`` to the subscription required-sources list without
    ALSO loading the file first, the empty placeholder would trigger
    the same false-positive Round 38 fixed on the leader path.
    """
    src = inspect.getsource(app_simple.run_subscription_analysis)
    call = _get_validator_call(src)
    sources = _required_sources_in(call)
    assert sources, (
        "Round 38 audit regression: ``run_subscription_analysis`` "
        "validator call no longer specifies a literal "
        "``required_sources=[...]`` kwarg.  Without it the validator "
        "falls back to its built-in policy, which DOES include "
        "csone for some report types -- the empty placeholder this "
        "path passes would then trigger the false-positive."
    )
    assert "team_subscriptions" in sources, (
        f"Round 38 audit regression: subscription required_sources "
        f"no longer includes ``team_subscriptions``.  Got: {sources!r}"
    )
    assert "csone" not in sources, (
        f"Round 38 audit regression: subscription path now requires "
        f"``csone`` ({sources!r}) but still passes "
        f"``csone_data=pd.DataFrame()``.  Either drop ``csone`` from "
        f"the required list OR load the file BEFORE the validator "
        f"call (see the leader-path two-pass design as the reference "
        f"pattern for fixing the latter)."
    )


def test_only_leader_pass1_is_allowed_to_pair_empty_csone_with_required_sources() -> None:
    """Whole-file audit: across ``app_simple.py`` the ONLY validator
    call allowed to pass ``csone_data=pd.DataFrame()`` is the leader
    Pass 1 call (which uses ``required_sources=_leader_required_pass1``,
    a snowflake+team_subs-only list with NO csone) AND the leader
    optional-fetch-error re-validation call right after it (same
    shape).  The subscription call is also allowed because it uses an
    explicit ``required_sources=['team_subscriptions']`` literal.

    Any other call in the file that passes both
    ``csone_data=pd.DataFrame()`` AND a required-sources list that
    INCLUDES ``csone`` is the load-order bug Round 38 fixed -- this
    test trips loud if a future edit re-introduces it anywhere.
    """
    import os

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_path = os.path.join(repo_root, "app_simple.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        full_src = fh.read()

    # Find every raise_validation_error_if_invalid call and inspect its body.
    pattern = re.compile(
        r"raise_validation_error_if_invalid\s*\((.*?)\n\s{0,20}\)",
        re.DOTALL,
    )
    offending_sites: list[str] = []
    for m in pattern.finditer(full_src):
        call = m.group(0)
        body = m.group(1)
        if "csone_data=pd.DataFrame()" not in body:
            continue
        # Allowed shape A: leader Pass 1 (uses
        # ``required_sources=_leader_required_pass1``).
        if "required_sources=_leader_required_pass1" in body:
            continue
        # Allowed shape B: subscription
        # (uses ``required_sources=['team_subscriptions']`` -- no csone).
        sources = _required_sources_in(call)
        if sources and "csone" not in sources:
            continue
        # Otherwise: this is the bug.
        offending_sites.append(call[:240])

    assert not offending_sites, (
        "Round 38 audit regression: found "
        f"{len(offending_sites)} validator call(s) in app_simple.py "
        "that pass ``csone_data=pd.DataFrame()`` paired with a "
        "required-sources list that includes ``csone`` (or that "
        "defaults to the validator's built-in policy).  This is the "
        "exact load-order bug Round 38 fixed on the leader path.  "
        "Either load the CSOne file BEFORE this validator call OR "
        "drop ``csone`` from required_sources for this pre-load "
        "pass.  Offending sites (truncated to 240 chars each):\n\n"
        + "\n\n---\n\n".join(offending_sites)
    )
