"""DecisionMetric evidence and real-path grounding regressions."""

from __future__ import annotations

import re

import pandas as pd
import pytest

import ask_ai_grounded as grounded


AS_OF = pd.Timestamp("2026-08-04T12:00:00Z")


def _decision_profiles(count: int = 2) -> dict[str, dict]:
    profiles = {
        "Acme": {
            "risk_score_0_100": 81.0,
            "risk_band": "CRITICAL",
            "risk_factors": [
                "Compound risk in Webex Calling [Source: derived]",
                "Critical adoption barrier",
                "Three P1 support cases",
            ],
            "next_best_action": "Contact the executive sponsor today.",
        },
        "Beta": {
            "risk_score_0_100": 22.0,
            "risk_band": "LOW",
            "risk_factors": ["One unresolved action plan"],
            "next_best_action": "Maintain the standard success cadence.",
        },
    }
    for index in range(3, count + 1):
        profiles[f"Customer {index}"] = {
            "risk_score_0_100": float(22 - index),
            "risk_band": "LOW",
            "risk_factors": ["Low activity volume"],
            "next_best_action": "Continue monitoring.",
        }
    return profiles


def _decision_outlooks() -> dict[str, dict]:
    return {
        "Acme": {
            "tier": "ELEVATED",
            "points": 55,
            "calibration_state": "uncalibrated_prior",
        },
    }


def test_decision_metric_exact_row_survives_compose_with_all_claim_parts() -> None:
    records = grounded.build_decision_evidence_records(
        _decision_profiles(),
        timestamp=AS_OF,
        outlooks=_decision_outlooks(),
    )
    repeated = grounded.build_decision_evidence_records(
        _decision_profiles(),
        timestamp=AS_OF,
        outlooks=_decision_outlooks(),
    )
    top = records[0]

    assert [record.source_id for record in records] == [
        record.source_id for record in repeated
    ]
    assert top.source_type == "DecisionMetric"
    assert top.source_id.startswith("METRIC-DECISION-R01-")
    for expected in (
        "Canonical decision rank 1: Acme",
        "Risk band: CRITICAL",
        "Top drivers:",
        "Next best action:",
        "30-day escalation outlook: ELEVATED",
        "relative ranking only, not a probability",
    ):
        assert expected in top.text

    answer, rejected = grounded.compose_grounded_answer(
        {
            "executive_summary": "",
            "claims": [{
                "statement": top.text,
                "citations": [top.source_id],
            }],
            "actions": [],
            "unknowns": [],
        },
        {top.source_id},
        evidence_records=[grounded._r98_evidence_record_to_dict(top)],  # noqa: SLF001
    )

    assert rejected == 0
    assert top.text in answer
    assert f"[Sources: {top.source_id}]" in answer


def test_tampered_and_non_rendered_decision_metric_ids_are_rejected() -> None:
    rendered = grounded.build_decision_evidence_records(
        _decision_profiles(count=6),
        timestamp=AS_OF,
        cap=5,
        outlooks=_decision_outlooks(),
    )
    all_records = grounded.build_decision_evidence_records(
        _decision_profiles(count=6),
        timestamp=AS_OF,
        cap=6,
        outlooks=_decision_outlooks(),
    )
    allowed = {record.source_id for record in rendered}
    evidence = [
        grounded._r98_evidence_record_to_dict(record)  # noqa: SLF001
        for record in rendered
    ]
    tampered = rendered[0].source_id[:-1] + (
        "0" if rendered[0].source_id[-1] != "0" else "1"
    )
    non_rendered = all_records[5]

    for statement, citation in (
        (rendered[0].text, tampered),
        (non_rendered.text, non_rendered.source_id),
    ):
        answer, rejected = grounded.compose_grounded_answer(
            {
                "executive_summary": "",
                "claims": [{"statement": statement, "citations": [citation]}],
                "actions": [],
                "unknowns": [],
            },
            allowed,
            evidence_records=evidence,
        )

        assert rejected == 1
        assert "### Supported Findings" not in answer
        assert "no exact allowed source citation" in answer


def _run_real_decision_question(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
) -> tuple[dict, dict]:
    import adoptiq_backend
    import incident_storage

    class Connection:
        def close(self) -> None:
            return None

    subscriptions = pd.DataFrame([
        {
            "ACCOUNT_ID_C": "A-1",
            "SUBSCRIPTION_ID": "SUB-1",
            "BU_NAME": "Acme",
            "CSSM_EMAIL": "a@example.com",
        },
        {
            "ACCOUNT_ID_C": "B-1",
            "SUBSCRIPTION_ID": "SUB-2",
            "BU_NAME": "Beta",
            "CSSM_EMAIL": "a@example.com",
        },
    ])
    snowflake_ab = pd.DataFrame([
        {
            "ID": "AB-1",
            "ACCOUNT_ID_C": "A-1",
            "BU_NAME": "Acme",
            "SUBJECT_C": "Shared rollout barrier",
            "SEVERITY_C": "High",
            "STATUS_C": "Open",
            "OPEN_DATE_C": "2026-07-01",
        },
        {
            "ID": "AB-2",
            "ACCOUNT_ID_C": "B-1",
            "BU_NAME": "Beta",
            "SUBJECT_C": "Snowflake-only barrier",
            "SEVERITY_C": "Medium",
            "STATUS_C": "Open",
            "OPEN_DATE_C": "2026-07-02",
        },
    ])
    csconsole_ab = pd.DataFrame([
        {
            "ID": "AB-1",
            "ACCOUNT_ID_C": "A-1",
            "BU_NAME": "Acme",
            "SUBJECT_C": "Shared rollout barrier",
            "STATUS_C": "Open",
        },
        {
            "ID": "AB-3",
            "ACCOUNT_ID_C": "A-1",
            "BU_NAME": "Acme",
            "SUBJECT_C": "CSConsole-only executive blocker",
            "SEVERITY_C": "Critical",
            "STATUS_C": "Open",
            "OPEN_DATE_C": "2026-07-03",
        },
    ])
    cases = pd.DataFrame([
        {
            "CASE_ID": "CASE-OLD-A",
            "ACCOUNT_ID_C": "A-1",
            "BU_NAME": "Acme",
            "SUBJECT": "Historical baseline",
            "STATUS": "Closed",
            "Severity": "P3",
            "open_date": "2026-04-01",
        },
        *[
            {
                "CASE_ID": f"CASE-A-{index}",
                "ACCOUNT_ID_C": "A-1",
                "BU_NAME": "Acme",
                "SUBJECT": "Recent escalation",
                "STATUS": "Open",
                "Severity": "P1",
                "open_date": f"2026-08-0{index}",
            }
            for index in range(1, 4)
        ],
        {
            "CASE_ID": "CASE-OLD-B",
            "ACCOUNT_ID_C": "B-1",
            "BU_NAME": "Beta",
            "SUBJECT": "Historical low-priority case",
            "STATUS": "Closed",
            "Severity": "P4",
            "open_date": "2026-04-01",
        },
    ])

    monkeypatch.setattr(
        adoptiq_backend,
        "TEAM_ROSTER",
        (("Manager One", "Alex", "a@example.com"),),
    )
    monkeypatch.setattr(adoptiq_backend, "_connect_with_keeper", lambda: Connection())
    monkeypatch.setattr(
        adoptiq_backend,
        "get_subscriptions_for_team",
        lambda *_args, **_kwargs: subscriptions,
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "compute_barrier_aging",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "scan_historical_reports",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "build_cross_report_trends",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        incident_storage,
        "get_all_external_intel",
        lambda **_kwargs: {
            "incidents": [],
            "maintenances": [],
            "bugs": [],
            "fetch_errors": {},
            "source_states": {},
            "list_truncated": {},
        },
    )

    def fake_prefetch(_run_ctx, *, include_datasets):
        bundle = {name: pd.DataFrame() for name in include_datasets}
        bundle.update({
            # Keep the empty modern-key placeholder beside the populated
            # legacy key: an empty frame must not mask the Snowflake feed.
            "adoption_barriers": pd.DataFrame(),
            "ab_data": snowflake_ab.copy(),
            "csconsole_adoption_barriers": csconsole_ab.copy(),
            "support_cases_snowflake": cases.copy(),
            "csconsole_action_plans": pd.DataFrame([{
                "ID": "AP-1",
                "ACCOUNT_ID_C": "A-1",
                "BU_NAME": "Acme",
                "STATUS_C": "Open",
                "SUBJECT_C": "Executive recovery plan",
            }]),
            "csconsole_customer_pulse": pd.DataFrame([{
                "ID": "PULSE-1",
                "ACCOUNT_ID_C": "A-1",
                "BU_NAME": "Acme",
                "PULSE_RATING__C": "Poor",
                "SCORE__C": 2,
                "PULSE_DATE_C": "2026-08-01",
            }]),
            "csconsole_success_priorities": pd.DataFrame(),
        })
        return bundle

    captured: dict[str, object] = {}
    decision_line = re.compile(
        r"^- \[SourceID: (?P<source_id>METRIC-DECISION-[^\]]+)\] "
        r"\[DecisionMetric\] Customer: (?P<customer>.*?) \| "
        r"Time: .*? \| (?P<text>.*)$",
        flags=re.MULTILINE,
    )

    def fake_model(system_prompt, user_prompt, _schema, **_kwargs):
        matches = list(decision_line.finditer(user_prompt))
        assert matches, user_prompt
        top = matches[0]
        captured.update({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "source_id": top.group("source_id"),
            "customer": top.group("customer"),
            "text": top.group("text"),
        })
        return {
            "ok": True,
            "data": {
                "executive_summary": "",
                "claims": [{
                    "statement": top.group("text"),
                    "citations": [top.group("source_id")],
                }],
                "actions": [],
                "unknowns": [],
            },
        }

    monkeypatch.setattr(grounded, "prefetch_ask_ai_grounded", fake_prefetch)
    monkeypatch.setattr(
        adoptiq_backend,
        "generate_llm_json_response",
        fake_model,
    )

    result = grounded.run_portfolio_grounded_ask_ai(grounded.AskAIRequest(
        question=question,
        turn_question=question,
        manager="Manager One",
        technology="All",
        days=90,
    ))
    return result, captured


@pytest.mark.parametrize(
    ("question", "required_text"),
    [
        (
            "Who should I call first and why?",
            ("Canonical decision rank 1", "Top drivers:", "Next best action:"),
        ),
        (
            "Which customer is likely to escalate?",
            ("30-day escalation outlook:", "not a probability"),
        ),
    ],
)
def test_real_portfolio_path_renders_and_resolves_decision_metrics(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    required_text: tuple[str, ...],
) -> None:
    result, captured = _run_real_decision_question(monkeypatch, question)

    assert result["ok"] is True
    assert captured["customer"] == "Acme"
    assert "DecisionMetric Evidence" in str(captured["system_prompt"])
    assert captured["source_id"] in str(captured["user_prompt"])
    for expected in required_text:
        assert expected in str(captured["text"])
        assert expected in result["answer"]
    assert f"[Sources: {captured['source_id']}]" in result["answer"]
    decision_records = [
        record
        for record in result["evidence_records"]
        if record["source_type"] == "DecisionMetric"
    ]
    assert decision_records
    assert decision_records[0]["source_id"] == captured["source_id"]

    # The real path must union both AB feeds: AB-1 is shared and counted once;
    # AB-2 / AB-3 are unique and neither source is ignored.
    assert result["canonical_headline"]["total_barriers"] == 3
    barrier_records = {
        record["source_id"]: record
        for record in result["evidence_records"]
        if record["source_type"] == "AdoptionBarrier"
    }
    assert set(barrier_records) == {"AB-1", "AB-2", "AB-3"}
    assert (
        "Snowflake Adoption Barriers + CSConsole Adoption Barriers"
        in barrier_records["AB-1"]["text"]
    )
