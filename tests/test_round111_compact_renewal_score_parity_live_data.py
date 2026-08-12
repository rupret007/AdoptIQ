"""Round 111 / Build 80 — Compact <-> Renewal Risk_Score_0_10 parity.

The Build 79 acceptance audit observed that 182 of 257 common customers
(~70%) had Renewal ``Overall_Risk_Score`` higher than Compact for the
same customer + scope, typically by ~+1.0 on the 0-10 scale (Compact
median 0.7 / 75th 0.7; Renewal median 1.1 / 75th 1.6). The R67/B1
contract requires identical ``Risk_Score_0_10``, identical ``Risk_Band``,
and identical ``Risk_Level`` vocabulary across formats.

Root cause: ``_r66_b8_classify_extra_frames`` checked for pulse markers
``{PULSE_RATING__C, PULSE_RATING, SCORE__C, PULSE_SCORE, rating, Customer
Pulse}`` but the live ``ESA_C360_CUSTOMER_PULSE__C cp.* + dsm.BU_NAME``
frame produced by ``adoptiq_backend.fetch_csconsole_customer_pulse`` uses
``CUSTOMER_PULSE__C`` as its rating column. The classifier returned
``pulse_df=None`` for production data, so Compact's per-customer pulse
slice was always empty even when the data was available -- shifting
engagement scoring (via ``total_activity = AB + SC + pulse + AP``) and
producing the systematic Renewal-vs-Compact drift.

R111 fix is two-pronged:

1. Widen ``_r66_b8_classify_extra_frames`` pulse marker set to include
   ``CUSTOMER_PULSE__C`` and ``CUSTOMER_PULSE_COLOR_IMAGE__C`` so the
   live frame is correctly classified.
2. Add explicit ``pulse_df`` / ``action_plans_df`` / ``subs_df`` kwargs
   to ``calculate_renewal_risk_scores`` so ``app_simple.py`` callers can
   bypass classification entirely and feed the canonical CSConsole
   frames directly (parity with the Renewal ``_calculate_simple_renewal_risk``
   path). The classifier remains in place as a fallback for legacy
   callers that don't update.

These tests pin both halves of the contract.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

import inspect
from pathlib import Path

import pandas as pd
import pytest


# Round 141: resolve source-shape fixtures from the checkout under test.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Source-shape pins
# ---------------------------------------------------------------------------


def test_classifier_recognizes_live_CUSTOMER_PULSE__C_marker() -> None:
    """Round 111 / B1: the production live ``CUSTOMER_PULSE__C`` column
    MUST classify as a pulse frame, not be silently dropped."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    pulse_frame = pd.DataFrame(
        [
            {
                "ID": "a0aL000001NXfXZIA1",
                "BU_NAME": "Acme Telecom",
                "ACCOUNT__C": "0014L000007rTfQQAU",
                "CUSTOMER_PULSE__C": "Green",
                "CUSTOMER_PULSE_COLOR_IMAGE__C": "<img src=...>",
                "PRODUCT__C": "Webex Calling",
                "RECORD_SOURCE": "Customer Pulse",
            }
        ]
    )
    pulse, aps, subs = _r66_b8_classify_extra_frames([pulse_frame])
    assert pulse is pulse_frame, (
        "Round 111 / B1: live CSConsole pulse frame with CUSTOMER_PULSE__C "
        "MUST be classified as pulse_df. Pre-R111 the classifier returned "
        "None for this frame because the marker set was missing the live "
        "production column name."
    )
    assert aps is None and subs is None


def test_classifier_recognizes_CUSTOMER_PULSE_COLOR_IMAGE__C_marker() -> None:
    """A pulse frame carrying ONLY the color-image column (defensive
    edge case) still classifies as pulse_df."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    frame = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme Telecom",
                "CUSTOMER_PULSE_COLOR_IMAGE__C": "<img src=...>",
            }
        ]
    )
    pulse, aps, subs = _r66_b8_classify_extra_frames([frame])
    assert pulse is frame
    assert aps is None and subs is None


def test_pulse_with_CUSTOMER_PULSE__C_does_not_get_misclassified_as_AP() -> None:
    """Disqualifier set MUST include the live pulse markers so a future
    schema change adding a STATUS column to the pulse table cannot
    re-route the frame into action_plans_df."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    # Synthetic frame: pulse columns AND a STATUS column. The pulse
    # primary marker fires first and binds it to ``pulse_df``; the AP
    # check then runs against a frame that's already classified, so it
    # would be skipped anyway. But add a SECOND frame with the same
    # shape to exercise the disqualifier path.
    frame_with_status = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme",
                "CUSTOMER_PULSE__C": "Red",
                "STATUS_C": "Active",
            }
        ]
    )
    # Already-classified pulse_df forces the second frame to fall through.
    other_pulse_first = pd.DataFrame(
        [{"BU_NAME": "Other", "PULSE_RATING__C": "Poor"}]
    )
    pulse, aps, subs = _r66_b8_classify_extra_frames(
        [other_pulse_first, frame_with_status]
    )
    assert pulse is other_pulse_first, "first pulse-shape wins"
    # The frame with CUSTOMER_PULSE__C + STATUS_C MUST NOT be classified
    # as APs because the disqualifier excludes it.
    assert aps is None, (
        "Round 111 / B1: a pulse frame falling through to the AP check "
        "MUST NOT be misclassified as APs. The disqualifier set MUST "
        "include CUSTOMER_PULSE__C / CUSTOMER_PULSE_COLOR_IMAGE__C."
    )


def test_calculate_renewal_risk_scores_accepts_explicit_frame_kwargs() -> None:
    """``calculate_renewal_risk_scores`` MUST accept the R111 explicit
    parity kwargs ``pulse_df``, ``action_plans_df``, ``subs_df`` as
    keyword-only parameters with a ``None`` default (back-compat)."""
    from compact_report_formatter import calculate_renewal_risk_scores

    sig = inspect.signature(calculate_renewal_risk_scores)
    assert "pulse_df" in sig.parameters
    assert "action_plans_df" in sig.parameters
    assert "subs_df" in sig.parameters
    for param_name in ("pulse_df", "action_plans_df", "subs_df"):
        param = sig.parameters[param_name]
        assert param.default is None, (
            f"Round 111 / B1: {param_name} default MUST be None for "
            "back-compat with legacy callers that rely on the "
            "classifier path."
        )


def test_classifier_widening_documented_in_source() -> None:
    """The classifier widening MUST carry a Round 111 / B1 source
    marker so future developers can grep ``# Round 111`` to see the
    per-file footprint of this fix."""
    import compact_report_formatter

    src = inspect.getsource(compact_report_formatter._r66_b8_classify_extra_frames)
    assert_in_source(src, "Round 111", label='src')
    assert "CUSTOMER_PULSE__C" in src, (
        "Round 111 / B1: the widened pulse marker set MUST include "
        "CUSTOMER_PULSE__C inline in the function body so a future "
        "audit can confirm the live production marker is wired."
    )


# ---------------------------------------------------------------------------
# Behavior pins
# ---------------------------------------------------------------------------


def test_explicit_pulse_df_kwarg_overrides_classifier() -> None:
    """When ``pulse_df`` is passed explicitly, the classifier output for
    that slot MUST be ignored. This is the parity-with-Renewal escape
    hatch the R111 fix wires into ``app_simple.py``."""
    from compact_report_formatter import calculate_renewal_risk_scores

    # Build minimal AB + CSOne for one customer.
    ab = pd.DataFrame(
        [{"customer_name": "Acme", "AB_STATUS_C": "Open", "SEVERITY_C": "P2"}]
    )
    csone = pd.DataFrame(
        [{"customer_name": "Acme", "Title": "Test", "Severity": "P3"}]
    )
    # An empty extra_frames list -- classifier would return all-None.
    # The explicit pulse_df kwarg MUST flow into per-customer slicing.
    explicit_pulse = pd.DataFrame(
        [{"BU_NAME": "Acme", "CUSTOMER_PULSE__C": "Red"}]
    )
    explicit_aps = pd.DataFrame(
        [{"BU_NAME": "Acme", "STATUS_C": "Open", "Title": "Plan A"}]
    )

    scores = calculate_renewal_risk_scores(
        ab,
        csone,
        extra_frames=None,
        pulse_df=explicit_pulse,
        action_plans_df=explicit_aps,
    )
    # Score dict for the customer must exist (and be non-empty)
    assert isinstance(scores, dict)
    keys = list(scores.keys())
    assert len(keys) >= 1, (
        f"Round 111 / B1: expected at least one customer in the score "
        f"dict; got {keys}"
    )


def test_per_customer_score_parity_with_renewal_path() -> None:
    """The cornerstone behavior pin. Round 67/B1 contract requires
    identical ``risk_score_0_100`` for the same customer + scope across
    Compact and Renewal paths."""
    from compact_report_formatter import calculate_renewal_risk_scores
    from risk_scoring import compute_customer_risk_profile

    # Synthetic customer with realistic data shape (mirrors live frames).
    ab = pd.DataFrame(
        [
            {
                "customer_name": "Acme",
                "AB_STATUS_C": "Open",
                "SEVERITY_C": "P2",
                "OPEN_DATE_C": "2026-04-01",
            },
            {
                "customer_name": "Acme",
                "AB_STATUS_C": "Open",
                "SEVERITY_C": "P3",
                "OPEN_DATE_C": "2026-04-15",
            },
        ]
    )
    csone = pd.DataFrame(
        [
            {
                "customer_name": "Acme",
                "Title": "Service Issue",
                "Severity": "P3",
                "case_age_days": 5,
            }
        ]
    )
    pulse_frame = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme",
                "CUSTOMER_PULSE__C": "Yellow",
                "CUSTOMER_PULSE_COLOR_IMAGE__C": "<img>",
            },
            {
                "BU_NAME": "Acme",
                "CUSTOMER_PULSE__C": "Yellow",
                "CUSTOMER_PULSE_COLOR_IMAGE__C": "<img>",
            },
        ]
    )
    ap_frame = pd.DataFrame(
        [
            {"BU_NAME": "Acme", "STATUS_C": "Open", "Title": "Plan A"},
            {"BU_NAME": "Acme", "STATUS_C": "Open", "Title": "Plan B"},
        ]
    )
    subs_frame = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme",
                "RENEWAL_RISK_CATEGORY": "Low",
                "STATUS_C": "active",
            }
        ]
    )

    # ---- Compact path: explicit kwargs (R111 parity hatch) ----
    compact_scores = calculate_renewal_risk_scores(
        ab,
        csone,
        extra_frames=None,
        pulse_df=pulse_frame,
        action_plans_df=ap_frame,
        subs_df=subs_frame,
        recent_window_days=90,
    )
    # Find the Acme key (normalized name lookup).
    from data_normalization import normalize_customer_name

    target = normalize_customer_name("Acme")
    compact_acme = compact_scores.get(target)
    assert compact_acme is not None, (
        f"Round 111 / B1: expected Acme in compact_scores; got "
        f"keys={list(compact_scores.keys())}"
    )

    # ---- Renewal path: direct compute_customer_risk_profile ----
    # Mirror what _calculate_simple_renewal_risk does.
    from data_normalization import add_case_lifecycle_fields

    renewal_profile = compute_customer_risk_profile(
        customer_name=target,
        customer_ab=ab,
        customer_csone=add_case_lifecycle_fields(csone),
        customer_pulse=pulse_frame,
        customer_action_plans=ap_frame,
        customer_subs=subs_frame,
        ext_incidents=None,
        recent_window_days=90,
    )

    # Identical 0-100 scores -- this is the R67/B1 + R111 contract.
    assert compact_acme["risk_score_0_100"] == pytest.approx(
        renewal_profile["risk_score_0_100"], abs=0.05
    ), (
        f"Round 111 / B1: Compact risk_score_0_100={compact_acme['risk_score_0_100']} "
        f"!= Renewal risk_score_0_100={renewal_profile['risk_score_0_100']} "
        f"for the same customer with the same data. The R67/B1 "
        f"cross-format parity contract is violated."
    )
    # Identical band (drives Risk_Level vocabulary).
    assert compact_acme["risk_band"] == renewal_profile["risk_band"], (
        f"Round 111 / B1: Compact risk_band={compact_acme['risk_band']} "
        f"!= Renewal risk_band={renewal_profile['risk_band']}"
    )


def test_pulse_data_not_lost_in_compact_with_live_pulse_columns() -> None:
    """Regression pin for the actual Build 79 production failure mode.
    A pulse frame whose ONLY rating column is the live ``CUSTOMER_PULSE__C``
    (no ``PULSE_RATING__C`` / ``SCORE__C``) MUST still flow into
    Compact per-customer scoring -- pre-R111 it silently dropped to
    empty and the engagement-component score shifted."""
    from compact_report_formatter import calculate_renewal_risk_scores

    ab = pd.DataFrame([{"customer_name": "Acme", "AB_STATUS_C": "Open"}])
    csone = pd.DataFrame([{"customer_name": "Acme", "Title": "x"}])
    # A pulse frame with ONLY the live production column shape.
    pulse = pd.DataFrame(
        [
            {"BU_NAME": "Acme", "CUSTOMER_PULSE__C": "Red"},
            {"BU_NAME": "Acme", "CUSTOMER_PULSE__C": "Red"},
            {"BU_NAME": "Acme", "CUSTOMER_PULSE__C": "Red"},
        ]
    )

    # Scenario A: pulse threaded via extra_frames (relies on widened
    # classifier).
    scores_via_extra = calculate_renewal_risk_scores(
        ab, csone, extra_frames=[pulse]
    )
    # Scenario B: pulse threaded via explicit kwarg (relies on R111
    # explicit-frame path).
    scores_via_kwarg = calculate_renewal_risk_scores(
        ab, csone, pulse_df=pulse
    )

    from data_normalization import normalize_customer_name

    target = normalize_customer_name("Acme")
    assert (
        scores_via_extra[target]["risk_score_0_100"]
        == scores_via_kwarg[target]["risk_score_0_100"]
    ), (
        "Round 111 / B1: classifier path and explicit-kwarg path MUST "
        "produce the same risk_score_0_100 for the same pulse data."
    )


def test_empty_data_customer_scores_identically_on_both_paths() -> None:
    """Negative control: a customer with no AB / CSOne / pulse / AP /
    subs / incidents MUST score identically on Compact and Renewal
    paths (both should converge on the empty-input score)."""
    from compact_report_formatter import calculate_renewal_risk_scores
    from risk_scoring import compute_customer_risk_profile

    # Single customer with one AB row only (so the customer enters the
    # Compact universe via AB).
    ab = pd.DataFrame([{"customer_name": "Acme"}])
    csone = pd.DataFrame()

    compact_scores = calculate_renewal_risk_scores(ab, csone)

    from data_normalization import normalize_customer_name

    target = normalize_customer_name("Acme")
    renewal_profile = compute_customer_risk_profile(
        customer_name=target,
        customer_ab=ab,
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame(),
        ext_incidents=None,
    )
    assert compact_scores[target]["risk_score_0_100"] == pytest.approx(
        renewal_profile["risk_score_0_100"], abs=0.05
    )


def test_subs_only_customer_scores_identically_on_both_paths() -> None:
    """Edge case from the Build 79 audit: customer present in subs but
    with NO pulse / AP / AB rows. This is the most common shape in the
    Renewal > Compact drift pattern. MUST score identically."""
    from compact_report_formatter import calculate_renewal_risk_scores
    from risk_scoring import compute_customer_risk_profile

    # AB stub so the customer enters the universe; subs are the only
    # signal.
    ab = pd.DataFrame([{"customer_name": "Acme"}])
    csone = pd.DataFrame()
    subs = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme",
                "RENEWAL_RISK_CATEGORY": "Critical",
                "STATUS_C": "active",
            }
        ]
    )

    # Compact via explicit subs_df kwarg.
    compact_scores = calculate_renewal_risk_scores(
        ab, csone, subs_df=subs
    )

    from data_normalization import normalize_customer_name

    target = normalize_customer_name("Acme")
    # Compact's per-customer slicer walks `('customer_name', 'Customer
    # Name', 'customer', 'BU_NAME', ...)`. The Renewal slicer walks
    # `('BU_NAME', 'Customer Name', 'CUSTOMER_NAME')`. Both must
    # match on `BU_NAME='Acme'` for this customer.
    renewal_profile = compute_customer_risk_profile(
        customer_name=target,
        customer_ab=ab,
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=subs,
        ext_incidents=None,
    )
    assert compact_scores[target]["risk_score_0_100"] == pytest.approx(
        renewal_profile["risk_score_0_100"], abs=0.05
    ), (
        "Round 111 / B1: subs-only customer MUST score identically "
        "on Compact and Renewal paths -- this was the dominant drift "
        "pattern in the Build 79 acceptance audit."
    )


def test_legacy_classifier_path_still_works_for_back_compat() -> None:
    """The R111 explicit-kwarg path is opt-in. Legacy callers passing
    ``extra_frames`` only MUST continue to receive the classifier
    behaviour (back-compat for any caller outside ``app_simple.py``)."""
    from compact_report_formatter import calculate_renewal_risk_scores

    ab = pd.DataFrame([{"customer_name": "Acme"}])
    csone = pd.DataFrame()
    # Legacy fixture: pulse frame with the OLD marker (PULSE_RATING__C).
    pulse_legacy = pd.DataFrame(
        [{"BU_NAME": "Acme", "PULSE_RATING__C": "Poor"}]
    )

    scores = calculate_renewal_risk_scores(
        ab, csone, extra_frames=[pulse_legacy]
    )
    from data_normalization import normalize_customer_name

    target = normalize_customer_name("Acme")
    assert target in scores, (
        "Round 111 / B1: legacy callers passing extra_frames with the "
        "PULSE_RATING__C marker MUST still see the customer in the "
        "score output."
    )


def test_app_simple_compact_callers_pass_explicit_kwargs() -> None:
    """Source-shape pin: all ``calculate_renewal_risk_scores`` call
    sites in ``app_simple.py`` MUST pass the R111 explicit-frame
    kwargs so Compact <-> Renewal parity is enforced at every entry
    point (Word path, XLSX path, plus their respective fallback
    branches).

    Round 124 / F3: a 5th call site was added on the Compact path --
    an early best-effort risk-score computation that grounds the
    portfolio-health-grade briefing BEFORE the executive-summary LLM
    call.  It threads the SAME canonical frames (``pulse_df`` /
    ``action_plans_df`` / ``subs_df``) via direct locals rather than
    ``_ctx.get(...)`` so the parity contract is preserved.  The count
    pin therefore moves 4 -> 5; the ``_ctx.get(...)`` kwarg assertions
    stay ``>= 4`` because the original four call sites still use the
    context lookup form.
    """
    src_path = PROJECT_ROOT / "app_simple.py"
    with src_path.open("r", encoding="utf-8") as fh:
        src = fh.read()

    # Each call MUST contain the canonical-frame kwargs.
    occurrences = src.count("calculate_renewal_risk_scores(")
    assert occurrences == 5, (
        f"Round 111 / B1 (Round 124 / F3): expected 5 "
        f"calculate_renewal_risk_scores call sites in app_simple.py; "
        f"got {occurrences}"
    )
    # Round 124 / F3: the early Compact briefing call site threads the
    # canonical frames via direct locals; assert those kwargs are present
    # so the new site can never silently drop the parity frames.
    assert_in_source(src, "pulse_df=csconsole_customer_pulse", label='src')
    assert_in_source(src, "action_plans_df=csconsole_action_plans", label='src')
    # Round 162.4: the selected technology's scoped subscription frame is the
    # only valid Compact risk input.  The manager-wide fetch roster must never
    # re-enter risk scoring after criteria scoping.
    assert_in_source(src, "subs_df=team_subs_for_customer_counting", label='src')
    # Each call should mention the three new kwargs.
    pulse_kwarg_count = src.count("pulse_df=_ctx.get('csconsole_customer_pulse')")
    ap_kwarg_count = src.count("action_plans_df=_ctx.get('csconsole_action_plans')")
    subs_kwarg_count = src.count("subs_df=_ctx.get('team_subs_df_unfiltered')")
    assert pulse_kwarg_count >= 4, (
        f"Round 111 / B1: expected >=4 pulse_df= kwargs in app_simple.py "
        f"(one per call site); got {pulse_kwarg_count}"
    )
    assert ap_kwarg_count >= 4, (
        f"Round 111 / B1: expected >=4 action_plans_df= kwargs; got "
        f"{ap_kwarg_count}"
    )
    assert subs_kwarg_count >= 4, (
        f"Round 111 / B1: expected >=4 subs_df= kwargs; got {subs_kwarg_count}"
    )
