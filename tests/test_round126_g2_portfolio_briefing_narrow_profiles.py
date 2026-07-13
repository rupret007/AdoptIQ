"""Round 126 / Build 95 (G2) -- portfolio briefing uses the NARROW risk profiles.

The Comprehensive title-page risk-band tile and ``portfolio_metrics
['high_risk_customers']`` are computed from ``_r64_narrow_risk_profiles``
(the AB u CSOne u Pulse u Subs universe) via
``compute_portfolio_risk_summary``.  Pre-Round-126 the portfolio LLM
briefing was handed the WIDE ``risk_profiles`` set, so its Risk Bands
section listed MORE Critical/High customers than the canonical tile.  The
LLM echoed that higher count, drifting the portfolio narrative off the
canonical high-risk band -> tripping the R25C
``validate_word_risk_band_claims`` gate -> burning the R68/A4 retry budget
-> escalating to the deterministic fallback (losing the AI commentary).

Round 126 / G2 threads ``_r64_narrow_risk_profiles`` into the portfolio
briefing call so the briefing agrees with the canonical tile at the
source.

Two layers:
  1. a SEMANTIC invariant proving the high-risk band count is sensitive to
     which profile set is passed (the bug mechanism + why the fix matters);
  2. a SOURCE-SHAPE pin proving the comprehensive portfolio briefing call
     threads the narrow set, not the wide ``risk_profiles``.
"""

import re
from pathlib import Path

from risk_scoring import compute_portfolio_risk_summary


# --- semantic invariant -------------------------------------------------

def _profile(score: float, band: str) -> dict:
    return {"risk_score_0_100": score, "risk_band": band}


def test_high_risk_count_is_sensitive_to_profile_set():
    """A wide set with an extra HIGH customer must report a larger
    high_risk_customers than the narrow subset that excludes it."""
    wide = {
        "Acme HIGH": _profile(82.0, "HIGH"),
        "Beta CRITICAL": _profile(95.0, "CRITICAL"),
        "Gamma MEDIUM": _profile(45.0, "MEDIUM"),
    }
    # Narrow excludes the extra HIGH-band customer (Acme) -- this is what
    # the AB u CSOne u Pulse u Subs scoping does on a real run.
    narrow = {
        "Beta CRITICAL": _profile(95.0, "CRITICAL"),
        "Gamma MEDIUM": _profile(45.0, "MEDIUM"),
    }

    wide_high = int(compute_portfolio_risk_summary(wide)["high_risk_customers"])
    narrow_high = int(compute_portfolio_risk_summary(narrow)["high_risk_customers"])

    assert wide_high == 2  # Acme + Beta
    assert narrow_high == 1  # Beta only
    assert narrow_high < wide_high, (
        "narrow profile set must report fewer high-risk customers when it "
        "excludes a wide-only HIGH-band customer; this is the drift the G2 "
        "fix removes"
    )


def test_empty_profiles_report_zero_high_risk():
    assert int(compute_portfolio_risk_summary({})["high_risk_customers"]) == 0
    assert int(compute_portfolio_risk_summary(None)["high_risk_customers"]) == 0


# --- source-shape pin ---------------------------------------------------

_APP_SIMPLE_SRC = Path(__file__).resolve().parent.parent / "app_simple.py"


def _portfolio_briefing_call_block() -> str:
    """Return the source text of the comprehensive portfolio
    ``_create_briefing_book(... ) `` call (the block carrying the G2
    marker), so the assertions below are scoped to the right call site
    and don't accidentally match a per-customer briefing call."""
    src = _APP_SIMPLE_SRC.read_text(encoding="utf-8")
    marker = "Round 126 / Build 95 (G2)"
    idx = src.find(marker)
    assert idx != -1, "Round 126 / Build 95 (G2) marker missing from app_simple.py"
    # Grab a window around the marker covering the risk_profiles= kwarg.
    # The G2 comment block is long, so reach well past it to the
    # risk_profiles=( assignment.
    return src[idx - 400 : idx + 2200]


def test_portfolio_briefing_threads_narrow_risk_profiles():
    block = _portfolio_briefing_call_block()
    # The risk_profiles= kwarg must reference the narrow set.
    assert "_r64_narrow_risk_profiles" in block, (
        "portfolio briefing must thread _r64_narrow_risk_profiles into "
        "risk_profiles="
    )
    # And it must be the value assigned to risk_profiles= (not just a
    # comment mention).
    assert re.search(
        r"risk_profiles\s*=\s*\(\s*\n\s*_r64_narrow_risk_profiles", block
    ), "risk_profiles= must be assigned the narrow profile set"


def test_portfolio_briefing_no_longer_passes_bare_wide_risk_profiles():
    """Guard against a regression to the pre-R126 shape where the kwarg was
    ``risk_profiles=risk_profiles if isinstance(risk_profiles, dict) else
    None)`` standalone."""
    block = _portfolio_briefing_call_block()
    bare_wide = "risk_profiles=risk_profiles if isinstance(risk_profiles, dict) else None)"
    assert bare_wide not in block, (
        "portfolio briefing must NOT pass the bare wide risk_profiles set "
        "(pre-R126 shape)"
    )
