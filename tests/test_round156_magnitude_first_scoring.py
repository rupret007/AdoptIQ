"""Round 156 -- opt-in magnitude-first risk scoring profile.

Three component scorers historically score by *proportion*, which inverts the
ranking a risk model exists to produce (who to call first):

* action plans: ``unresolved_ratio * 100`` -> 1 open plan of 1 scores 100
  (maximum) while 3 open of 20 scores 15 -- a 6.7x inversion that rewards
  customers who complete nothing and penalizes real remediation.
* adoption-barrier severity & openness: 1 critical barrier is indistinguishable
  from 4; 1 open of 1 outscores 10 open of 20.
* contract: 1 at-risk sub of 1 maxes the term over 3 at-risk of 20.

The ``magnitude_first`` profile makes magnitude primary and proportion
secondary, bounded to the same 0-100 envelope.  It is OPT-IN: the default
``legacy`` profile is preserved byte-for-byte so every shipped oracle/report is
unchanged until the change is validated on live outcomes and promoted to the
default in a dedicated round (see RISK_LOGIC_EVALUATION.md).

These tests assert (a) the default is legacy and unchanged, (b) each inversion
is corrected under ``magnitude_first``, and (c) profile resolution honors the
explicit arg > env > Config > ``legacy`` precedence.
"""

from __future__ import annotations

import pandas as pd
import pytest

import risk_scoring as rs
from risk_scoring import (
    RISK_SCORING_PROFILE_LEGACY,
    RISK_SCORING_PROFILE_MAGNITUDE_FIRST,
    _score_action_plans,
    _score_adoption_barriers,
    _score_contract,
    compute_customer_risk_profile,
    resolve_risk_scoring_profile,
)

MF = RISK_SCORING_PROFILE_MAGNITUDE_FIRST
ENV = "ADOPTIQ_RISK_SCORING_PROFILE"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with no ambient profile env var."""
    monkeypatch.delenv(ENV, raising=False)
    yield


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------
def _aps(open_n: int, total: int) -> pd.DataFrame:
    rows = [{"STATUS_C": "Open"}] * open_n + [{"STATUS_C": "Closed"}] * (total - open_n)
    return pd.DataFrame(rows)


def _subs(high_risk: int, total: int) -> pd.DataFrame:
    rows = [{"RENEWAL_RISK_CATEGORY": "High", "STATUS_C": "Active"}] * high_risk + [
        {"RENEWAL_RISK_CATEGORY": "Low", "STATUS_C": "Active"}
    ] * (total - high_risk)
    return pd.DataFrame(rows)


def _barriers(n: int, *, severity: str = "Critical", status: str = "Open") -> pd.DataFrame:
    return pd.DataFrame([{"SEVERITY_C": severity, "AB_STATUS_C": status}] * n)


# ---------------------------------------------------------------------------
# profile resolution
# ---------------------------------------------------------------------------
def test_default_profile_is_legacy():
    assert resolve_risk_scoring_profile() == RISK_SCORING_PROFILE_LEGACY
    assert resolve_risk_scoring_profile(None) == RISK_SCORING_PROFILE_LEGACY


def test_explicit_profile_wins():
    assert resolve_risk_scoring_profile("magnitude_first") == MF
    assert resolve_risk_scoring_profile("legacy") == RISK_SCORING_PROFILE_LEGACY
    # hyphen / case tolerant
    assert resolve_risk_scoring_profile("Magnitude-First") == MF


def test_env_activates_profile(monkeypatch):
    monkeypatch.setenv(ENV, "magnitude_first")
    assert resolve_risk_scoring_profile() == MF
    # explicit arg still overrides the env
    assert resolve_risk_scoring_profile("legacy") == RISK_SCORING_PROFILE_LEGACY


def test_unknown_value_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv(ENV, "not_a_profile")
    assert resolve_risk_scoring_profile() == RISK_SCORING_PROFILE_LEGACY
    assert resolve_risk_scoring_profile("bogus") == RISK_SCORING_PROFILE_LEGACY


# ---------------------------------------------------------------------------
# action plans: the clearest inversion
# ---------------------------------------------------------------------------
def test_action_plans_legacy_is_inverted():
    a = _score_action_plans(_aps(1, 1))["score"]  # 1 open of 1
    b = _score_action_plans(_aps(3, 20))["score"]  # 3 open of 20
    assert a == 100.0
    assert b == 15.0
    # legacy ranks the single-open account 6.7x above the account with 3x the
    # open work -- the inversion this round targets.
    assert a / b > 6.0


def test_action_plans_magnitude_first_corrects_inversion():
    a = _score_action_plans(_aps(1, 1), scoring_profile=MF)["score"]
    b = _score_action_plans(_aps(3, 20), scoring_profile=MF)["score"]
    c = _score_action_plans(_aps(5, 5), scoring_profile=MF)["score"]
    # one open item no longer maxes the dimension
    assert a < 100.0
    # the account doing real remediation is lifted out of the near-zero band
    assert b > 40.0
    # the 6.7x inversion collapses to a sane spread
    assert a / b < 1.5
    # a genuinely saturated account (all open) still maxes
    assert c == 100.0


def test_action_plans_default_matches_legacy():
    # default (no profile) must equal explicit legacy, byte-for-byte
    for o, t in [(1, 1), (3, 20), (5, 5), (0, 4)]:
        assert (
            _score_action_plans(_aps(o, t))["score"]
            == _score_action_plans(_aps(o, t), scoring_profile="legacy")["score"]
        )


# ---------------------------------------------------------------------------
# contract: proportion -> magnitude
# ---------------------------------------------------------------------------
def test_contract_legacy_is_inverted():
    one = _score_contract(_subs(1, 1))["score"]
    many = _score_contract(_subs(3, 20))["score"]
    assert one > many  # 1-of-1 maxes the term over 3 real at-risk subs


def test_contract_magnitude_first_corrects_inversion():
    one = _score_contract(_subs(1, 1), scoring_profile=MF)["score"]
    many = _score_contract(_subs(3, 20), scoring_profile=MF)["score"]
    # three at-risk subscriptions (real renewal exposure) now outrank one
    assert many > one


# ---------------------------------------------------------------------------
# adoption barriers: severity magnitude & openness magnitude
# ---------------------------------------------------------------------------
def test_barrier_severity_magnitude_first_distinguishes_count():
    one_legacy = _score_adoption_barriers(_barriers(1))["score"]
    four_legacy = _score_adoption_barriers(_barriers(4))["score"]
    one_mf = _score_adoption_barriers(_barriers(1), scoring_profile=MF)["score"]
    four_mf = _score_adoption_barriers(_barriers(4), scoring_profile=MF)["score"]
    # legacy can barely tell 1 critical from 4 (only the small volume term moves)
    assert (four_legacy - one_legacy) < 10.0
    # magnitude_first spreads them widely: 4 critical is materially worse
    assert (four_mf - one_mf) > 30.0
    # and a single critical is no longer inflated to a near-max AB score
    assert one_mf < one_legacy


def test_barrier_openness_magnitude_first_ranks_more_open_higher():
    # 1 open of 1 vs 10 open of 20 (severity held to Low to focus on openness)
    x = _score_adoption_barriers(
        pd.DataFrame([{"SEVERITY_C": "Low", "AB_STATUS_C": "Open"}])
    )
    y = _score_adoption_barriers(
        pd.DataFrame(
            [{"SEVERITY_C": "Low", "AB_STATUS_C": "Open"}] * 10
            + [{"SEVERITY_C": "Low", "AB_STATUS_C": "Closed"}] * 10
        )
    )
    x_mf = _score_adoption_barriers(
        pd.DataFrame([{"SEVERITY_C": "Low", "AB_STATUS_C": "Open"}]),
        scoring_profile=MF,
    )
    y_mf = _score_adoption_barriers(
        pd.DataFrame(
            [{"SEVERITY_C": "Low", "AB_STATUS_C": "Open"}] * 10
            + [{"SEVERITY_C": "Low", "AB_STATUS_C": "Closed"}] * 10
        ),
        scoring_profile=MF,
    )
    # legacy ranks the single open barrier at or above ten open barriers
    assert x["score"] >= y["score"]
    # magnitude_first ranks ten open barriers well above one
    assert y_mf["score"] > x_mf["score"]


def test_barrier_default_matches_legacy():
    for n in (1, 4, 10):
        assert (
            _score_adoption_barriers(_barriers(n))["score"]
            == _score_adoption_barriers(_barriers(n), scoring_profile="legacy")["score"]
        )


# ---------------------------------------------------------------------------
# composite: default unchanged; support/pulse/incidents untouched by profile
# ---------------------------------------------------------------------------
def _customer_frames():
    ab = _barriers(1, severity="Critical", status="Open")
    aps = _aps(3, 3)
    pulse = pd.DataFrame([{"SCORE__C": 1.0}])  # one poor pulse
    csone = pd.DataFrame(
        [{"Severity": "P1", "Case Status": "Open", "Date/Time Opened": "2026-08-01"}]
    )
    return ab, aps, pulse, csone


def test_composite_default_equals_legacy():
    ab, aps, pulse, csone = _customer_frames()
    default = compute_customer_risk_profile(
        "Acme", customer_ab=ab, customer_action_plans=aps,
        customer_pulse=pulse, customer_csone=csone,
    )
    legacy = compute_customer_risk_profile(
        "Acme", customer_ab=ab, customer_action_plans=aps,
        customer_pulse=pulse, customer_csone=csone,
        scoring_profile="legacy",
    )
    assert default["risk_score_0_100"] == legacy["risk_score_0_100"]
    assert default["risk_band"] == legacy["risk_band"]


def test_composite_magnitude_first_deflates_ratio_inflation():
    ab, aps, pulse, csone = _customer_frames()
    legacy = compute_customer_risk_profile(
        "Acme", customer_ab=ab, customer_action_plans=aps,
        customer_pulse=pulse, customer_csone=csone,
        scoring_profile="legacy",
    )
    mf = compute_customer_risk_profile(
        "Acme", customer_ab=ab, customer_action_plans=aps,
        customer_pulse=pulse, customer_csone=csone,
        scoring_profile=MF,
    )
    # a single critical barrier + a few open plans is no longer scored as a
    # near-HIGH account once ratio inflation is removed
    assert mf["risk_score_0_100"] < legacy["risk_score_0_100"]


def test_support_component_is_profile_independent():
    ab, aps, pulse, csone = _customer_frames()
    legacy = compute_customer_risk_profile(
        "Acme", customer_csone=csone, scoring_profile="legacy",
    )["components"]["support_cases"]["score"]
    mf = compute_customer_risk_profile(
        "Acme", customer_csone=csone, scoring_profile=MF,
    )["components"]["support_cases"]["score"]
    # the support-case scorer is the sound reference model -- unchanged by profile
    assert legacy == mf
