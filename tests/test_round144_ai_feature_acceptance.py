"""Round 144 live AI feature acceptance runner contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_ai_feature_acceptance as acceptance


def _portfolio_payload(answer: str, *, source_id: str = "AP-001") -> dict:
    return {
        "ok": True,
        "mode": "grounded",
        "answer": answer,
        "query_id": "valid-query_123",
        "retrieval_method": "hybrid",
        "model_name": "fixture-model",
        "evidence_index": [{"source_id": source_id, "source_type": "ActionPlan"}],
        "evidence_records": [{"source_id": source_id, "source_type": "ActionPlan"}],
        "canonical_headline": {"customers": 3, "open_action_plans": 1},
        "canonical_corrections": [],
        "canonical_verified": ["customers"],
        "partial_data_warnings": [],
        "evidence_truncated": False,
        "account_batch_truncated": False,
        "follow_up_suggestions": ["What changed?"],
    }


def test_extract_citations_handles_source_and_sources_forms() -> None:
    answer = "A [Source: AP-1]\nB [Sources: TAC-2, CP-3]"

    assert acceptance.extract_citations(answer) == ["AP-1", "CP-3", "TAC-2"]


def test_portfolio_validation_accepts_supported_grounded_answer() -> None:
    payload = _portfolio_payload("One open plan. [Sources: AP-001]")

    assert acceptance.validate_portfolio_payload(
        payload,
        require_citations=True,
        require_evidence_gap=False,
    ) == []


def test_portfolio_validation_rejects_unsupported_citation() -> None:
    payload = _portfolio_payload("Invented. [Sources: AP-FABRICATED]")

    errors = acceptance.validate_portfolio_payload(
        payload,
        require_citations=True,
        require_evidence_gap=False,
    )

    assert any("unsupported citation" in error for error in errors)


def test_unanswerable_case_requires_explicit_evidence_gap() -> None:
    payload = _portfolio_payload("The exact score is 9.7. [Sources: AP-001]")

    errors = acceptance.validate_portfolio_payload(
        payload,
        require_citations=False,
        require_evidence_gap=True,
    )

    assert any("did not disclose an evidence gap" in error for error in errors)


def test_disclosed_bounded_evidence_is_not_an_acceptance_failure() -> None:
    payload = _portfolio_payload("One open plan. [Sources: AP-001]")
    payload.update(
        evidence_truncated=True,
        evidence_records_used=200,
        evidence_records_total=277,
    )

    assert acceptance.validate_portfolio_payload(
        payload,
        require_citations=True,
        require_evidence_gap=False,
    ) == []


def test_truncation_without_coherent_counts_fails_acceptance() -> None:
    payload = _portfolio_payload("One open plan. [Sources: AP-001]")
    payload.update(
        evidence_truncated=True,
        evidence_records_used=200,
        evidence_records_total=200,
    )

    errors = acceptance.validate_portfolio_payload(
        payload,
        require_citations=True,
        require_evidence_gap=False,
    )

    assert any("truncation metadata" in error for error in errors)


def test_support_case_acceptance_requires_gap_when_source_is_absent() -> None:
    case = acceptance.QuestionCase(
        key="support_case_search_sync",
        route="portfolio_sync",
        question="Find matching support cases.",
    )
    payload = _portfolio_payload("### Evidence Gaps\n- No support-case records.")

    assert acceptance._portfolio_validation_requirements(  # noqa: SLF001
        case, payload
    ) == (False, True)
    payload["evidence_index"].append(
        {"source_id": "CASE-1", "source_type": "SupportCase"}
    )
    assert acceptance._portfolio_validation_requirements(  # noqa: SLF001
        case, payload
    ) == (True, False)


def test_support_case_gap_does_not_require_citation_lookup() -> None:
    case = acceptance.QuestionCase(
        key="support_case_search_sync",
        route="portfolio_sync",
        question="Find matching support cases.",
    )
    payload = _portfolio_payload(
        "### Evidence Gaps\n- Support-case evidence is absent for this scope."
    )

    class _Client:
        def post_json(self, _path, _payload, *, expensive=False):
            del expensive
            return 200, payload

        def get_json(self, path, *, params=None):
            del params
            assert path.startswith("/api/ask-ai/diagnostics/")
            return 200, {"ok": True}

    redacted, _sensitive = acceptance._run_pass(  # noqa: SLF001
        _Client(),
        pass_number=1,
        cases=(case,),
        manager="Fixture Manager",
        technology="All",
        days=90,
    )

    scenario = redacted["scenarios"]["support_case_search_sync"]
    assert scenario["ok"] is True
    assert scenario["evidence_lookup_ok"] is None
    assert scenario["diagnostics_ok"] is True


def test_unanswerable_gap_wording_matches_evidence_gap_regex() -> None:
    answer = (
        "### Evidence Gaps\n"
        "- The requested source and period are absent from the retrieved "
        "evidence; no estimate or substitute was used."
    )

    assert acceptance.EVIDENCE_GAP_RE.search(answer)
    errors = acceptance.validate_portfolio_payload(
        {
            "ok": True,
            "mode": "grounded",
            "answer": answer,
            "query_id": "abc123def456",
            "retrieval_method": "lexical",
            "model_name": "gemini-3.1-flash-lite",
            "evidence_index": [],
        },
        require_citations=False,
        require_evidence_gap=True,
    )

    assert not errors


def test_local_canonical_headline_reconciles_exact_fixture_oracle() -> None:
    payload = _portfolio_payload("One open plan. [Sources: AP-001]")
    payload["canonical_headline"] = {
        "total_customers": 3,
        "total_barriers": 2,
        "total_cases": 4,
    }
    expected = {"total_customers": 3, "total_barriers": 2, "total_cases": 4}

    assert acceptance.validate_canonical_headline(payload, expected) == []
    payload["canonical_headline"]["total_cases"] = 5
    errors = acceptance.validate_canonical_headline(payload, expected)
    assert errors == [
        "canonical headline total_cases=5 does not match fixture oracle"
    ]


def test_parse_sse_and_stream_payload_round_trip() -> None:
    raw = "\n\n".join(
        [
            'event: meta\ndata: {"query_id":"valid-query_123","retrieval_method":"hybrid",'
            '"model_name":"fixture-model","evidence_index":[{"source_id":"AP-001"}],'
            '"evidence_records":[{"source_id":"AP-001"}]}',
            'event: data\ndata: {"chunk":"One open plan. "}',
            'event: data\ndata: {"chunk":"[Sources: AP-001]"}',
            'event: done\ndata: {"follow_up_suggestions":["Why?"]}',
        ]
    )

    events = acceptance.parse_sse(raw)
    payload = acceptance._stream_payload(events)  # noqa: SLF001 - runner seam

    assert [name for name, _ in events] == ["meta", "data", "data", "done"]
    assert payload["ok"] is True
    assert payload["answer"] == "One open plan. [Sources: AP-001]"
    assert payload["follow_up_suggestions"] == ["Why?"]


def test_parse_sse_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="invalid SSE JSON"):
        acceptance.parse_sse("event: meta\ndata: {not-json}\n\n")


def test_scenario_redaction_does_not_embed_answer_or_source_id() -> None:
    case = acceptance.QuestionCase(
        key="fixture",
        route="portfolio_sync",
        question="fixture question",
    )
    payload = _portfolio_payload(
        "Sensitive Customer has $1,250 at risk. [Sources: SECRET-AP-001]",
        source_id="SECRET-AP-001",
    )

    redacted = acceptance._scenario_redaction(  # noqa: SLF001 - contract seam
        case=case,
        payload=payload,
        errors=[],
        duration_ms=1,
        evidence_lookup_ok=True,
        diagnostics_ok=True,
    )
    serialized = json.dumps(redacted, sort_keys=True)

    assert "Sensitive Customer" not in serialized
    assert "SECRET-AP-001" not in serialized
    assert "1,250" not in serialized
    assert "canonical_headline_numeric" not in redacted
    assert redacted["citation_count"] == 1
    assert redacted["evidence_id_count"] == 1
    assert redacted["fact_token_count"] == 1


def test_compare_passes_accepts_provider_variance_but_rejects_evidence_drift() -> None:
    stable = {
        "scenario": "fixture",
        "evidence_id_hashes": ["evidence-hash"],
        "evidence_type_counts": {"ActionPlan": 1},
        "canonical_headline_sha256": "canonical-hash",
    }
    passes = [
        {"scenarios": {"fixture": dict(stable)}},
        {"scenarios": {"fixture": dict(stable)}},
    ]

    assert acceptance.compare_passes(passes)["ok"] is True
    passes[1]["scenarios"]["fixture"]["fact_token_hashes"] = ["provider-variance"]
    assert acceptance.compare_passes(passes)["ok"] is True
    passes[1]["scenarios"]["fixture"]["evidence_id_hashes"] = ["changed-hash"]
    result = acceptance.compare_passes(passes)
    assert result["ok"] is False
    assert "evidence_id_hashes" in result["scenarios"]["fixture"]["drift_fields"]


def test_conversation_repeatability_allows_ranked_row_swap_only() -> None:
    stable = {
        "scenario": "conversation_follow_up_sync",
        "evidence_id_count": 191,
        "evidence_id_hashes": ["first-ranked-set"],
        "evidence_type_counts": {"ActionPlan": 120, "CanonicalMetric": 15},
        "canonical_headline_sha256": "canonical-hash",
    }
    passes = [
        {"scenarios": {"conversation_follow_up_sync": dict(stable)}},
        {
            "scenarios": {
                "conversation_follow_up_sync": {
                    **stable,
                    "evidence_id_hashes": ["second-ranked-set"],
                    "evidence_type_counts": {"ActionPlan": 118, "CanonicalMetric": 17},
                    "evidence_id_count": 190,
                }
            }
        },
    ]

    assert acceptance.compare_passes(passes)["ok"] is True
    passes[1]["scenarios"]["conversation_follow_up_sync"]["canonical_headline_sha256"] = (
        "changed-headline"
    )
    result = acceptance.compare_passes(passes)
    assert result["ok"] is False
    assert result["scenarios"]["conversation_follow_up_sync"]["drift_fields"] == [
        "canonical_headline_sha256"
    ]


def test_sync_stream_delivery_requires_identical_canonical_evidence() -> None:
    stable = {
        "evidence_id_hashes": ["evidence"],
        "evidence_type_counts": {"ActionPlan": 1},
        "canonical_headline_sha256": "headline",
    }
    scenarios = {
        "delivery_parity_sync": dict(stable),
        "delivery_parity_stream": dict(stable),
    }

    assert acceptance.compare_sync_stream_delivery(scenarios)["ok"] is True
    scenarios["delivery_parity_stream"]["citation_id_hashes"] = ["provider-variance"]
    assert acceptance.compare_sync_stream_delivery(scenarios)["ok"] is True
    scenarios["delivery_parity_stream"]["evidence_id_hashes"] = ["changed"]
    result = acceptance.compare_sync_stream_delivery(scenarios)
    assert result["ok"] is False
    assert result["drift_fields"] == ["evidence_id_hashes"]


def test_corpus_feature_page_requires_result_markers_without_alerts() -> None:
    body = """
    <html><body>
      <h1>Customer 360</h1><h2>Fixture Customer</h2>
      <h2>Cases timeline</h2>
      <p>First seen: 2026-01-01. Last seen: 2026-08-01. Prior occurrences: 4.</p>
      <p>Representative corpus-backed case history and resolution context.</p>
    </body></html>
    """

    result = acceptance._corpus_feature_page_result(  # noqa: SLF001
        200,
        "text/html; charset=utf-8",
        body,
        required_markers=("Customer 360", "Fixture Customer", "Cases timeline"),
    )

    assert result["ok"] is True
    assert result["alert_count"] == 0
    assert result["required_marker_count"] == 3
    assert result["required_markers_found"] == 3
    assert result["missing_marker_sha256s"] == []


def test_corpus_feature_page_rejects_http_200_degraded_banner() -> None:
    body = """
    <html><body>
      <h1>Customer 360</h1><h2>Fixture Customer</h2>
      <h2>Cases timeline</h2>
      <div class="alert alert-warning">Customer history is unavailable.</div>
    </body></html>
    """

    result = acceptance._corpus_feature_page_result(  # noqa: SLF001
        200,
        "text/html",
        body,
        required_markers=("Customer 360", "Fixture Customer", "Cases timeline"),
    )

    assert result["ok"] is False
    assert result["alert_count"] == 1
    assert "Customer history is unavailable" not in json.dumps(result)


def test_corpus_feature_page_ignores_hidden_global_status_templates() -> None:
    body = """
    <html><body>
      <div class="alert alert-warning" hidden>Restart required.</div>
      <div class="alert alert-info" aria-hidden="true">Update available.</div>
      <h1>Customer 360</h1><h2>Fixture Customer</h2>
      <h2>Cases timeline</h2><p>Verified corpus evidence.</p>
    </body></html>
    """

    result = acceptance._corpus_feature_page_result(  # noqa: SLF001
        200,
        "text/html",
        body,
        required_markers=("Customer 360", "Fixture Customer", "Cases timeline"),
    )

    assert result["ok"] is True
    assert result["alert_count"] == 0


def test_corpus_feature_page_rejects_form_only_playbook_page() -> None:
    result = acceptance._corpus_feature_page_result(  # noqa: SLF001
        200,
        "text/html",
        "<html><body><h1>Troubleshooting Playbook</h1></body></html>",
        required_markers=("Troubleshooting Playbook", "Search matches for:"),
    )

    assert result["ok"] is False
    assert result["required_marker_count"] == 2
    assert result["required_markers_found"] == 1
    assert len(result["missing_marker_sha256s"]) == 1


def test_failed_bootstrap_cannot_claim_live_validation(tmp_path, monkeypatch) -> None:
    def fail_bootstrap(_self) -> None:
        raise RuntimeError("fixture bootstrap failure")

    monkeypatch.setattr(acceptance.AiFeatureClient, "bootstrap", fail_bootstrap)
    output_dir = tmp_path / "acceptance"

    exit_code = acceptance.main(
        [
            "--manager",
            "Fixture Manager",
            "--customer-name",
            "Fixture Customer",
            "--output-dir",
            str(output_dir),
            "--pace-seconds",
            "0",
        ]
    )
    summary = json.loads(
        (output_dir / "ai_feature_acceptance_summary.json").read_text(encoding="utf-8")
    )

    assert exit_code == 5
    assert summary["live_validation_attempted"] is False
    assert summary["live_validation_performed"] is False
    assert summary["live_validation_passed"] is False
    assert summary["all_automated_checks_passed"] is False
    assert summary["do_not_commit"] is True
    assert "fixture bootstrap failure" not in json.dumps(summary)


@pytest.mark.parametrize(
    "base_url",
    ["https://example.com", "http://10.0.0.4:5151", "file:///tmp/app"],
)
def test_client_rejects_non_loopback_targets(base_url: str) -> None:
    with pytest.raises(ValueError):
        acceptance.AiFeatureClient(
            base_url=base_url,
            request_timeout=10,
            pace_seconds=0,
            max_rate_retries=0,
        )


def test_fixed_question_set_covers_required_delivery_paths() -> None:
    cases = acceptance.build_question_cases("Fixture Customer")

    assert len(cases) == 11
    assert {case.route for case in cases} == {
        "portfolio_sync",
        "portfolio_stream",
        "ask_intel",
    }
    assert any(case.use_conversation_history for case in cases)
    assert any(case.require_evidence_gap for case in cases)
    assert any(case.forbidden_answer_terms for case in cases)
    assert {
        case.route
        for case in cases
        if case.key.startswith("delivery_parity_")
    } == {"portfolio_sync", "portfolio_stream"}


def test_makefile_and_work_machine_runbook_wire_live_ai_acceptance() -> None:
    root = Path(__file__).resolve().parents[1]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    runbook = (root / "WORK_MACHINE_ROLLOUT.md").read_text(encoding="utf-8")

    assert "ai-feature-acceptance:" in makefile
    assert "scripts/run_ai_feature_acceptance.py" in makefile
    assert "make ai-feature-acceptance" in runbook
    assert "all_automated_checks_passed: true" in runbook
    assert "permission-restricted sensitive evidence" in runbook
    assert "do not mislabel it as external incident/bug state" in runbook
