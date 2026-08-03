"""Round 142 deterministic risk-clock coverage."""

from __future__ import annotations

import pandas as pd
import pytest

import risk_scoring


def _barriers() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ID": "AB-001",
                "SEVERITY_C": "Low",
                "AB_STATUS_C": "Open",
                "OPEN_DATE_C": "2026-07-01T00:00:00Z",
                # Simulate an upstream normalizer that used a different clock.
                "open_age_days": 999,
            }
        ]
    )


def _cases() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SR Number": "700000001",
                "Case Status": "Open",
                "Severity": "4",
                "Date/Time Opened": "2026-07-15T00:00:00Z",
            },
            {
                "SR Number": "700000002",
                "Case Status": "Open",
                "Severity": "4",
                # A future record must not enter the recent window.
                "Date/Time Opened": "2026-08-10T00:00:00Z",
            },
        ]
    )


def test_explicit_as_of_controls_barrier_age_and_recent_case_window() -> None:
    early = risk_scoring.compute_customer_risk_profile(
        "Acme",
        customer_ab=_barriers(),
        customer_csone=_cases(),
        recent_window_days=30,
        as_of="2026-08-03T12:00:00Z",
    )
    late = risk_scoring.compute_customer_risk_profile(
        "Acme",
        customer_ab=_barriers(),
        customer_csone=_cases(),
        recent_window_days=30,
        as_of="2026-09-15T12:00:00Z",
    )

    # The explicit clock overrides the deliberately wrong upstream age value.
    assert early["components"]["adoption_barriers"]["details"]["aging_open_count"] == 0
    assert late["components"]["adoption_barriers"]["details"]["aging_open_count"] == 1
    # Only the in-window past case is recent at the early clock; neither is
    # recent 43 days later.
    assert early["components"]["support_cases"]["details"]["recent_count"] == 1
    assert late["components"]["support_cases"]["details"]["recent_count"] == 0


def test_explicit_as_of_does_not_consult_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ExplodingDateTime:
        @classmethod
        def now(cls, *_args, **_kwargs):  # pragma: no cover - failure sentinel
            raise AssertionError("wall clock consulted despite explicit as_of")

    monkeypatch.setattr(risk_scoring, "datetime", _ExplodingDateTime)
    profile = risk_scoring.compute_customer_risk_profile(
        "Acme",
        customer_ab=_barriers(),
        customer_csone=_cases(),
        recent_window_days=30,
        as_of="2026-08-03T07:00:00-05:00",
    )

    assert profile["risk_as_of_utc"] == "2026-08-03T12:00:00+00:00"
    assert profile["components"]["support_cases"]["details"]["recent_count"] == 1


def test_omitted_as_of_preserves_existing_upstream_age_field() -> None:
    profile = risk_scoring.compute_customer_risk_profile(
        "Acme",
        customer_ab=_barriers(),
    )

    # Backward-compatible callers keep trusting an already-derived age field.
    assert profile["components"]["adoption_barriers"]["details"]["aging_open_count"] == 1


@pytest.mark.parametrize("invalid", ["not-a-date", pd.NaT])
def test_invalid_explicit_risk_clock_fails_closed(invalid: object) -> None:
    with pytest.raises(ValueError, match="valid explicit as_of"):
        risk_scoring.compute_customer_risk_profile("Acme", as_of=invalid)
