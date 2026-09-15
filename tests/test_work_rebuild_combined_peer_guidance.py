"""Regression coverage for the composed Round 178-182 peer-guidance tree."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import corpus_retriever as cr
import report_corpus_context as rcc


def _evidence(**overrides: object) -> cr.PeerGuidanceEvidence:
    values: dict[str, object] = {
        "theme": "authentication",
        "technology": "security",
        "peer_customer_count": 2,
        "resolved_peer_count": 2,
        "dominant_method_text": "Stage the configuration and verify telemetry.",
        "dominant_method_peers": 2,
        "closed_peer_count": 2,
        "open_peer_count": 0,
        "close_time_median_days": 2.0,
        "pulse_recovered_count": 0,
        "pulse_worsened_count": 0,
        "likely_next": "closure",
        "next_step": "Test the peer-observed method, then verify closure.",
        "evidence_sufficient": True,
        "method_closed_peer_count": 2,
        "likely_next_basis": "case",
        "target_path": "not_tried",
        "comparable_severity_band": "critical",
        "comparable_method_peer_count": 2,
        "comparable_case_severity_band": "high",
        "comparable_case_method_peer_count": 2,
    }
    values.update(overrides)
    return cr.PeerGuidanceEvidence(**values)


def test_receipt_fingerprints_both_independent_severity_dimensions() -> None:
    baseline = _evidence()
    barrier_changed = replace(baseline, comparable_severity_band="low")
    case_changed = replace(baseline, comparable_case_severity_band="critical")

    receipts = {
        rcc.peer_guidance_source_id(item)
        for item in (baseline, barrier_changed, case_changed)
    }

    assert len(receipts) == 3
    assert all(receipt.startswith("CORPUS:PG-") for receipt in receipts)


def test_already_lived_is_stronger_than_incomparable_peer_paths() -> None:
    evidence = _evidence(
        likely_next="insufficient",
        next_step="",
        target_path="already_closed",
        incomparable_severity=True,
        incomparable_case_severity=True,
    )

    view = rcc.build_peer_guidance_view(evidence)

    assert view["status"] == "method_only"
    assert view["insufficient_reason"] == "already_lived"
    assert view["next_step"] == ""
    assert view["ready_for_live_cisco"] is False


def test_theme_filter_preserves_barrier_severity_argument(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def load(
        customer: str,
        theme: str,
        technology: str,
        *,
        target_severity: object = None,
    ) -> cr.PeerGuidanceEvidence:
        calls.append((theme, target_severity))
        return _evidence(theme=theme, technology=technology)

    monkeypatch.setattr(rcc, "_r175_load_peer_evidence", load)
    barriers = [
        SimpleNamespace(
            theme="authentication",
            technology="security",
            occurrences=3,
            severity="Critical",
        ),
        SimpleNamespace(
            theme="performance",
            technology="security",
            occurrences=5,
            severity="Low",
        ),
    ]

    selected = rcc.select_ranked_peer_guidance(
        customer="Synthetic Target",
        barriers=barriers,
        prefer_theme="performance",
    )

    assert selected is not None
    assert selected.theme == "performance"
    assert calls == [("performance", "Low")]
