"""Round 146 exact-source citation contracts for report-bound Ask AI."""

from __future__ import annotations

import pandas as pd

import ask_ai_grounded as grounded


def test_report_context_whitelist_excludes_ids_only_mentioned_inside_rows() -> None:
    records = [
        grounded.EvidenceRecord(
            "AdoptionBarrier",
            "AB-001",
            "Acme",
            "2026-08-01",
            "Escalation text mentions CASE-OUTSIDE but this is the barrier row.",
        )
    ]

    context, legacy_ids, used, _ranked = (
        grounded.build_evidence_context_with_ranking(
            records,
            "Which decision comes first?",
            ("core",),
        )
    )

    assert used == 1
    assert legacy_ids == {"AB-001", "CASE-OUTSIDE"}
    assert grounded._r146_context_source_ids(context) == {"AB-001"}


def test_report_citation_contract_rewrites_to_exact_resolvable_source_id() -> None:
    answer, contract = grounded._r146_report_bound_citation_contract(
        "### Supported Findings\n- Review this record. [Sources: AP-001]",
        [
            {
                "source_id": "ap-001",
                "source_type": "ActionPlan",
                "text": "Owner decision is due.",
            }
        ],
        report_analysis_id="leader-report-146",
        fact_fingerprint="sha256:bound-facts",
    )

    assert answer.endswith("[Sources: ap-001]")
    assert contract == {
        "mode": "report_bound",
        "required": True,
        "fact_fingerprint_bound": True,
        "all_citations_resolved": True,
        "citation_count": 1,
        "reason": "",
    }


def test_report_citation_contract_replaces_uncited_or_unresolved_claims() -> None:
    evidence = [{"source_id": "AP-001", "text": "Exact evidence row"}]

    for unsafe_answer, expected_reason in (
        ("Make the owner decision now.", "no_exact_source_citation"),
        (
            "Make the owner decision now. [Sources: AP-NOT-IN-REPORT]",
            "unresolved_source_citation",
        ),
    ):
        answer, contract = grounded._r146_report_bound_citation_contract(
            unsafe_answer,
            evidence,
            report_analysis_id="leader-report-146",
            fact_fingerprint="sha256:bound-facts",
        )

        assert answer.startswith("Insufficient report-bound evidence")
        assert "No decision claim is presented" in answer
        assert "[Sources:" not in answer
        assert contract["all_citations_resolved"] is False
        assert contract["citation_count"] == 0
        assert contract["reason"] == expected_reason


def test_report_citation_contract_requires_server_fact_fingerprint() -> None:
    answer, contract = grounded._r146_report_bound_citation_contract(
        "Review it. [Sources: AP-001]",
        [{"source_id": "AP-001"}],
        report_analysis_id="leader-report-146",
        fact_fingerprint="",
    )

    assert answer.startswith("Insufficient report-bound evidence")
    assert contract["fact_fingerprint_bound"] is False
    assert contract["reason"] == "missing_report_fact_binding"


def test_all_contact_center_report_scope_uses_report_family_semantics() -> None:
    subscriptions = pd.DataFrame(
        [
            {
                "SUBSCRIPTION_ID": "SUB-WXCC",
                "TECHNOLOGY_C": "Webex Contact Center",
            },
            {
                "SUBSCRIPTION_ID": "SUB-UCCE",
                "TECHNOLOGY_C": "Cisco UCCE",
            },
            {
                "SUBSCRIPTION_ID": "SUB-MEETINGS",
                "TECHNOLOGY_C": "Webex Meetings",
            },
        ]
    )

    scoped = grounded._r146_filter_report_bound_technology(
        subscriptions,
        "All Contact Center",
    )

    assert scoped["SUBSCRIPTION_ID"].tolist() == ["SUB-WXCC", "SUB-UCCE"]
