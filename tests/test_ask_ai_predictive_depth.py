"""Ask AI predictive-depth, renewal, maintenance, and CSC regressions."""

from __future__ import annotations

import re

import pandas as pd
import pytest

import ask_ai_grounded as grounded
from defect_correlation import build_defect_correlation_bundle


AS_OF = pd.Timestamp("2026-08-11T12:00:00Z")


def _empty_portfolio_payload() -> dict:
    return {
        "adoption_barriers": pd.DataFrame(),
        "support_cases_snowflake": pd.DataFrame(),
        "csconsole_customer_pulse": pd.DataFrame(),
        "csconsole_success_priorities": pd.DataFrame(),
        "csconsole_action_plans": pd.DataFrame(),
        "incidents": [],
        "bugs": [],
    }


def test_portfolio_maintenance_is_a_bounded_exact_citation_record() -> None:
    payload = _empty_portfolio_payload()
    payload["maintenances"] = [
        {
            "id": "MAINT-2026-08-11",
            "status": "scheduled",
            "title": "Webex Calling maintenance",
            "published": "2026-08-11T04:00:00Z",
        },
        {"title": "missing stable id must not be citable"},
    ]

    records, ids = grounded._portfolio_records_from_payload(  # noqa: SLF001
        payload,
        question="What maintenance is scheduled?",
    )
    maintenance = [record for record in records if record.source_type == "Maintenance"]

    assert len(maintenance) == 1
    assert maintenance[0].source_id == "MAINT-2026-08-11"
    assert "scheduled" in maintenance[0].text
    assert "Webex Calling maintenance" in maintenance[0].text
    assert grounded._normalize_claim_id("MAINT-2026-08-11") in ids  # noqa: SLF001

    context, allowed, used = grounded.build_evidence_context(
        records,
        "What maintenance is scheduled?",
        domains=["intel"],
    )
    assert used == 1
    assert "[SourceID: MAINT-2026-08-11]" in context
    assert grounded._normalize_claim_id("MAINT-2026-08-11") in allowed  # noqa: SLF001


def test_bundle_trust_state_never_labels_failed_empty_frame_as_zero() -> None:
    successful_zero = pd.DataFrame()
    failed = pd.DataFrame()
    failed.attrs["fetch_error"] = "query failed"
    unavailable = pd.DataFrame()
    unavailable.attrs.update({
        "source_unavailable": True,
        "source_unavailable_detail": "source not supplied",
    })
    partial = pd.DataFrame([{"ID": "ROW-1"}])
    partial.attrs["partial"] = True

    states = grounded._ask_ai_bundle_source_states({  # noqa: SLF001
        "successful_zero": successful_zero,
        "failed": failed,
        "unavailable": unavailable,
        "partial": partial,
    })

    assert states == {
        "failed": "failed",
        "partial": "partial",
        "successful_zero": "zero",
        "unavailable": "unavailable",
    }


def _predictive_frames(*, failed_optional_sources: bool) -> dict[str, pd.DataFrame]:
    subscriptions = pd.DataFrame([
        {
            "SUBSCRIPTION_ID": "SUB-1",
            "ACCOUNT_ID_C": "A-1",
            "BU_NAME": "Acme",
        }
    ])
    tac = pd.DataFrame([
        {
            "CASE_ID": "CASE-OLD",
            "ACCOUNT_ID_C": "A-1",
            "BU_NAME": "Acme Incorporated",
            "Customer": "Acme Incorporated",
            "Severity": "P3",
            "open_date": "2026-04-01",
            "STATUS": "Closed",
        },
        {
            "CASE_ID": "CASE-NEW",
            "ACCOUNT_ID_C": "A-1",
            "BU_NAME": "Acme Incorporated",
            "Customer": "Acme Incorporated",
            "Severity": "P1",
            "open_date": "2026-08-01",
            "STATUS": "Open",
        },
    ])
    adoption_barriers = pd.DataFrame(
        columns=["ID", "ACCOUNT_ID_C", "BU_NAME", "OPEN_DATE_C", "SEVERITY_C"]
    )
    pulse = pd.DataFrame(
        columns=["ID", "ACCOUNT_ID_C", "BU_NAME", "PULSE_DATE_C", "SCORE__C"]
    )
    if failed_optional_sources:
        adoption_barriers.attrs["fetch_error"] = "adoption barrier feed failed"
        pulse.attrs["fetch_error"] = "pulse feed failed"
    return {
        "subscriptions": subscriptions,
        "action_plans": pd.DataFrame(),
        "adoption_barriers": adoption_barriers,
        "customer_pulse": pulse,
        "tac_cases": tac,
        "success_priorities": pd.DataFrame(),
    }


def test_shared_predictive_seam_is_id_first_and_failed_is_not_zero() -> None:
    from decision_report_delivery import build_canonical_predictive_outlooks

    complete = build_canonical_predictive_outlooks(
        _predictive_frames(failed_optional_sources=False),
        as_of=AS_OF,
    )
    failed = build_canonical_predictive_outlooks(
        _predictive_frames(failed_optional_sources=True),
        as_of=AS_OF,
    )

    # Stable account ID collapses Acme and Acme Incorporated into one exact
    # report/Ask identity. A successful empty frame is complete evidence.
    assert [identity["label"] for identity in complete["identities"]] == ["Acme"]
    assert complete["coverage_by_customer"]["Acme"]["source_states"] == {
        "tac_cases": "available",
        "adoption_barriers": "zero",
        "customer_pulse": "zero",
    }
    assert complete["outlooks"]["Acme"]["coverage_state"] == "available"

    # The same empty shapes carrying retrieval failures remain a lower-bound
    # signal, cannot use calibration, and name both missing sources.
    outlook = failed["outlooks"]["Acme"]
    assert outlook["coverage_state"] == "partial"
    assert outlook["calibration_state"] == "withheld_incomplete_coverage"
    assert outlook["relative_signal_state"] == "lower_bound_relative_signal"
    assert outlook["missing_sources"] == ["adoption_barriers", "customer_pulse"]

    profile = {
        "Acme": {
            "risk_score_0_100": 70.0,
            "risk_band": "HIGH",
            "risk_factors": ["One P1 support case"],
            "next_best_action": "Resolve the P1 case.",
        }
    }
    evidence = grounded.build_decision_evidence_records(
        profile,
        timestamp=AS_OF,
        outlooks=failed["outlooks"],
    )[0]
    assert "lower-bound relative signal only" in evidence.text
    assert "adoption_barriers, customer_pulse" in evidence.text
    assert "not a probability" in evidence.text


def test_withheld_escalation_coverage_distinguishes_failure_from_cold_start() -> None:
    profiles = {
        "Acme": {
            "risk_score_0_100": 70.0,
            "risk_band": "HIGH",
            "risk_factors": [],
            "next_best_action": "Review evidence.",
        }
    }
    failed = grounded.build_escalation_coverage_evidence_records(
        profiles,
        {
            "Acme": {
                "coverage_state": "unavailable",
                "outlook_state": "unavailable",
                "source_states": {
                    "tac_cases": "failed",
                    "adoption_barriers": "zero",
                    "customer_pulse": "zero",
                },
                "missing_sources": ["tac_cases"],
            }
        },
        outlooks={},
        timestamp=AS_OF,
    )[0]
    cold_start = grounded.build_escalation_coverage_evidence_records(
        profiles,
        {
            "Acme": {
                "coverage_state": "available",
                "outlook_state": "insufficient_history",
                "source_states": {
                    "tac_cases": "zero",
                    "adoption_barriers": "zero",
                    "customer_pulse": "zero",
                },
                "missing_sources": [],
            }
        },
        outlooks={},
        timestamp=AS_OF,
    )[0]

    assert "tac_cases=failed" in failed.text
    assert "unavailable, not zero" in failed.text
    assert "minimum dated-history gate" in cold_start.text
    assert "complete zero evidence" in cold_start.text.casefold()
    assert failed.source_id != cold_start.source_id


def _renewal_insights() -> dict:
    return {
        "_meta": {
            "account_batch_truncated": False,
            "subsection_errors": {},
        },
        "renewals": {
            "details": [
                {
                    "contract": "CONTRACT-A",
                    "account_id": "A-1",
                    "probability": 35.0,
                    "status": "At Risk",
                },
                {
                    "contract": "CONTRACT-B",
                    "account_id": "B-1",
                    "probability": 92.0,
                    "status": "Likely",
                },
            ],
            "was_truncated": False,
        },
        "contracts": {
            "upcoming_expirations": [
                {"contract": "CONTRACT-A", "end_date": "2026-09-15"},
                {"contract": "CONTRACT-B", "end_date": "2026-12-20"},
            ],
            "was_truncated": False,
        },
    }


def _renewal_identities() -> list[dict]:
    return [
        {
            "identity_key": "account:a-1",
            "label": "Acme",
            "account_ids": ("a-1",),
        },
        {
            "identity_key": "account:b-1",
            "label": "Beta",
            "account_ids": ("b-1",),
        },
    ]


def test_renewal_outlook_is_customer_specific_and_separate_from_escalation() -> None:
    outlooks, coverage = grounded.build_renewal_outlooks(
        _renewal_insights(),
        _renewal_identities(),
        as_of=AS_OF,
    )

    assert coverage["coverage_state"] == "available"
    assert outlooks["Acme"]["primary"] == {
        "contract": "CONTRACT-A",
        "account_id": "A-1",
        "source_provided_probability_pct": 35.0,
        "status": "At Risk",
        "service_end_date": "2026-09-15",
    }
    assert outlooks["Beta"]["primary"]["source_provided_probability_pct"] == 92.0
    assert outlooks["Beta"]["primary"]["service_end_date"] == "2026-12-20"
    assert outlooks["Acme"]["calibration_state"] == (
        "source_provided_not_adoptiq_calibrated"
    )
    assert "35%" in outlooks["Acme"]["next_best_action"]
    assert "92%" in outlooks["Beta"]["next_best_action"]
    assert all("escalation" not in str(outlook).casefold() for outlook in outlooks.values())

    records = grounded.build_renewal_outlook_evidence_records(
        outlooks,
        coverage=coverage,
        timestamp=AS_OF,
    )
    assert [record.customer for record in records] == ["Acme", "Beta"]
    assert "not an AdoptIQ-calibrated probability" in records[0].text
    assert "Service end date: 2026-09-15" in records[0].text


def test_renewal_evidence_ids_are_stable_across_retrieval_clocks() -> None:
    first_outlooks, first_coverage = grounded.build_renewal_outlooks(
        _renewal_insights(),
        _renewal_identities(),
        as_of="2026-08-11T12:00:00Z",
    )
    second_outlooks, second_coverage = grounded.build_renewal_outlooks(
        _renewal_insights(),
        _renewal_identities(),
        as_of="2026-08-11T12:00:07Z",
    )

    first = grounded.build_renewal_outlook_evidence_records(
        first_outlooks,
        coverage=first_coverage,
        timestamp="2026-08-11T12:00:00Z",
    )
    second = grounded.build_renewal_outlook_evidence_records(
        second_outlooks,
        coverage=second_coverage,
        timestamp="2026-08-11T12:00:07Z",
    )

    assert [record.source_id for record in first] == [record.source_id for record in second]
    assert [record.text for record in first] == [record.text for record in second]
    assert [record.timestamp for record in first] != [record.timestamp for record in second]


def test_renewal_outlook_failure_is_not_a_zero() -> None:
    failed = {
        "_meta": {
            "account_batch_truncated": False,
            "subsection_errors": {"renewals": "RENEWAL_DATA query failed"},
        },
        "contracts": {"upcoming_expirations": [], "was_truncated": False},
    }
    outlooks, coverage = grounded.build_renewal_outlooks(
        failed,
        _renewal_identities(),
        as_of=AS_OF,
    )

    assert outlooks == {}
    assert coverage["coverage_state"] == "failed"
    assert coverage["missing_sources"] == ["RENEWAL_DATA"]
    record = grounded.build_renewal_outlook_evidence_records(
        outlooks,
        coverage=coverage,
        timestamp=AS_OF,
    )[0]
    assert "Customer-level renewal outlook unavailable" in record.text
    assert "Missing sources: RENEWAL_DATA" in record.text
    assert "No customer renewal probability" in record.text


def test_bst_reference_exact_citation_and_tamper_rejection() -> None:
    correlation = build_defect_correlation_bundle(
        pd.DataFrame([
            {
                "CASE_ID": "CASE-1",
                "ACCOUNT_ID_C": "A-1",
                "BU_NAME": "Acme",
                "bemscsc_refs": "CSCwa12345 BEMS-7788",
            }
        ]),
        pd.DataFrame([
            {
                "ID": "AB-1",
                "ACCOUNT_ID_C": "A-1",
                "BU_NAME": "Acme",
                "DESCRIPTION_C": "Blocked by cscWA12345 and BEMS-7788",
            }
        ]),
        [
            {
                "bug_id": "CSCWA12345",
                "status": "Open",
                "severity": "High",
                "version": "44.1",
                "title": "Calling policy defect",
            }
        ],
    )
    records = grounded.build_defect_correlation_evidence_records(
        correlation,
        timestamp=AS_OF,
    )
    assert len(records) == 1
    record = records[0]
    assert record.source_id.startswith("BSTREF-CSCWA12345-")
    assert "Parent record IDs: AB-1, CASE-1" in record.text
    assert "status=Open" in record.text
    assert "severity=High" in record.text
    assert "version=44.1" in record.text
    assert "BEMS" not in record.source_id

    evidence = [grounded._r98_evidence_record_to_dict(record)]  # noqa: SLF001
    answer, rejected = grounded.compose_grounded_answer(
        {
            "executive_summary": "",
            "claims": [{"statement": record.text, "citations": [record.source_id]}],
            "actions": [],
            "unknowns": [],
        },
        {record.source_id},
        evidence_records=evidence,
    )
    assert rejected == 0
    assert record.text in answer

    tampered = record.source_id[:-1] + (
        "0" if record.source_id[-1] != "0" else "1"
    )
    answer, rejected = grounded.compose_grounded_answer(
        {
            "executive_summary": "",
            "claims": [{"statement": record.text, "citations": [tampered]}],
            "actions": [],
            "unknowns": [],
        },
        {record.source_id},
        evidence_records=evidence,
    )
    assert rejected == 1
    assert "### Supported Findings" not in answer


def test_enhanced_renewal_query_retains_account_id() -> None:
    from adoptiq_backend import fetch_enhanced_account_insights

    class Cursor:
        description: list[tuple[str]] = []
        query = ""

        def execute(self, query, _params=()):
            self.query = " ".join(str(query).split())
            return self

        def fetchone(self):
            if "FROM CX_DB.CX_SWSSBST_BR.RENEWAL_DATA" in self.query:
                return (1, 35.0, 35.0, 1)
            return None

        def fetchall(self):
            if (
                "SELECT CONTRACT_NUMBER, ACCOUNT_ID_C, RENEWAL_STATUS" in self.query
            ):
                self.description = [
                    ("CONTRACT_NUMBER",),
                    ("ACCOUNT_ID_C",),
                    ("RENEWAL_STATUS",),
                    ("RENEWAL_PROBABILITY",),
                ]
                return [("CONTRACT-A", "A-1", "At Risk", 35.0)]
            self.description = []
            return []

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

    result = fetch_enhanced_account_insights(Connection(), ["A-1"], days=90)

    assert result["renewals"]["details"] == [
        {
            "contract": "CONTRACT-A",
            "account_id": "A-1",
            "probability": 35.0,
            "status": "At Risk",
        }
    ]
    assert result["renewals"]["at_risk"][0]["account_id"] == "A-1"


def _run_real_supplemental_evidence_question(
    monkeypatch: pytest.MonkeyPatch,
    *,
    question: str,
    source_type: str,
    include_unresolved_defect: bool = False,
) -> tuple[dict, dict]:
    import adoptiq_backend
    import incident_storage

    class Connection:
        def close(self):
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
    support_case_rows = [
        {
            "CASE_ID": "CASE-ACME",
            "ACCOUNT_ID_C": "A-1",
            "BU_NAME": "Acme Incorporated",
            "SUBJECT": "Calling defect",
            "DESCRIPTION": "Customer impact linked to CSCwa12345",
            "bemscsc_refs": "CSCwa12345",
            "Severity": "P2",
            "STATUS": "Open",
            "open_date": "2026-04-01",
        }
    ]
    if include_unresolved_defect:
        support_case_rows.append({
            "CASE_ID": "CASE-UNRESOLVED",
            "ACCOUNT_ID_C": "",
            "BU_NAME": "Unknown",
            "SUBJECT": "Unresolved identity defect",
            "DESCRIPTION": "Customer impact linked to CSCzz99999",
            "bemscsc_refs": "CSCzz99999",
            "Severity": "P2",
            "STATUS": "Open",
            "open_date": "2026-04-02",
        })
    support_cases = pd.DataFrame(support_case_rows)
    adoption_barriers = pd.DataFrame([
        {
            "ID": "AB-ACME",
            "ACCOUNT_ID_C": "A-1",
            "BU_NAME": "Acme",
            "SUBJECT_C": "Deployment blocker",
            "DESCRIPTION_C": "Blocked by CSCWA12345",
            "SEVERITY_C": "High",
            "STATUS_C": "Open",
            "OPEN_DATE_C": "2026-06-01",
        }
    ])
    enhanced = _renewal_insights()

    monkeypatch.setattr(
        adoptiq_backend,
        "TEAM_ROSTER",
        (("Manager One", "Alex", "a@example.com"),),
    )
    monkeypatch.setattr(adoptiq_backend, "_connect_with_keeper", lambda: Connection())
    monkeypatch.setattr(
        adoptiq_backend,
        "get_subscriptions_for_team",
        lambda *_args, **_kwargs: subscriptions.copy(),
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "compute_barrier_aging",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(adoptiq_backend, "scan_historical_reports", lambda *_a, **_k: [])
    monkeypatch.setattr(adoptiq_backend, "build_cross_report_trends", lambda *_a, **_k: {})
    monkeypatch.setattr(
        incident_storage,
        "get_all_external_intel",
        lambda **_kwargs: {
            "incidents": [],
            "maintenances": [
                {
                    "id": "MAINT-1",
                    "status": "scheduled",
                    "title": "Calling maintenance",
                    "published": "2026-08-11T01:00:00Z",
                }
            ],
            "bugs": [
                {
                    "bug_id": "CSCWA12345",
                    "status": "Open",
                    "severity": "High",
                    "version": "44.1",
                    "title": "Calling policy defect",
                }
            ],
            "fetch_errors": {},
            "source_states": {
                "incidents": "zero",
                "maintenances": "available",
                "bugs": "available",
            },
            "list_truncated": {},
        },
    )

    def fake_prefetch(_run_ctx, *, include_datasets):
        bundle = {name: pd.DataFrame() for name in include_datasets}
        bundle.update({
            "support_cases_snowflake": support_cases.copy(),
            "adoption_barriers": adoption_barriers.copy(),
            "ab_data": adoption_barriers.copy(),
            "csconsole_adoption_barriers": pd.DataFrame(),
            "csconsole_customer_pulse": pd.DataFrame(),
            "csconsole_success_priorities": pd.DataFrame(),
            "csconsole_action_plans": pd.DataFrame(),
            "enhanced_account_insights": enhanced,
        })
        return bundle

    captured: dict[str, str] = {}

    def fake_model(system_prompt, user_prompt, _schema, **_kwargs):
        pattern = re.compile(
            rf"^- \[SourceID: (?P<source_id>[^\]]+)\] "
            rf"\[{re.escape(source_type)}\] Customer: (?P<customer>.*?) \| "
            rf"Time: .*? \| (?P<text>.*)$",
            flags=re.MULTILINE,
        )
        match = pattern.search(user_prompt)
        assert match, user_prompt
        captured.update(match.groupdict())
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return {
            "ok": True,
            "data": {
                "executive_summary": "",
                "claims": [{
                    "statement": match.group("text"),
                    "citations": [match.group("source_id")],
                }],
                "actions": [],
                "unknowns": [],
            },
        }

    monkeypatch.setattr(grounded, "prefetch_ask_ai_grounded", fake_prefetch)
    monkeypatch.setattr(adoptiq_backend, "generate_llm_json_response", fake_model)
    result = grounded.run_portfolio_grounded_ask_ai(grounded.AskAIRequest(
        question=question,
        turn_question=question,
        manager="Manager One",
        technology="All",
        days=90,
    ))
    return result, captured


@pytest.mark.parametrize(
    ("question", "source_type", "required_text"),
    [
        (
            "Which contracts have renewal risk?",
            "RenewalOutlook",
            (
                "Snowflake source-provided renewal estimate: 35%",
                "not an AdoptIQ-calibrated probability",
                "Service end date: 2026-09-15",
            ),
        ),
        (
            "Which customer case or barrier is linked to CSCWA12345?",
            "BSTReference",
            (
                "Exact CSC correlation: CSCWA12345",
                "Parent record IDs: AB-ACME, CASE-ACME",
                "status=Open",
            ),
        ),
    ],
)
def test_real_portfolio_path_resolves_supplemental_evidence(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    source_type: str,
    required_text: tuple[str, ...],
) -> None:
    result, captured = _run_real_supplemental_evidence_question(
        monkeypatch,
        question=question,
        source_type=source_type,
    )

    assert result["ok"] is True
    assert captured["customer"] == "Acme"
    assert captured["source_id"] in captured["user_prompt"]
    assert f"[Sources: {captured['source_id']}]" in result["answer"]
    for expected in required_text:
        assert expected in captured["text"]
        assert expected in result["answer"]
    matching = [
        record for record in result["evidence_records"]
        if record["source_type"] == source_type
    ]
    assert matching
    assert matching[0]["source_id"] == captured["source_id"]


def test_portfolio_defect_identity_partial_warning_has_prompt_and_trust_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, captured = _run_real_supplemental_evidence_question(
        monkeypatch,
        question="Which customer case or barrier is linked to CSCWA12345?",
        source_type="BSTReference",
        include_unresolved_defect=True,
    )

    matching = [
        warning
        for warning in result["partial_data_warnings"]
        if warning.get("kind") == "identity_resolution_partial"
    ]
    assert matching == [{
        "dataset": "defect_correlations",
        "kind": "identity_resolution_partial",
        "source_state": "partial",
        "error": (
            "1 CSC-bearing source row(s) were retained in their source sheets "
            "but withheld from account-level defect correlations because no "
            "canonical customer identity was available."
        ),
    }]

    warning_block = captured["user_prompt"].split(
        "DATA_SOURCE_WARNINGS", 1
    )[1].split("SERVER_RESOLVED_CONTEXT", 1)[0]
    assert matching[0]["error"] in warning_block
    assert "CASE-UNRESOLVED" not in warning_block
    assert "CSCZZ99999" not in warning_block

    assert result["response_state"] == "partial"
    assert result["confidence"]["level"] != "High"
    assert result["retrieval_diag"]["response_state"] == result["response_state"]
    assert result["retrieval_diag"]["confidence"] == result["confidence"]


def test_defect_identity_warning_projects_no_raw_identity_diagnostics() -> None:
    warning = grounded._defect_identity_resolution_warning({  # noqa: SLF001
        "coverage": {
            "identity_resolution": {
                "state": "partial",
                "quarantined_observation_count": 2,
                "quarantined_by_source": {"Secret Source": 2},
                "quarantined_by_reason": {"Sensitive Tenant": 2},
                "raw_customer_aliases": ["Sensitive Tenant"],
            }
        }
    })

    assert warning is not None
    assert set(warning) == {"dataset", "kind", "source_state", "error"}
    assert "2 CSC-bearing source row(s)" in warning["error"]
    assert "Sensitive Tenant" not in repr(warning)
    assert "Secret Source" not in repr(warning)
