"""Coverage-aware predictive and incident-completeness regressions."""

from __future__ import annotations

import json

import pandas as pd

import decision_report_delivery as delivery
import predictive_signals as predictive


AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")


def _failed_frame(detail: str) -> pd.DataFrame:
    frame = pd.DataFrame()
    frame.attrs["fetch_error"] = detail
    return frame


def _zero_frame(columns: list[str] | None = None) -> pd.DataFrame:
    return pd.DataFrame(columns=columns or [])


def _predictive_history(customer: str = "Acme") -> dict[str, pd.DataFrame]:
    return {
        "tac_cases": pd.DataFrame(
            [
                {
                    "ACCOUNT_ID_C": "ACC-1",
                    "BU_NAME": customer,
                    "Date/Time Opened": "2026-03-01",
                    "Date/Time Closed": "2026-03-10",
                    "Severity": "P3",
                },
                {
                    "ACCOUNT_ID_C": "ACC-1",
                    "BU_NAME": customer,
                    "Date/Time Opened": "2026-07-25",
                    "Severity": "P1",
                },
            ]
        ),
        "adoption_barriers": pd.DataFrame(
            [
                {
                    "ACCOUNT_ID_C": "ACC-1",
                    "BU_NAME": customer,
                    "OPEN_DATE_C": "2026-06-01",
                    "SEVERITY_C": "High",
                }
            ]
        ),
        "customer_pulse": pd.DataFrame(
            [
                {
                    "ACCOUNT_ID_C": "ACC-1",
                    "BU_NAME": customer,
                    "PULSE_DATE_C": "2026-04-01",
                    "SCORE__C": 7,
                },
                {
                    "ACCOUNT_ID_C": "ACC-1",
                    "BU_NAME": customer,
                    "PULSE_DATE_C": "2026-07-20",
                    "SCORE__C": 3,
                },
            ]
        ),
    }


def _live_calibration(tier: str) -> dict:
    return {
        "derived_from": "live_cisco_sources",
        "claim_level": "normal",
        "bands": [
            {
                "band": tier,
                "n": 40,
                "events": 12,
                "rate_smoothed": 0.3049,
                "rate_monotone": 0.3049,
                "wilson_low": 0.18,
                "wilson_high": 0.45,
            }
        ],
    }


def test_failed_tac_blocks_forecast_but_successful_zero_is_distinct() -> None:
    failed = {
        "tac_cases": _failed_frame("TAC query failed"),
        "adoption_barriers": pd.DataFrame(
            [{"OPEN_DATE_C": "2026-03-01", "SEVERITY_C": "High"}]
        ),
        "customer_pulse": _zero_frame(),
    }
    failed_coverage = predictive.predictive_source_coverage(failed)
    assert failed_coverage["coverage_state"] == "unavailable"
    assert failed_coverage["forecast_available"] is False
    assert failed_coverage["source_states"]["tac_cases"] == "failed"
    assert predictive.escalation_outlook(failed, AS_OF) is None

    successful_zero = dict(failed)
    successful_zero["tac_cases"] = _zero_frame(
        ["Date/Time Opened", "Severity"]
    )
    zero_coverage = predictive.predictive_source_coverage(successful_zero)
    assert zero_coverage["coverage_state"] == "available"
    assert zero_coverage["source_states"]["tac_cases"] == "zero"
    assert zero_coverage["missing_sources"] == []
    assert predictive.escalation_outlook(successful_zero, AS_OF) is not None


def test_partial_inputs_render_lower_bound_and_withhold_calibration() -> None:
    frames = _predictive_history()
    partial_ab = frames["adoption_barriers"].copy()
    partial_ab.attrs["partial"] = True
    partial_ab.attrs["source_mode_detail"] = "page two unavailable"
    frames["adoption_barriers"] = partial_ab

    prior = predictive.escalation_outlook(frames, AS_OF)
    assert prior is not None
    calibrated = predictive.escalation_outlook(
        frames,
        AS_OF,
        calibration=_live_calibration(prior["tier"]),
    )
    assert calibrated["coverage_state"] == "partial"
    assert calibrated["relative_signal_state"] == "lower_bound_relative_signal"
    assert calibrated["missing_sources"] == ["adoption_barriers"]
    assert calibrated["degraded_sources"] == ["adoption_barriers"]
    assert calibrated["calibration_state"] == "withheld_incomplete_coverage"
    assert "observed_rate" not in calibrated


def test_calibration_loader_requires_explicit_safe_live_provenance(
    tmp_path, monkeypatch
) -> None:
    live = tmp_path / "live.json"
    smoke = tmp_path / "smoke.json"
    invalid = tmp_path / "invalid.json"
    live.write_text(json.dumps(_live_calibration("ELEVATED")), encoding="utf-8")
    smoke.write_text(
        json.dumps(
            {
                **_live_calibration("ELEVATED"),
                "derived_from": "offline_fixture_smoke",
            }
        ),
        encoding="utf-8",
    )
    invalid.write_text("not json", encoding="utf-8")

    assert predictive.load_calibration_artifact(live)["derived_from"] == "live_cisco_sources"
    assert predictive.load_calibration_artifact(smoke) is None
    assert predictive.load_calibration_artifact(invalid) is None
    assert predictive.load_calibration_artifact("relative.json") is None

    monkeypatch.setenv(predictive.PREDICTIVE_CALIBRATION_PATH_ENV, str(live))
    assert predictive.load_calibration_artifact()["derived_from"] == "live_cisco_sources"


def test_report_predictive_bundle_uses_all_source_id_first_aliases() -> None:
    history = _predictive_history("NYU MEDICAL CENTER")
    frames = {
        "subscriptions": pd.DataFrame(
            [
                {
                    "ACCOUNT_ID_C": "ACC-1",
                    "SUBSCRIPTION_ID": "SUB-1",
                    "BU_NAME": "NYU LANGONE HEALTH SYSTEMS",
                },
                {
                    "ACCOUNT_ID_C": "ACC-PULSE",
                    "SUBSCRIPTION_ID": "SUB-PULSE",
                    "BU_NAME": "Pulse Only Customer",
                },
            ]
        ),
        "action_plans": _zero_frame(),
        "adoption_barriers": history["adoption_barriers"],
        "customer_pulse": pd.concat(
            [
                history["customer_pulse"],
                pd.DataFrame(
                    [
                        {
                            "ACCOUNT_ID_C": "ACC-PULSE",
                            "BU_NAME": "Pulse Only Customer",
                            "PULSE_DATE_C": "2026-03-01",
                            "SCORE__C": 6,
                        },
                        {
                            "ACCOUNT_ID_C": "ACC-PULSE",
                            "BU_NAME": "Pulse Only Customer",
                            "PULSE_DATE_C": "2026-07-01",
                            "SCORE__C": 5,
                        },
                    ]
                ),
            ],
            ignore_index=True,
        ),
        "tac_cases": history["tac_cases"],
        "success_priorities": _zero_frame(),
    }
    bundle = delivery.build_canonical_predictive_outlooks(frames, as_of=AS_OF)

    labels = [identity["label"] for identity in bundle["identities"]]
    assert labels == ["NYU LANGONE HEALTH SYSTEMS", "Pulse Only Customer"]
    assert "NYU MEDICAL CENTER" not in labels
    nyu_slice = bundle["customer_slices"]["NYU LANGONE HEALTH SYSTEMS"]
    assert len(nyu_slice["tac_cases"]) == 2
    assert "NYU LANGONE HEALTH SYSTEMS" in bundle["outlooks"]
    assert bundle["coverage_by_customer"]["Pulse Only Customer"][
        "source_states"
    ]["tac_cases"] == "zero"


def _incident_team() -> dict:
    return {
        "Jordan": {
            "subscriptions": pd.DataFrame(
                [
                    {
                        "ACCOUNT_ID_C": "ACC-1",
                        "SUBSCRIPTION_ID": "S1",
                        "BU_NAME": "Acme",
                    }
                ]
            ),
            "action_plans": _zero_frame(),
            "adoption_barriers": _zero_frame(),
            "customer_pulse": _zero_frame(),
            "tac_cases": _zero_frame(),
            "success_priorities": _zero_frame(),
        }
    }


def _facts_with_incidents(incidents) -> dict:
    return delivery.build_report_facts(
        _incident_team(),
        report_type="Leader",
        scope_type="team",
        scope_value="Jordan's Team",
        manager_name="Jordan",
        days=90,
        as_of=AS_OF,
        external_incidents=incidents,
        external_bugs=[],
    )


def test_incident_failure_is_missing_and_makes_risk_summary_partial() -> None:
    failed = _failed_frame("status.webex.com timeout")
    facts = _facts_with_incidents(failed)
    component = facts["risk_profiles"]["Acme"]["components"]["incidents"]
    assert component["score"] is None
    assert component["details"]["data_state"] == "missing"
    assert facts["risk_summary"]["source_state"] == "partial"
    assert "external_incidents" in facts["risk_summary"]["source_state_detail"]


def test_untagged_incident_is_context_only_but_tagged_incident_drives_action() -> None:
    untagged = [
        {
            "id": "INC-PORTFOLIO",
            "status": "active",
            "impact_level": "High",
            "title": "Portfolio status incident",
        }
    ]
    context_facts = _facts_with_incidents(untagged)
    context_profile = context_facts["risk_profiles"]["Acme"]
    assert context_profile["components"]["incidents"]["score"] == 0.0
    assert context_profile["components"]["incidents"]["details"]["count"] == 0
    assert all(
        "INC-PORTFOLIO" not in factor for factor in context_profile["risk_factors"]
    )

    tagged = [
        {
            "id": "INC-ACME",
            "ACCOUNT_ID_C": "ACC-1",
            "status": "active",
            "impact_level": "High",
            "title": "Acme service incident",
        }
    ]
    tagged_facts = _facts_with_incidents(tagged)
    tagged_profile = tagged_facts["risk_profiles"]["Acme"]
    assert tagged_profile["components"]["incidents"]["score"] > 0
    assert any("INC-ACME" in factor for factor in tagged_profile["risk_factors"])
    assert "INC-ACME" in tagged_profile["next_best_action"]
    assert "publish the mitigation" in tagged_profile["next_best_action"]
