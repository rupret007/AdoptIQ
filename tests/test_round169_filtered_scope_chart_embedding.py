"""Round 169 / P1: filtered technology scopes must not silently drop all Word charts."""

from __future__ import annotations

import pandas as pd
import pytest

import decision_report_delivery as delivery
import report_iteration_loop
from tests.test_round142_decision_report_delivery import (
    AS_OF,
    _fake_chart_renderer,
    _team_fixture,
)


def test_build_chart_data_withholds_only_incomplete_series_rows() -> None:
    activity_mix = {
        "series": pd.DataFrame(
            [
                {"Category": "Action Plans", "Value": 5, "Source_State": "available"},
                {"Category": "Customer Pulse", "Value": 2, "Source_State": "partial"},
            ]
        )
    }
    lifecycle = {
        "due_soon_days": 14,
        "source_state": "available",
        "source_state_detail": "",
        "field_selection": {"id": "ID"},
        "deduplication_rule": "stable ID",
        "bucket_counts": {"Open": 1, "Completed": 0},
        "age_band_counts": {"0-30 days": 1},
    }
    risk_summary = {
        "source_state": "available",
        "source_state_detail": "",
        "risk_band_counts": {
            "HIGH": 1,
            "CRITICAL": 0,
            "MEDIUM": 0,
            "LOW": 0,
            "HEALTHY": 0,
        },
    }
    trend = {
        "frequency": "W",
        "series": pd.DataFrame(
            [
                {
                    "Source": "Action Plans",
                    "Period_Start": "2026-07-01",
                    "Value": 3,
                    "Source_State": "available",
                }
            ]
        ),
    }

    chart_data = delivery._build_chart_data(activity_mix, lifecycle, risk_summary, trend)
    mix_rows = chart_data.loc[chart_data["Chart_ID"] == "activity_mix"]
    action_row = mix_rows.loc[mix_rows["Category"] == "Action Plans"].iloc[0]
    pulse_row = mix_rows.loc[mix_rows["Category"] == "Customer Pulse"].iloc[0]

    assert action_row["Value"] == 5
    assert pd.isna(pulse_row["Value"])
    assert "withheld" in str(pulse_row["Caveat"]).casefold()


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("matplotlib") is None,
    reason="chart rendering requires matplotlib",
)
def test_filtered_scope_embeds_charts_when_only_one_activity_series_is_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _team_fixture()
    failed_pulse = pd.DataFrame()
    failed_pulse.attrs["fetch_error"] = "sanitized partial pulse fixture"
    fixture["Alex Rivera"]["customer_pulse"] = failed_pulse

    facts = delivery.build_report_facts(
        fixture,
        report_type="Comprehensive",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        technology="All Contact Center",
        days=90,
        as_of=AS_OF,
        partial_data_warnings=[{"dataset": "Customer_Pulse", "kind": "fetch_failed"}],
    )

    mix_rows = facts["chart_data"].loc[
        facts["chart_data"]["Chart_ID"] == "activity_mix"
    ]
    assert mix_rows.loc[
        mix_rows["Category"] == "Customer Pulse", "Value"
    ].isna().all()
    assert mix_rows.loc[
        mix_rows["Category"] == "Action Plans", "Value"
    ].notna().any()

    monkeypatch.setattr(delivery, "_render_chart_image", _fake_chart_renderer)
    doc = delivery.build_concise_word_document(facts)
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)

    assert len(doc.inline_shapes) >= 1
    assert "Activity Mix by Source" in text
    assert any(
        heading in text
        for heading in (
            "Customer Risk Distribution",
            "Action Plan Status and Aging",
            "Activity Trend",
        )
    )


def test_mixed_complete_and_withheld_series_pass_canonical_workbook_contract(
    tmp_path,
) -> None:
    fixture = _team_fixture()
    failed_pulse = pd.DataFrame()
    failed_pulse.attrs["fetch_error"] = "sanitized partial pulse fixture"
    fixture["Alex Rivera"]["customer_pulse"] = failed_pulse

    facts = delivery.build_report_facts(
        fixture,
        report_type="Comprehensive",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        technology="All Contact Center",
        days=90,
        as_of=AS_OF,
        partial_data_warnings=[{"dataset": "Customer_Pulse", "kind": "fetch_failed"}],
    )
    workbook = tmp_path / "mixed-series-source-data.xlsx"
    delivery.write_source_data_workbook(
        workbook,
        delivery.build_source_data_sheets(facts),
    )

    contract = report_iteration_loop._canonical_chart_renderability(workbook)

    assert contract["contract_valid"] is True
    assert contract["renderable_chart_count"] > 0
    assert contract["withheld_chart_count"] > 0


def test_withheld_chart_gets_per_chart_coverage_paragraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _team_fixture()
    failed_tac = pd.DataFrame()
    failed_tac.attrs["fetch_error"] = "sanitized tac outage"
    fixture["Alex Rivera"]["tac_cases"] = failed_tac

    facts = delivery.build_report_facts(
        fixture,
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of=AS_OF,
    )
    monkeypatch.setattr(delivery, "_render_chart_image", _fake_chart_renderer)
    doc = delivery.build_concise_word_document(facts)
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)

    assert "Activity Mix by Source" in text
    assert "Chart withheld" in text or "Chart unavailable" in text
