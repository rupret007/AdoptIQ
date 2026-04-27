"""Round 22 / R22-001 regression tests: every caller that builds
``portfolio_metrics`` with ``extra_customer_frames`` must thread the
SAME extras (and ``account_to_customer`` when available) through
``validate_report_consistency``, so the validator's customer
universe matches ``portfolio_metrics["total_customers"]`` exactly.

Why this test file exists
-------------------------
Round 21.1's hot spot #1 (``R21-NEXT-CONSISTENCY``) flagged that the
EI formatter render test was deliberately NOT passing
``_ei_extra_frames`` to the formatter as a workaround for a real
``ValueError: Portfolio metric mismatch`` raised by
``validate_report_consistency``.  The Round 21.1 handoff suspected the
disagreement was between the validator and ``build_portfolio_metrics``
and asked Claude to decide which side was canonical.

Round 22's investigation found the actual root cause: the validator
already accepts ``extra_frames`` and ``account_to_customer`` as kwargs
(``report_consistency.validate_report_consistency`` signature L56-71)
and routes them through ``cm.count_customers`` exactly the same way
``build_portfolio_metrics(extra_customer_frames=...)`` does.  The bug
was on the caller side: 4 of the 7 ``validate_report_consistency``
callsites built ``portfolio_metrics`` with extras but called the
validator WITHOUT extras.  That always produced a mismatch on any
realistic dataset where csconsole frames contributed customer names
not in AB ∪ CSOne.

The leader path at ``app_simple.py:18776`` has done it correctly
since Round 5/6 (passes both extras and account_to_customer).  Round
22 brings the other 4 callsites in line with the leader path:
- ``compact_report_formatter.create_compact_executive_report`` (L2752)
- ``executive_intelligence_formatter.create_executive_intelligence_report``
  (L1746)
- ``app_simple._create_enhanced_compact_report`` consistency block
  (L5649)
- ``app_simple.run_customer_renewal_analysis`` consistency block
  (L10975)

What this test pins
-------------------
1. **Source-text contract pin** — for each of the 4 fixed callsites,
   the source text contains the exact extras keyword being passed to
   ``validate_report_consistency`` from the same scope as the
   matching ``build_portfolio_metrics(extra_customer_frames=...)``
   call.  A future "cleanup" edit that removes the kwarg silently
   fails this test instead of silently re-introducing the production
   ``Portfolio metric mismatch`` error.
2. **Behavioural pin** — using the Round 19 golden fixture, drive
   ``validate_report_consistency`` with and without ``extra_frames``
   and assert that:
     - Without extras, the validator's ``total_customers`` is the
       AB+CSOne-only count (3 -- AcmeCorp, BetaInc, GammaLLC).
     - With extras, the validator's ``total_customers`` matches the
       full multi-source universe (5 -- adds DeltaCo, EpsilonInc).
     - PM built with extras has ``total_customers`` == 5; the
       extras-bearing validator call agrees.  This is what the Round
       22 fix unlocks for production formatters.
3. **No regression** — the Round 21.1 EI render test that
   deliberately did NOT pass extras is left untouched (still skipping
   the consistency-failure mode); a separate Round 22 test exercises
   the WITH-extras shape.

Notes
-----
* This test only pins the source-text shape and the validator's
  arithmetic.  It does NOT drive the full formatters end-to-end
  with extras (that would require live Snowflake context).  The
  Round 19 golden fixture covers per-helper KPI parity; this test
  covers the validator-vs-PM alignment that was its caller-side gap.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import canonical_metrics as cm  # noqa: E402
from report_consistency import validate_report_consistency  # noqa: E402
from tests.fixtures.round19.golden import (  # noqa: E402
    EXPECTED_KPIS,
    make_ab_df,
    make_csone_df,
    make_extra_frames,
)

COMPACT_FORMATTER = REPO_ROOT / "compact_report_formatter.py"
EI_FORMATTER = REPO_ROOT / "executive_intelligence_formatter.py"
APP_SIMPLE = REPO_ROOT / "app_simple.py"


# ---------------------------------------------------------------------------
# Source-text contract pins (1 test per fixed callsite)
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block_after(haystack: str, anchor: str, max_chars: int = 800) -> str:
    """Return the text window starting at ``anchor`` for substring scans."""
    idx = haystack.find(anchor)
    assert idx != -1, f"anchor not found in source: {anchor!r}"
    return haystack[idx : idx + max_chars]


def test_compact_formatter_threads_extras_to_validator() -> None:
    """``compact_report_formatter`` validator call must include
    ``extra_frames=_extra_customer_frames or None`` and
    ``account_to_customer=account_to_customer``."""
    src = _read(COMPACT_FORMATTER)
    block = _block_after(
        src,
        "consistency = validate_report_consistency(\n"
        "            ab_data,\n"
        "            csone_norm,\n",
    )
    assert "extra_frames=_extra_customer_frames or None" in block, (
        "R22-001 regression: compact_report_formatter no longer threads "
        "_extra_customer_frames into validate_report_consistency.  Without "
        "this kwarg the validator's customer universe will be AB+CSOne only "
        "and disagree with portfolio_metrics whenever an extras-only "
        "customer is present."
    )
    assert "account_to_customer=account_to_customer" in block, (
        "R22-001 regression: compact_report_formatter no longer threads "
        "account_to_customer into validate_report_consistency."
    )


def test_executive_intelligence_formatter_threads_extras_to_validator() -> None:
    """``executive_intelligence_formatter`` validator call must
    include ``extra_frames=_ei_extra_frames or None`` and
    ``account_to_customer=_account_to_customer``."""
    src = _read(EI_FORMATTER)
    block = _block_after(
        src,
        "consistency = validate_report_consistency(\n"
        "        ab_for_check,\n"
        "        csone_for_check,\n",
    )
    assert "extra_frames=_ei_extra_frames or None" in block, (
        "R22-001 regression: executive_intelligence_formatter no longer "
        "threads _ei_extra_frames into validate_report_consistency.  This "
        "was the original symptom of R21-NEXT-CONSISTENCY: the EI render "
        "test had to skip extras to avoid 'Portfolio metric mismatch'."
    )
    assert "account_to_customer=_account_to_customer" in block, (
        "R22-001 regression: executive_intelligence_formatter no longer "
        "threads _account_to_customer into validate_report_consistency."
    )


def test_app_simple_enhanced_compact_threads_extras_to_validator() -> None:
    """``app_simple._create_enhanced_compact_report`` validator call
    must include ``extra_frames=_enh_extra_frames or None``.  No
    account_to_customer in scope on this path; pass None acceptable."""
    src = _read(APP_SIMPLE)
    block = _block_after(src, "_enh_consistency = validate_report_consistency(")
    assert "extra_frames=_enh_extra_frames or None" in block, (
        "R22-001 regression: app_simple._create_enhanced_compact_report no "
        "longer threads _enh_extra_frames into validate_report_consistency."
    )


def test_app_simple_renewal_threads_extras_to_validator() -> None:
    """``app_simple.run_customer_renewal_analysis`` validator call
    must include ``extra_frames=_ren_extra_frames or None`` and
    ``account_to_customer=_ren_account_to_customer``."""
    src = _read(APP_SIMPLE)
    block = _block_after(src, "consistency_check = validate_report_consistency(")
    assert "extra_frames=_ren_extra_frames or None" in block, (
        "R22-001 regression: run_customer_renewal_analysis no longer "
        "threads _ren_extra_frames into validate_report_consistency."
    )
    assert "account_to_customer=_ren_account_to_customer" in block, (
        "R22-001 regression: run_customer_renewal_analysis no longer "
        "threads _ren_account_to_customer into validate_report_consistency."
    )


# ---------------------------------------------------------------------------
# Behavioural pin: validator-vs-PM alignment with the golden fixture
# ---------------------------------------------------------------------------


def test_validator_without_extras_undercounts_customer_universe() -> None:
    """Document the pre-R22 behaviour for posterity.

    With the Round 19 golden fixture's ab_df + csone_df only (no
    extras), the validator should compute total_customers == 3
    (AcmeCorp, BetaInc, GammaLLC -- AB-side names; csone_df adds
    only AcmeCorp + BetaInc which dedup against AB).  This is the
    universe the validator was using BEFORE Round 22 even when the
    formatter's portfolio_metrics included extras.
    """
    ab_df = make_ab_df()
    csone_df = make_csone_df()
    result = validate_report_consistency(ab_df, csone_df)
    assert result["metrics"]["total_customers"] == 3, (
        "Pre-R22 baseline: validator without extras must report "
        "total_customers == 3 (AB+CSOne-only universe).  If this "
        "changes, either the validator's count_customers contract "
        "moved, or the golden fixture's customer rows were edited."
    )


def test_validator_with_extras_matches_portfolio_metrics_universe() -> None:
    """The Round 22 fix unlock: when callers thread extras through
    the validator (as the 4 fixed callsites now do), the validator
    sees the same multi-source universe as
    ``build_portfolio_metrics(extra_customer_frames=...)`` and
    ``portfolio_metrics["total_customers"]`` == validator's
    ``metrics["total_customers"]``.  No more "Portfolio metric
    mismatch"."""
    ab_df = make_ab_df()
    csone_df = make_csone_df()
    extras = make_extra_frames()
    expected_total = EXPECTED_KPIS["total_customers"]  # 5

    pm = cm.build_portfolio_metrics(
        ab_df=ab_df,
        csone_df=csone_df,
        risk_profiles={},
        risk_scale=cm.RISK_SCALE_0_TO_100,
        extra_customer_frames=extras,
    )
    assert pm["total_customers"] == expected_total, (
        "Sanity: build_portfolio_metrics with extras should match "
        f"EXPECTED_KPIS['total_customers'] == {expected_total}."
    )

    result_with_extras = validate_report_consistency(
        ab_df,
        csone_df,
        portfolio_metrics=pm,
        extra_frames=extras,
    )
    assert result_with_extras["metrics"]["total_customers"] == expected_total, (
        "R22-001 contract: the validator must compute the SAME "
        f"total_customers ({expected_total}) as build_portfolio_metrics "
        "when the caller threads the same extras through both helpers."
    )
    assert result_with_extras["is_valid"], (
        "R22-001 contract: the validator must NOT raise 'Portfolio "
        "metric mismatch' when extras are threaded consistently.  "
        f"Errors observed: {result_with_extras.get('errors')}"
    )


def test_validator_without_extras_disagrees_with_pm_with_extras() -> None:
    """The exact failure mode Round 22 fixes: when the formatter
    builds portfolio_metrics with extras but the validator runs
    without, the validator's total_customers (3) disagrees with
    portfolio_metrics["total_customers"] (5) and the validator
    reports an error.  This test pins the pre-R22 misalignment so
    a future regression that re-introduces it (by silently
    dropping the extras kwarg from any of the 4 fixed callsites)
    surfaces immediately."""
    ab_df = make_ab_df()
    csone_df = make_csone_df()
    extras = make_extra_frames()

    pm = cm.build_portfolio_metrics(
        ab_df=ab_df,
        csone_df=csone_df,
        risk_profiles={},
        risk_scale=cm.RISK_SCALE_0_TO_100,
        extra_customer_frames=extras,
    )
    # Validator called WITHOUT extras (the buggy pre-R22 caller shape).
    result_without_extras = validate_report_consistency(
        ab_df,
        csone_df,
        portfolio_metrics=pm,
    )
    assert result_without_extras["metrics"]["total_customers"] == 3, (
        "Pre-R22 baseline: validator without extras must still see "
        "AB+CSOne-only universe (3)."
    )
    assert pm["total_customers"] == 5, (
        "Sanity: PM built with extras must show 5."
    )
    assert not result_without_extras["is_valid"], (
        "Pre-R22 contract: when PM has 5 customers but validator only "
        "sees 3, validator must raise an error.  If this assertion ever "
        "fails, the validator silently swallowed the mismatch and the "
        "Round 22 fix has been silently undone (production reports may "
        "ship with disagreeing totals)."
    )
    # The mismatch must mention total_customers explicitly.
    error_text = "; ".join(result_without_extras.get("errors", []))
    assert "total_customers" in error_text, (
        "Pre-R22 baseline: error message must reference total_customers "
        f"so a future debugger can find this site quickly.  Got: {error_text!r}"
    )
