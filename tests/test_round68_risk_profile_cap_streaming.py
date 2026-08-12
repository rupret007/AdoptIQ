"""Round 68 / Build 42 (C4): pin the risk-profile cap raise + streaming
mode in ``ask_ai_grounded.py``.

Pre-R68 the per-customer scoring loop in the canonical-headline
build was hard-capped at 200 customers and silently published a
partial-coverage value as if it were authoritative.  R68 raises the
cap to 500 and adds an explicit STREAMING branch for portfolios
above the cap that suppresses the risk-derived metrics entirely
(rather than understating them) and surfaces a 4-state coverage
disclosure (FULL / PARTIAL / STREAMING / NONE) in the
CANONICAL_HEADLINE block so the LLM cannot quote a partial count.

These tests are source-shape pins.  The behavioural tests for
``build_portfolio_metrics`` already cover the ``risk_profiles=None``
suppression branch -- here we just guarantee the call site honors
the new cap and streaming logic.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


_ASK_AI_GROUNDED = Path(__file__).resolve().parent.parent / "ask_ai_grounded.py"


def _src() -> str:
    assert _ASK_AI_GROUNDED.exists(), f"ask_ai_grounded.py missing at {_ASK_AI_GROUNDED}"
    return _ASK_AI_GROUNDED.read_text(encoding="utf-8")


# --- Cap raise ---------------------------------------------------------------


def test_r68_risk_profile_cap_default_is_500() -> None:
    """The default scoring cap must be 500 (raised from the pre-R68
    value of 200).  Lowering it back would understate every risk-
    derived metric on portfolios with 201-500 customers."""
    src = _src()
    assert 'ADOPTIQ_ASK_AI_RISK_PROFILE_CAP", "500"' in src, (
        "default risk-profile cap not set to 500 -- regressed to pre-R68"
        " value (would understate risk metrics on mid-size portfolios)"
    )


def test_r68_risk_profile_cap_env_overridable() -> None:
    """Operators must be able to dial the cap down without code
    changes (e.g. when Snowflake is slow)."""
    src = _src()
    assert "os.environ.get(" in src, "cap not env-overridable"
    assert "ADOPTIQ_ASK_AI_RISK_PROFILE_CAP" in src, "env var name not exposed"


def test_r68_pre_r68_hardcoded_200_cap_removed() -> None:
    """The pre-R68 hardcoded ``[:200]`` slice MUST be replaced by the
    env-driven cap.  A reintroduced ``[:200]`` would silently re-
    cap requests at 200 even when the env var is set higher."""
    src = _src()
    assert "[:200]" not in src
    assert "_build_ask_ai_canonical_risk_profiles" in src


# --- Streaming branch --------------------------------------------------------


def test_r68_streaming_mode_decision_present() -> None:
    """The ``_streaming_mode = _universe_size_pre > _RISK_PROFILE_CAP``
    decision must be the gate -- without it the loop would still run
    on huge portfolios and wedge the request thread."""
    from ask_ai_grounded import _build_ask_ai_canonical_risk_profiles

    profiles, identities, streaming = _build_ask_ai_canonical_risk_profiles(
        {
            "subscriptions": pd.DataFrame([
                {"ACCOUNT_ID_C": "1", "BU_NAME": "Acme"},
                {"ACCOUNT_ID_C": "2", "BU_NAME": "Beta"},
            ]),
        },
        days=90,
        as_of=pd.Timestamp("2026-08-04T12:00:00Z"),
        cap=1,
    )

    assert len(identities) == 2
    assert streaming is True
    assert profiles == {}


def test_r68_streaming_mode_skips_per_customer_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streaming mode must SKIP the per-customer loop -- if it ran
    the loop anyway, the cap-raise would be useless."""
    import decision_report_delivery as delivery
    from ask_ai_grounded import _build_ask_ai_canonical_risk_profiles

    def fail_if_scored(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("streaming mode must not call the report scorer")

    monkeypatch.setattr(delivery, "_build_risk_profiles", fail_if_scored)
    profiles, identities, streaming = _build_ask_ai_canonical_risk_profiles(
        {
            "subscriptions": pd.DataFrame([
                {"ACCOUNT_ID_C": "1", "BU_NAME": "Acme"},
                {"ACCOUNT_ID_C": "2", "BU_NAME": "Beta"},
            ]),
        },
        days=90,
        as_of=pd.Timestamp("2026-08-04T12:00:00Z"),
        cap=1,
    )

    assert len(identities) == 2
    assert streaming is True
    assert profiles == {}


def test_r68_streaming_mode_logs_decision() -> None:
    """Streaming mode must log so the operator can see why the band
    counts are missing for a given run.  Without the log the
    streaming decision is silent."""
    src = _src()
    assert 'streaming mode' in src.lower(), (
        "streaming-mode decision not logged -- operator can't see why"
        " band counts are missing for huge portfolios"
    )


# --- Disclosure block --------------------------------------------------------


def test_r68_streaming_coverage_state_present_in_disclosure() -> None:
    """The CANONICAL_HEADLINE coverage row must include a STREAMING
    state so the LLM cannot quote a risk-derived count when the
    per-customer loop was deliberately skipped."""
    src = _src()
    assert "STREAMING" in src, (
        "STREAMING coverage state missing from canonical headline disclosure"
    )
    assert "risk_profiles_coverage: STREAMING" in src, (
        "STREAMING coverage row not surfaced under risk_profiles_coverage"
    )


def test_r68_streaming_disclosure_tells_llm_to_skip_risk_metrics() -> None:
    """The streaming disclosure must explicitly tell the LLM the
    risk-derived metrics are unavailable -- otherwise the model
    might fall back to ``high_risk_customers: 0`` in the
    CANONICAL_HEADLINE block (which build_portfolio_metrics omits
    in streaming mode) and report 0 high-risk customers as fact."""
    src = _src()
    streaming_idx = src.find("STREAMING (")
    assert streaming_idx != -1
    snippet = src[streaming_idx : streaming_idx + 1200]
    assert "treat all risk-derived metrics as unavailable" in snippet, (
        "STREAMING disclosure doesn't tell the LLM to suppress risk metrics"
    )
    assert "deliberately skipped" in snippet, (
        "STREAMING disclosure doesn't surface that the skip was deliberate"
    )


def test_r68_four_state_coverage_disclosure_present() -> None:
    """All four coverage states (FULL / PARTIAL / STREAMING / NONE)
    must remain in the disclosure block.  Dropping one would let
    the LLM see a missing risk_profiles_coverage row and quote
    risk metrics with no warning."""
    src = _src()
    for state in ("FULL", "PARTIAL", "STREAMING", "NONE"):
        assert f"risk_profiles_coverage: {state}" in src, (
            f"coverage state {state!r} dropped from disclosure block"
        )


# --- build_portfolio_metrics call site --------------------------------------


def test_r68_streaming_passes_none_to_build_portfolio_metrics() -> None:
    """When streaming, the per-customer scoring dict is empty so
    ``build_portfolio_metrics`` receives ``risk_profiles=None``
    (via ``or None``) and suppresses the risk-derived keys.
    This is the actual mechanism that prevents the LLM from
    seeing a phantom 0 for high_risk_customers in streaming mode."""
    src = _src()
    # ``_risk_profiles_canon or None`` -- when the dict is empty,
    # this evaluates to None, which build_portfolio_metrics treats
    # as "skip risk-derived keys entirely".
    assert "risk_profiles=_risk_profiles_canon or None" in src, (
        "build_portfolio_metrics call doesn't degrade to None when"
        " _risk_profiles_canon is empty -- streaming mode would publish"
        " phantom 0s for risk metrics"
    )


# --- Smoke importability -----------------------------------------------------


def test_r68_module_imports_cleanly() -> None:
    """Catch any syntax error from the streaming-mode rewrite."""
    import ask_ai_grounded  # noqa: F401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
