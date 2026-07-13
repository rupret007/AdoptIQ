"""Focused Decision Intelligence V2 coverage for the shared WxCC exporter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
import sys
import types
from unittest import mock

import pandas as pd

# The focused suite is offline.  The exporter imports connector clients via
# adoptiq_backend, but none of these tests performs connector I/O.
if "hvac" not in sys.modules:
    hvac_stub = types.ModuleType("hvac")
    hvac_stub.Client = mock.MagicMock
    sys.modules["hvac"] = hvac_stub
if "snowflake.connector" not in sys.modules:
    snowflake_stub = types.ModuleType("snowflake")
    snowflake_stub.__path__ = []
    connector_stub = types.ModuleType("snowflake.connector")
    connector_stub.DictCursor = object()
    connector_stub.connect = mock.MagicMock()
    snowflake_stub.connector = connector_stub
    sys.modules["snowflake"] = snowflake_stub
    sys.modules["snowflake.connector"] = connector_stub
if "feedparser" not in sys.modules:
    feedparser_stub = types.ModuleType("feedparser")
    feedparser_stub.parse = mock.MagicMock(return_value={})
    sys.modules["feedparser"] = feedparser_stub

import canonical_metrics as cm
import decision_intelligence
import wxcc_health_input_exporter as exporter


def _scope() -> exporter.CustomerScope:
    subscriptions = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme Corp",
                "ACCOUNT_ID_C": "ACC-1",
                "SUBSCRIPTION_ID": "SUB-1",
                "TECHNOLOGY_C": "Webex Contact Center",
                "STATUS_C": "Active",
                "RENEWAL_RISK_CATEGORY": "High",
            }
        ]
    )
    return exporter.CustomerScope(
        canonical_name="Acme Corp",
        customer_query="Acme",
        subscription_id="SUB-1",
        days=90,
        team_subs_df=subscriptions,
        account_ids=["ACC-1"],
    )


def _source_frames() -> dict[str, object]:
    csone = pd.DataFrame(
        [
            {
                "Customer Name": "Acme Corp",
                "Case #": "CS-1",
                "Severity": "P1",
                "Status": "Open",
            }
        ]
    )
    snowflake = pd.DataFrame(
        [
            {
                "Customer Name": "Acme Corp",
                "Case #": "SF-2",
                "Severity": "P2",
                "Status": "Open",
            }
        ]
    )
    return {
        "adoption_barriers": pd.DataFrame(
            [
                {
                    "BU_NAME": "Acme Corp",
                    "ACCOUNT_ID_C": "ACC-1",
                    "ID": "AB-1",
                    "SEVERITY_C": "Critical",
                    "AB_STATUS_C": "Open",
                }
            ]
        ),
        "support_cases": exporter._combine_tac_frames(csone, snowflake),
        "customer_pulse": pd.DataFrame(
            [
                {
                    "BU_NAME": "Acme Corp",
                    "ID": "PULSE-1",
                    "PULSE_RATING__C": "Red",
                }
            ]
        ),
        "action_plans": pd.DataFrame(
            [
                {
                    "BU_NAME": "Acme Corp",
                    "ID": "AP-1",
                    "STATUS_C": "Open",
                }
            ]
        ),
        "subscriptions": _scope().team_subs_df.copy(),
        "external_incidents": [],
    }


def _context(
    *,
    warnings: list[dict[str, object]] | None = None,
    metadata: dict[str, str] | None = None,
) -> exporter.WxccHealthContext:
    scope = _scope()
    frames = _source_frames()
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    return exporter.WxccHealthContext(
        scope=scope,
        technology="Webex Contact Center",
        days=90,
        period_start=now - timedelta(days=90),
        period_end=now,
        ab_df=frames["adoption_barriers"],
        csone_df=pd.DataFrame(),
        tac_df=frames["support_cases"],
        action_plans_df=frames["action_plans"],
        pulse_df=frames["customer_pulse"],
        subs_slice=frames["subscriptions"],
        ext_incidents=[],
        matched_bugs=[],
        risk_profile={"risk_score_0_100": 72.0, "risk_band": "HIGH"},
        partial_data_warnings=list(warnings or []),
        source_diagnostics=[],
        decision_intelligence_metadata=dict(metadata or {}),
    )


def test_support_sources_are_combined_before_analysis() -> None:
    frames = _source_frames()
    support_cases = frames["support_cases"]

    assert set(support_cases["Case #"]) == {"CS-1", "SF-2"}
    assert len(support_cases) == 2
    assert support_cases.attrs["source_frame_count"] == 2


def test_marked_portfolio_slice_cannot_skip_cross_source_case_dedup() -> None:
    portfolio = cm.deduplicate_tac_cases(
        pd.DataFrame(
            [
                {"Case #": "A", "BU_NAME": "Alpha", "Status": "Open"},
                {"Case #": "B", "BU_NAME": "Beta", "Status": "Open"},
                {"Case #": "C", "BU_NAME": "Gamma", "Status": "Open"},
            ]
        )
    )
    alpha_slice = portfolio.iloc[[0]].copy()
    snowflake_overlap = pd.DataFrame(
        [{"Case #": "A", "BU_NAME": "Alpha", "Status": "Open"}]
    )

    combined = exporter._combine_tac_frames(alpha_slice, snowflake_overlap)

    assert combined["Case #"].tolist() == ["A"]
    assert combined.attrs["source_frame_count"] == 2
    assert combined.attrs["tac_dedup"]["logical_cases"] == 1


def test_shared_seam_builds_once_uses_prior_snapshot_and_never_calls_legacy(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ADOPTIQ_ANALYSIS_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    original_build = decision_intelligence.build_analysis_bundle
    calls = []

    def _spy_build(request, sources, **kwargs):
        calls.append((request, sources, kwargs))
        return original_build(request, sources, **kwargs)

    def _legacy_must_not_run(**_kwargs):
        raise AssertionError("legacy risk scorer ran on canonical success")

    monkeypatch.setattr(decision_intelligence, "build_analysis_bundle", _spy_build)
    monkeypatch.setattr(exporter, "compute_customer_risk_profile", _legacy_must_not_run)

    start = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    frames = _source_frames()
    first = exporter._build_wxcc_decision_intelligence(
        scope=_scope(),
        technology="Webex Contact Center",
        period_start=start - timedelta(days=90),
        period_end=start,
        **frames,
    )
    second = exporter._build_wxcc_decision_intelligence(
        scope=_scope(),
        technology="Webex Contact Center",
        period_start=start - timedelta(days=89),
        period_end=start + timedelta(days=1),
        **frames,
    )

    assert len(calls) == 2, "each export request must build exactly once"
    assert calls[0][2]["prior_bundle"] is None
    assert calls[1][2]["prior_bundle"] is not None
    assert len(calls[0][1].support_cases) == 2
    assert first.metadata["decision_intelligence_v2_status"] == "canonical"
    assert second.metadata["decision_intelligence_v2_status"] == "canonical"
    assert first.metadata["analysis_schema_version"]
    assert first.metadata["analysis_fingerprint"]
    assert first.metadata["analysis_request_fingerprint"]
    assert first.metadata["analysis_comparison_scope_fingerprint"]
    assert Path(first.metadata["analysis_snapshot_path"]).is_file()
    assert Path(second.metadata["analysis_snapshot_path"]).is_file()
    assert first.warnings == ()
    assert second.warnings == ()
    assert first.risk_profile is not None


def test_canonical_failure_is_visible_and_only_then_calls_legacy(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ADOPTIQ_ANALYSIS_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    build_calls = 0
    legacy_calls = 0

    def _fail_build(*_args, **_kwargs):
        nonlocal build_calls
        build_calls += 1
        raise RuntimeError("synthetic canonical failure")

    def _legacy(**kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        assert len(kwargs["customer_csone"]) == 2
        return {"risk_score_0_100": 51.0, "risk_band": "MEDIUM"}

    monkeypatch.setattr(decision_intelligence, "build_analysis_bundle", _fail_build)
    monkeypatch.setattr(exporter, "compute_customer_risk_profile", _legacy)
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    outcome = exporter._build_wxcc_decision_intelligence(
        scope=_scope(),
        technology="Webex Contact Center",
        period_start=now - timedelta(days=90),
        period_end=now,
        **_source_frames(),
    )

    assert build_calls == 1
    assert legacy_calls == 1
    assert outcome.metadata["decision_intelligence_v2_status"] == "legacy_fallback"
    assert outcome.risk_profile == {"risk_score_0_100": 51.0, "risk_band": "MEDIUM"}
    assert outcome.warnings[0]["kind"] == "decision_intelligence_v2_failed"
    assert "legacy deterministic risk scoring was used" in outcome.warnings[0]["message"]

    text = exporter.build_wxcc_health_text(_context(warnings=list(outcome.warnings), metadata=outcome.metadata))
    assert "Partial data (decision_intelligence_v2_failed)" in text
    assert "legacy deterministic risk scoring was used" in text


def test_export_result_carries_metadata_without_changing_txt(
    monkeypatch,
    tmp_path,
) -> None:
    metadata = {
        "decision_intelligence_v2_status": "canonical",
        "analysis_schema_version": "2.0.0",
        "analysis_fingerprint": "analysis:abc",
        "analysis_request_fingerprint": "request:def",
        "analysis_comparison_scope_fingerprint": "scope:ghi",
        "analysis_snapshot_path": str(tmp_path / "snapshot.json"),
    }
    context = _context(metadata=metadata)
    expected_text = exporter.build_wxcc_health_text(context)
    monkeypatch.setattr(exporter, "resolve_customer_scope", lambda **_kwargs: context.scope)
    monkeypatch.setattr(
        exporter,
        "fetch_customer_datasets",
        lambda *_args, **_kwargs: context,
    )

    target = tmp_path / "wxcc.txt"
    result = exporter.export_wxcc_health_input(customer="Acme", output_path=target)

    assert result.decision_intelligence_metadata == metadata
    assert result.text == expected_text
    assert target.read_text(encoding="utf-8") == expected_text
    changed_metadata = _context(metadata={"analysis_fingerprint": "analysis:other"})
    assert exporter.build_wxcc_health_text(changed_metadata) == expected_text


def test_fetch_boundary_has_one_v2_call_and_no_inline_legacy_recalculation() -> None:
    source = inspect.getsource(exporter.fetch_customer_datasets)

    assert source.count("_build_wxcc_decision_intelligence(") == 1
    assert "compute_customer_risk_profile(" not in source


def test_subscription_scope_reuses_fetch_and_retains_contract_evidence(monkeypatch) -> None:
    calls = 0

    def _fetch(subscription_id, days):
        nonlocal calls
        calls += 1
        assert subscription_id == "SUB-1"
        assert days == 90
        return {
            "found": True,
            "customer_name": "Acme Corp",
            "account_id": "ACC-1",
            "cssm_email": "owner@example.invalid",
            "technology": "Webex Contact Center",
            "sub_technology": "WxCC",
            "status": "Active",
            "renewal_risk_category": "High",
        }

    monkeypatch.setattr(exporter, "fetch_subscription_data", _fetch)
    scope = exporter.resolve_customer_scope(subscription_id="SUB-1", days=90)

    assert calls == 1
    row = scope.team_subs_df.iloc[0]
    assert row["TECHNOLOGY_C"] == "Webex Contact Center"
    assert row["STATUS_C"] == "Active"
    assert row["RENEWAL_RISK_CATEGORY"] == "High"
