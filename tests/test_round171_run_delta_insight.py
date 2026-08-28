"""Round 171 — the frozen run-over-run movement insight ("Since the last comparable report:").

The report must walk in already knowing what changed: at generation time the
caller resolves the most recent completed same-scope report, and
``build_report_facts`` freezes the Round-146 ``compare_snapshots`` result as a
first-class decision insight with real current-side row receipts.  These tests
pin the honesty contract: absent without a comparable canonical prior, exact
frozen text everywhere, source-driven differences excluded and disclosed, and
written-artifact parity with the frozen claim.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import pytest

import decision_report_delivery as delivery
import manager_decision_workspace as mdw

AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ADOPTIQ_RISK_SCORING_PROFILE", raising=False)


def _team_data(
    *,
    tac_rows=None,
    ap_status="Open",
    pulse_rows=None,
    barrier_rows=None,
):
    return {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame(
                [{"SUBSCRIPTION_ID": "S1", "BU_NAME": "Acme", "STATUS_C": "Active"}]
            ),
            "action_plans": pd.DataFrame(
                [
                    {
                        "ID": "AP1",
                        "BU_NAME": "Acme",
                        "SUBJECT_C": "Enable SSO",
                        "STATUS_C": ap_status,
                        "CREATED_DATE_C": "2026-07-01",
                    }
                ]
            ),
            "adoption_barriers": pd.DataFrame(
                barrier_rows
                if barrier_rows is not None
                else [
                    {
                        "ID": "AB1",
                        "BU_NAME": "Acme",
                        "SEVERITY_C": "High",
                        "AB_STATUS_C": "Open",
                        "OPEN_DATE_C": "2026-05-15",
                    }
                ]
            ),
            "customer_pulse": pd.DataFrame(
                pulse_rows
                if pulse_rows is not None
                else [
                    {
                        "ID": "CP1",
                        "BU_NAME": "Acme",
                        "SCORE__C": 6.0,
                        "PULSE_DATE_C": "2026-07-20",
                    }
                ]
            ),
            "tac_cases": pd.DataFrame(
                tac_rows
                if tac_rows is not None
                else [
                    {
                        "SR Number": "1",
                        "BU_NAME": "Acme",
                        "Severity": "P3",
                        "Case Status": "Open",
                        "Date/Time Opened": "2026-07-10",
                    }
                ]
            ),
            "success_priorities": pd.DataFrame(),
        }
    }


_ESCALATED_TAC = [
    {
        "SR Number": "1",
        "BU_NAME": "Acme",
        "Severity": "P3",
        "Case Status": "Open",
        "Date/Time Opened": "2026-07-10",
    },
    {
        "SR Number": "2",
        "BU_NAME": "Acme",
        "Severity": "P1",
        "Case Status": "Open",
        "Date/Time Opened": "2026-07-30",
    },
    {
        "SR Number": "3",
        "BU_NAME": "Acme",
        "Severity": "P2",
        "Case Status": "Open",
        "Date/Time Opened": "2026-07-29",
    },
    {
        "SR Number": "4",
        "BU_NAME": "Acme",
        "Severity": "P1",
        "Case Status": "Open",
        "Date/Time Opened": "2026-08-01",
    },
]


def _build(td, prior=None, meta=None, **overrides):
    kwargs = dict(
        report_type="Leader",
        scope_type="team",
        scope_value="Alex Rivera's Team",
        manager_name="Alex Rivera",
        days=90,
        as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(),
        data_as_of_state="available",
    )
    kwargs.update(overrides)
    return delivery.build_report_facts(
        td, prior_snapshot=prior, prior_snapshot_meta=meta, **kwargs
    )


def _written_snapshot(facts, tmp: Path, name: str):
    sheets = delivery.build_source_data_sheets(facts)
    path = str(tmp / name)
    delivery.write_source_data_workbook(path, sheets)
    return mdw.load_workbook_snapshot(path), sheets, path


# ---------------------------------------------------------------------------
# Absence is the honest default
# ---------------------------------------------------------------------------


def test_no_prior_renders_nothing():
    facts = _build(_team_data())
    assert "run_delta" not in facts["decision_insights"]
    doc = delivery.build_concise_word_document(facts)
    assert not [
        p.text
        for p in doc.paragraphs
        if p.text.startswith("Since the last comparable report:")
    ]


def test_non_canonical_or_tampered_priors_are_refused():
    facts = _build(_team_data(tac_rows=_ESCALATED_TAC))
    legit = {
        "canonical_snapshot": True,
        "fact_fingerprint": "abc123",
        "formula_cells": 0,
    }
    refusals = [
        None,
        "not-a-mapping",
        {**legit, "canonical_snapshot": False},
        {**legit, "fact_fingerprint": ""},
        {**legit, "formula_cells": 3},
    ]
    for prior in refusals:
        assert (
            delivery._build_run_delta_insight(facts, prior) is None
        ), f"prior {prior!r} must be refused"


def test_incompatible_scope_prior_is_absent():
    with tempfile.TemporaryDirectory() as tmp:
        facts_a = _build(_team_data(), days=30)
        prior, _, _ = _written_snapshot(facts_a, Path(tmp), "a.xlsx")
        facts_b = _build(_team_data(tac_rows=_ESCALATED_TAC), prior=prior)  # days=90
        assert "run_delta" not in facts_b["decision_insights"]


# ---------------------------------------------------------------------------
# Movement is frozen with receipts and survives every validator
# ---------------------------------------------------------------------------


def test_movement_full_chain_word_workbook_web():
    with tempfile.TemporaryDirectory() as tmp:
        facts_a = _build(_team_data())
        prior, _, _ = _written_snapshot(facts_a, Path(tmp), "a.xlsx")
        facts_b = _build(
            _team_data(tac_rows=_ESCALATED_TAC, ap_status="Completed"),
            prior=prior,
            meta={"analysis_id": "run-a", "completed_at": "2026-08-01T00:00:00Z"},
        )
        insight = facts_b["decision_insights"].get("run_delta")
        assert insight, "movement insight must exist for a comparable prior"

        text = insight["paragraph_text"]
        assert text.startswith("Since the last comparable report: ")
        assert f"fingerprint {prior['fact_fingerprint'][:12]}" in text
        assert "TAC Cases 1→4 (+3)" in text
        assert "Action Plans: 1 completed" in text
        assert insight["prior_report"]["fact_fingerprint"] == prior["fact_fingerprint"]
        assert insight["prior_report"]["analysis_id"] == "run-a"
        assert insight["evaluation_as_of_utc"] == str(
            facts_b["evaluation_as_of_utc"]
        )

        # Real current-side receipts, not derivation-only rows.
        assert insight["source_positions"].get("TAC_Cases") == [0, 1, 2, 3]
        assert insight["source_positions"].get("Action_Plans")

        doc = delivery.build_concise_word_document(facts_b)
        rendered = [
            p.text
            for p in doc.paragraphs
            if p.text.startswith("Since the last comparable report:")
        ]
        assert rendered == [text]

        semantics = delivery.validate_word_semantics(facts_b, doc)
        assert semantics.get("ok"), semantics.get("errors")

        snap_b, sheets_b, _ = _written_snapshot(facts_b, Path(tmp), "b.xlsx")
        contract = delivery.validate_cross_artifact_contract(facts_b, sheets_b, doc)
        assert contract.get("ok"), contract.get("errors")

        lineage = sheets_b["Metric_Lineage"]
        assert int((lineage["Metric_Key"] == "insight.run_delta").sum()) == 1
        evidence = sheets_b["Evidence_Links"]
        delta_rows = evidence.loc[evidence["Evidence_Key"] == "insight.run_delta"]
        assert int(delta_rows["Source_Row_Number"].notna().sum()) >= 1

        # The web workspace accepts and projects the insight from disk.
        assert snap_b.get("decision_insight_integrity") == "verified_shape"
        projected_all = snap_b["decision_insights"]
        if isinstance(projected_all, dict):
            projected = projected_all["insight.run_delta"]
        else:
            projected = next(
                item
                for item in projected_all
                if item.get("insight_key") == "insight.run_delta"
            )
        assert projected["claim"] == text
        assert projected["label"] == "Movement since last report"


def test_band_transition_names_the_customer():
    quiet = _team_data(
        tac_rows=[],
        barrier_rows=[],
        pulse_rows=[
            {
                "ID": "CP1",
                "BU_NAME": "Acme",
                "SCORE__C": 9.0,
                "PULSE_DATE_C": "2026-07-20",
            }
        ],
    )
    loud = _team_data(
        tac_rows=_ESCALATED_TAC,
        barrier_rows=[
            {
                "ID": f"AB{i}",
                "BU_NAME": "Acme",
                "SEVERITY_C": "Critical",
                "AB_STATUS_C": "Open",
                "OPEN_DATE_C": "2026-07-15",
            }
            for i in range(1, 5)
        ],
        pulse_rows=[
            {
                "ID": "CP1",
                "BU_NAME": "Acme",
                "SCORE__C": 2.0,
                "PULSE_DATE_C": "2026-07-30",
            }
        ],
    )
    with tempfile.TemporaryDirectory() as tmp:
        facts_a = _build(quiet)
        prior, _, _ = _written_snapshot(facts_a, Path(tmp), "a.xlsx")
        facts_b = _build(loud, prior=prior)
        insight = facts_b["decision_insights"].get("run_delta")
        assert insight
        transitions = insight["comparison"]["band_transitions"]
        assert transitions, "risk band must move between the quiet and loud runs"
        assert transitions[0]["customer"] == "Acme"
        assert transitions[0]["direction"] == "worsened"
        assert "risk bands moved for Acme" in insight["paragraph_text"]
        assert insight["source_positions"].get("Account_Summary") is not None


def test_steady_state_is_a_claim_with_receipts():
    with tempfile.TemporaryDirectory() as tmp:
        facts_a = _build(_team_data())
        prior, _, _ = _written_snapshot(facts_a, Path(tmp), "a.xlsx")
        facts_b = _build(_team_data(), prior=prior)
        insight = facts_b["decision_insights"].get("run_delta")
        assert insight
        assert "no material movement" in insight["paragraph_text"]
        assert insight["source_positions"].get("Account_Summary") == [0]
        assert insight["comparison"]["business_change_count"] == 0


def test_verified_zero_pulse_is_honest_business_movement():
    """Present-but-empty pulse is a verified zero — Round 146 deliberately
    treats it as comparable, so 1→0 IS claimable movement (with the
    state-change caveat), never silently hidden."""

    with tempfile.TemporaryDirectory() as tmp:
        facts_a = _build(_team_data())
        prior, _, _ = _written_snapshot(facts_a, Path(tmp), "a.xlsx")
        facts_b = _build(_team_data(pulse_rows=[]), prior=prior)
        insight = facts_b["decision_insights"].get("run_delta")
        assert insight
        assert "Source coverage changed" in insight["paragraph_text"]
        moved_keys = {
            item["metric_key"] for item in insight["comparison"]["metric_changes"]
        }
        assert "kpi.customer_pulse" in moved_keys


def test_unavailable_source_movement_is_excluded_and_disclosed():
    """A source that stops being retrievable is a coverage change: its KPI
    difference must be excluded from claimed business movement and disclosed."""

    without_pulse = _team_data()
    without_pulse["Alex Rivera"]["customer_pulse"] = None
    with tempfile.TemporaryDirectory() as tmp:
        facts_a = _build(_team_data())
        prior, _, _ = _written_snapshot(facts_a, Path(tmp), "a.xlsx")
        facts_b = _build(without_pulse, prior=prior)
        insight = facts_b["decision_insights"].get("run_delta")
        assert insight
        text = insight["paragraph_text"]
        assert "Source coverage changed" in text or "Excluded" in text
        moved_keys = {
            item["metric_key"] for item in insight["comparison"]["metric_changes"]
        }
        assert not any("pulse" in key for key in moved_keys), (
            "a pulse KPI difference under lost coverage must never be claimed "
            f"as business movement: {sorted(moved_keys)}"
        )


# ---------------------------------------------------------------------------
# Written-artifact parity: the frozen claim equals what the workspace computes
# ---------------------------------------------------------------------------


def test_frozen_movements_match_written_artifact_comparison():
    with tempfile.TemporaryDirectory() as tmp:
        facts_a = _build(_team_data())
        prior, _, _ = _written_snapshot(facts_a, Path(tmp), "a.xlsx")
        facts_b = _build(
            _team_data(tac_rows=_ESCALATED_TAC, ap_status="Completed"), prior=prior
        )
        insight = facts_b["decision_insights"]["run_delta"]
        snap_b, _, _ = _written_snapshot(facts_b, Path(tmp), "b.xlsx")

        recomputed = mdw.compare_snapshots(prior, snap_b)
        assert recomputed["comparison_state"] == "comparable"
        frozen = {
            (item["metric_key"], item["before"], item["after"])
            for item in insight["comparison"]["metric_changes"]
        }
        on_disk = {
            (item["metric_key"], item["before"], item["after"])
            for item in recomputed["metric_changes"]
        }
        assert frozen == on_disk


def test_fingerprint_covers_the_movement_claim():
    with tempfile.TemporaryDirectory() as tmp:
        facts_a = _build(_team_data())
        prior, _, _ = _written_snapshot(facts_a, Path(tmp), "a.xlsx")
        loud = _team_data(tac_rows=_ESCALATED_TAC)
        facts_with = _build(loud, prior=prior)
        facts_without = _build(loud)
        with_fp = delivery.fact_contract_fingerprint(facts_with)
        without_fp = delivery.fact_contract_fingerprint(facts_without)
        assert with_fp != without_fp, (
            "the movement insight must be inside the fact fingerprint"
        )
        assert delivery.fact_contract_fingerprint(_build(loud, prior=prior)) == with_fp


# ---------------------------------------------------------------------------
# Failure classification: current side blocks, prior side fails soft
# ---------------------------------------------------------------------------


def test_prior_side_failure_fails_soft(monkeypatch):
    def _boom(*args, **kwargs):
        raise ValueError("simulated prior-side comparison failure")

    monkeypatch.setattr(mdw, "compare_snapshots", _boom)
    prior = {
        "canonical_snapshot": True,
        "fact_fingerprint": "abc123",
        "formula_cells": 0,
    }
    facts = _build(_team_data(), prior=prior)
    assert "run_delta" not in facts["decision_insights"]


def test_current_side_failure_blocks_publication(monkeypatch):
    def _boom(*args, **kwargs):
        raise ValueError("simulated current-side sheet failure")

    monkeypatch.setattr(delivery, "build_source_data_sheets", _boom)
    prior = {
        "canonical_snapshot": True,
        "fact_fingerprint": "abc123",
        "formula_cells": 0,
    }
    with pytest.raises(delivery._RunDeltaCurrentSideError):
        _build(_team_data(), prior=prior)


# ---------------------------------------------------------------------------
# Prior selection (manager_decision_workspace.find_prior_comparable_snapshot)
# ---------------------------------------------------------------------------


def _history_row(analysis_id, completed_at, **overrides):
    row = {
        "request_id": analysis_id,
        "report_type": "leader",
        "manager": "Alex Rivera",
        "technology": "",
        "scope_type": "team",
        "scope_value": "",
        "days": 90,
        "status": "completed",
        "end_time": completed_at,
    }
    row.update(overrides)
    return row


def test_find_prior_prefers_newest_completed_matching_scope():
    loaded = []

    def _loader(path):
        loaded.append(path)
        return {
            "canonical_snapshot": True,
            "fact_fingerprint": f"fp-{path}",
            "formula_cells": 0,
        }

    records = [
        _history_row("old", "2026-07-01T00:00:00Z"),
        _history_row("newest", "2026-08-01T00:00:00Z"),
        _history_row("running", "2026-08-02T00:00:00Z", status="running"),
        _history_row("other-manager", "2026-08-02T00:00:00Z", manager="Sam Lee"),
        _history_row("other-days", "2026-08-02T00:00:00Z", days=30),
        _history_row("current", "2026-08-03T00:00:00Z"),
    ]
    result = mdw.find_prior_comparable_snapshot(
        records,
        report_type="Leader",
        manager="Alex Rivera",
        technology="All",
        scope_type="team",
        scope_value="Alex Rivera's Team",
        days=90,
        exclude_analysis_id="current",
        workbook_path_for=lambda record, raw: record["analysis_id"] + ".xlsx",
        load=_loader,
    )
    assert result is not None
    snapshot, meta = result
    assert meta["analysis_id"] == "newest"
    assert snapshot["fact_fingerprint"] == "fp-newest.xlsx"
    assert loaded == ["newest.xlsx"]


def test_find_prior_skips_broken_and_non_canonical_priors():
    snapshots = {
        "b.xlsx": {"canonical_snapshot": False, "fact_fingerprint": "x", "formula_cells": 0},
        "c.xlsx": {"canonical_snapshot": True, "fact_fingerprint": "", "formula_cells": 0},
        "d.xlsx": {"canonical_snapshot": True, "fact_fingerprint": "y", "formula_cells": 2},
        "e.xlsx": {"canonical_snapshot": True, "fact_fingerprint": "good", "formula_cells": 0},
    }

    def _loader(path):
        if path == "a.xlsx":
            raise ValueError("corrupt workbook")
        return snapshots[path]

    records = [
        _history_row("a", "2026-08-05T00:00:00Z"),
        _history_row("b", "2026-08-04T00:00:00Z"),
        _history_row("c", "2026-08-03T00:00:00Z"),
        _history_row("d", "2026-08-02T00:00:00Z"),
        _history_row("e", "2026-08-01T00:00:00Z"),
    ]
    result = mdw.find_prior_comparable_snapshot(
        records,
        report_type="Leader",
        manager="Alex Rivera",
        technology="",
        scope_type="team",
        scope_value="",
        days=90,
        workbook_path_for=lambda record, raw: record["analysis_id"] + ".xlsx",
        load=_loader,
    )
    assert result is not None
    _, meta = result
    assert meta["analysis_id"] == "e"


def test_find_prior_returns_none_without_resolver_or_candidates():
    assert (
        mdw.find_prior_comparable_snapshot(
            [_history_row("x", "2026-08-01T00:00:00Z")],
            report_type="Leader",
            manager="Alex Rivera",
            technology="",
            scope_type="team",
            scope_value="",
            days=90,
            workbook_path_for=None,
        )
        is None
    )
    assert (
        mdw.find_prior_comparable_snapshot(
            [],
            report_type="Leader",
            manager="Alex Rivera",
            technology="",
            scope_type="team",
            scope_value="",
            days=90,
            workbook_path_for=lambda record, raw: "x.xlsx",
        )
        is None
    )


# ---------------------------------------------------------------------------
# Surface policy: movement leads "What Is Changing" and survives the limit
# ---------------------------------------------------------------------------


def test_run_delta_leads_the_rendered_insights():
    with tempfile.TemporaryDirectory() as tmp:
        facts_a = _build(_team_data())
        prior, _, _ = _written_snapshot(facts_a, Path(tmp), "a.xlsx")
        facts_b = _build(_team_data(tac_rows=_ESCALATED_TAC), prior=prior)
        rendered = delivery._rendered_decision_insight_names(facts_b)
        assert rendered and rendered[0] == "run_delta"
        assert len(rendered) <= 3  # Leader insight_limit


# ---------------------------------------------------------------------------
# Ask AI surfaces
# ---------------------------------------------------------------------------


def _fake_risk_profiles():
    return {
        "Acme": {
            "risk_band": "HIGH",
            "risk_score_0_100": 61.2,
            "risk_factors": ["4 open TAC cases [Source: TAC_Cases]"],
            "next_best_action": "Engage TAC duty manager on the two P1 cases.",
        }
    }


def test_decision_context_leads_with_movement_claim():
    import ask_ai_grounded as ask_ai

    claim = (
        "Since the last comparable report: vs the 2026-08-01 report "
        "(fingerprint abc123def456): KPIs moved: TAC Cases 1→4 (+3)."
    )
    block = ask_ai.build_decision_context_block(
        _fake_risk_profiles(), movement_claim=claim
    )
    assert "movement_since_last_report:" in block
    stripped = block.split("movement_since_last_report:", 1)[1]
    assert "Since the last comparable report:" not in stripped.split("\n")[0]
    assert "fingerprint abc123def456" in block
    movement_at = block.index("movement_since_last_report:")
    ranking_at = block.index("1. Acme")
    assert movement_at < ranking_at, "movement must lead the ranking"

    without = ask_ai.build_decision_context_block(_fake_risk_profiles())
    assert "movement_since_last_report" not in without


def test_ask_ai_binding_carries_frozen_insights():
    snapshot = {
        "analysis_id": "run-b",
        "report_type": "leader",
        "manager": "Alex Rivera",
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "decision_insights": [
            {
                "insight_key": "insight.run_delta",
                "label": "Movement since last report",
                "claim": "Since the last comparable report: vs ... TAC Cases 1→4 (+3).",
                "caveat": "",
                "source_state": "available",
            },
            {"not_an_insight": True},
        ],
    }
    binding = mdw.ask_ai_binding(snapshot)
    insights = binding["decision_insights"]
    assert len(insights) == 1
    assert insights[0]["insight_key"] == "insight.run_delta"
    assert insights[0]["claim"].startswith("Since the last comparable report:")


def test_report_bound_bundle_insights_are_optional_for_legacy_cassettes():
    """Pre-171 frozen bundles carry no ``decision_insights`` key; the
    report-bound evidence loop must treat that as an empty, silent no-op so
    every recorded replay cassette stays byte-stable."""

    legacy_bundle = {"decision_metrics": [], "action_plans": [], "accounts": []}
    assert (legacy_bundle.get("decision_insights") or []) == []
