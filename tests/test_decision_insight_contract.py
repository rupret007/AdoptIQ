"""Fail-closed contract regressions for canonical executive insight prose."""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple

import pytest

import decision_report_delivery as delivery
from tests.test_round157_reporting_ask_ai import _facts_with_tac
from tests.test_round158_momentum_insights import _build_facts
from tests.test_round160_predictive_engine import (
    AS_OF,
    _facts_with_predictive_customer,
)


def _support_facts() -> Tuple[Any, Dict[str, Any]]:
    return _facts_with_tac(
        [
            {
                "SR Number": "1",
                "BU_NAME": "Acme",
                "Severity": "P1",
                "Case Status": "Open",
                "Date/Time Opened": "2026-07-30",
                "Tech.": "Webex Calling",
            },
            {
                "SR Number": "2",
                "BU_NAME": "Acme",
                "Severity": "P3",
                "Case Status": "Open",
                "Date/Time Opened": "2026-07-28",
                "Tech.": "Webex Calling",
            },
        ]
    )


def _momentum_facts() -> Tuple[Any, Dict[str, Any]]:
    return _build_facts(
        tac_rows=[
            {
                "SR Number": "1",
                "BU_NAME": "Acme",
                "Severity": "P1",
                "Case Status": "Open",
                "Date/Time Opened": "2026-07-30",
            },
            {
                "SR Number": "2",
                "BU_NAME": "Acme",
                "Severity": "P3",
                "Case Status": "Open",
                "Date/Time Opened": "2026-07-28",
            },
            {
                "SR Number": "3",
                "BU_NAME": "Acme",
                "Severity": "P3",
                "Case Status": "Open",
                "Date/Time Opened": "2026-05-10",
            },
        ],
        pulse_rows=[
            {
                "ID": "P1",
                "BU_NAME": "Acme",
                "SCORE__C": 4.0,
                "PULSE_DATE_C": "2026-05-10",
            },
            {
                "ID": "P2",
                "BU_NAME": "Acme",
                "SCORE__C": 8.0,
                "PULSE_DATE_C": "2026-07-30",
            },
        ],
    )


def _operating_health_facts() -> Tuple[Any, Dict[str, Any]]:
    return _facts_with_tac(
        [
            {
                "SR Number": "OH-1",
                "BU_NAME": "Acme",
                "Severity": "P3",
                "Case Status": "Closed",
                "Date/Time Opened": "2026-07-01",
                "Date/Time Closed": "2026-07-02",
                "# of Case Owner Changes": 0,
            },
            {
                "SR Number": "OH-2",
                "BU_NAME": "Acme",
                "Severity": "P3",
                "Case Status": "Closed",
                "Date/Time Opened": "2026-06-01",
                "Date/Time Closed": "2026-06-11",
                "# of Case Owner Changes": 2,
            },
            {
                "SR Number": "OH-3",
                "BU_NAME": "Acme",
                "Severity": "P3",
                "Case Status": "Closed",
                "Date/Time Opened": "2026-05-10",
                "Date/Time Closed": "2026-05-30",
                "# of Case Owner Changes": 3,
            },
        ]
    )


def _predictive_facts() -> Tuple[Any, Dict[str, Any]]:
    return _facts_with_predictive_customer()


@pytest.mark.parametrize(
    ("builder", "insight_name", "tampered_text"),
    [
        (
            _support_facts,
            "support_themes",
            "Support themes (TAC): Webex Calling — 999 case(s) (777 escalated).",
        ),
        (
            _momentum_facts,
            "window_momentum",
            (
                "Momentum within this window: TAC cases opened falling — 999 in the "
                "last 45 days vs 0 in the prior half; Pulse worsening — avg score 99 → 0."
            ),
        ),
        (
            _operating_health_facts,
            "support_operating_health",
            (
                "Support operating health (TAC): median time to close 999 days; "
                "90th percentile 999 days across 999 closed case(s)."
            ),
        ),
        (
            _predictive_facts,
            "predictive_outlook",
            (
                "Predictive outlook (next 30 days, deterministic scorecard): Acme — "
                "Critical Watch (999 pts): fabricated (+999) [calibrated: 99 of 100 escalated]."
            ),
        ),
    ],
    ids=("support-themes", "momentum", "support-operating-health", "predictive"),
)
def test_frozen_insight_has_resolvable_evidence_and_rejects_word_tampering(
    builder: Callable[[], Tuple[Any, Dict[str, Any]]],
    insight_name: str,
    tampered_text: str,
) -> None:
    module, facts = builder()
    insight = facts["decision_insights"][insight_name]
    metric_key = insight["metric_key"]
    expected_text = insight["paragraph_text"]
    expected_reference = (
        f"[Source: Source Data File → Metric_Lineage / {metric_key}]"
    )

    lineage = facts["metric_lineage"]
    lineage_rows = lineage.loc[lineage["Metric_Key"] == metric_key]
    assert len(lineage_rows) == 1
    assert lineage_rows.iloc[0]["Metric_Value"] == expected_text

    document = module.build_concise_word_document(facts)
    paragraph_texts = [paragraph.text.strip() for paragraph in document.paragraphs]
    paragraph_position = paragraph_texts.index(expected_text)
    assert paragraph_texts[paragraph_position + 1] == expected_reference

    sheets = module.build_source_data_sheets(facts)
    evidence_rows = sheets["Evidence_Links"].loc[
        sheets["Evidence_Links"]["Evidence_Key"] == metric_key
    ]
    assert not evidence_rows.empty
    assert set(evidence_rows["Source_Sheet"]) == set(insight["source_sheets"])
    evidence_contract = module.validate_evidence_links(sheets)
    assert evidence_contract["ok"], evidence_contract["errors"]

    original_contract = module.validate_cross_artifact_contract(
        facts,
        sheets,
        document,
    )
    assert original_contract["ok"], original_contract["errors"]
    assert (
        original_contract["word_semantics"]["validated_decision_insight_count"]
        == len(module._rendered_decision_insight_names(facts))  # noqa: SLF001
    )

    document.paragraphs[paragraph_position].text = tampered_text
    tampered_contract = module.validate_cross_artifact_contract(
        facts,
        sheets,
        document,
    )
    assert not tampered_contract["ok"]
    assert any(
        f"Word {insight_name} paragraph differs from canonical frozen facts" in error
        for error in tampered_contract["errors"]
    )


def _predictive_team_data() -> Dict[str, Dict[str, Any]]:
    _, seed = _facts_with_predictive_customer()
    return {
        "Alex Rivera": {
            frame_key: seed["frames"][frame_key].copy()
            for frame_key in (
                "subscriptions",
                "action_plans",
                "adoption_barriers",
                "customer_pulse",
                "tac_cases",
                "success_priorities",
            )
        }
    }


def test_predictive_insight_uses_evaluation_clock_not_source_freshness_clock() -> None:
    team_data = _predictive_team_data()
    paragraphs = []
    for source_clock, source_state in (
        (None, "unavailable"),
        ("2026-04-01T12:00:00Z", "stale"),
    ):
        facts = delivery.build_report_facts(
            team_data,
            report_type="Comprehensive",
            scope_type="team",
            scope_value="Alex Rivera's Team",
            manager_name="Alex Rivera",
            days=90,
            as_of=AS_OF,
            data_as_of_utc=source_clock,
            data_as_of_state=source_state,
        )
        insight = facts["decision_insights"]["predictive_outlook"]
        assert insight["evaluation_as_of_utc"] == facts["evaluation_as_of_utc"]
        assert "Critical Watch (76 pts)" in insight["paragraph_text"]
        document = delivery.build_concise_word_document(facts)
        sheets = delivery.build_source_data_sheets(facts)
        contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
        assert contract["ok"], contract["errors"]
        paragraphs.append(insight["paragraph_text"])

    assert paragraphs[0] == paragraphs[1]


def test_support_theme_derivation_failure_blocks_fact_bundle(monkeypatch) -> None:
    def fail(_frame):
        raise RuntimeError("support-theme derivation failed")

    monkeypatch.setattr(delivery.cm, "tac_theme_summary", fail)
    with pytest.raises(RuntimeError, match="support-theme derivation failed"):
        _support_facts()


def test_momentum_derivation_failure_blocks_fact_bundle(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("momentum derivation failed")

    monkeypatch.setattr(delivery.cm, "window_momentum", fail)
    with pytest.raises(RuntimeError, match="momentum derivation failed"):
        _momentum_facts()


def test_predictive_derivation_failure_blocks_fact_bundle(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("predictive derivation failed")

    monkeypatch.setattr(delivery.ps, "escalation_outlook", fail)
    with pytest.raises(RuntimeError, match="predictive derivation failed"):
        _predictive_facts()
