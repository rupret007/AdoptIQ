"""Focused Decision Intelligence V2 coverage for the Leader report seam."""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
import sys
import types
from unittest import mock

import pandas as pd

import decision_intelligence
from decision_intelligence_adapters import (
    project_canonical_portfolio_metrics,
    project_legacy_risk_profiles,
    validate_cross_output_reconciliation,
)

# The focused offline matrix intentionally does not install live connector
# clients.  Import-only stubs keep this formatter test hermetic; no connector
# method is exercised because every fetcher is replaced below.
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

from leader_report_generator import LeaderReportGenerator, LeaderTeamData


def _configured_generator(monkeypatch, tmp_path) -> LeaderReportGenerator:
    roster = [
        ("Director", "Alice", "alice@example.com"),
        ("Director", "Bob", "bob@example.com"),
    ]
    generator = LeaderReportGenerator(
        mock.MagicMock(),
        roster,
        data_retrieved_at=datetime(2026, 7, 13, 12, tzinfo=timezone.utc),
    )
    generator._decision_intelligence_manager_name = "Director"
    generator._decision_intelligence_external_incidents = []
    generator._decision_intelligence_snapshot_root = tmp_path / "snapshots"
    generator._decision_intelligence_support_cases = pd.DataFrame(
        [
            {
                "SR Number": "SR-1",
                "Customer Name": "Acme Corp",
                "Severity": "P1",
                "Date/Time Opened": "2026-07-12T10:00:00Z",
            },
            {
                "SR Number": "SR-OLD",
                "Customer Name": "Acme Corp",
                "Severity": "P2",
                "Date/Time Opened": "2025-01-01T10:00:00Z",
            },
        ]
    )

    subscriptions = pd.DataFrame(
        [
            {
                "SUBSCRIPTION_ID": "SUB-1",
                "ACCOUNT_ID_C": "ACC-1",
                "BU_NAME": "Acme Corp",
                "CSSM_EMAIL": "alice@example.com",
            },
            {
                "SUBSCRIPTION_ID": "SUB-2",
                "ACCOUNT_ID_C": "ACC-2",
                "BU_NAME": "Beta Inc",
                "CSSM_EMAIL": "bob@example.com",
            },
        ]
    )
    action_plans = pd.DataFrame(
        [
            {
                "ID": "AP-1",
                "ACCOUNT_ID_C": "ACC-1",
                "BU_NAME": "Acme Corp",
                "STATUS_C": "In Progress",
                "OWNER_EMAIL": "alice@example.com",
            },
            {
                "ID": "AP-2",
                "ACCOUNT_ID_C": "ACC-2",
                "BU_NAME": "Beta Inc",
                "STATUS_C": "Open",
                "OWNER_EMAIL": "bob@example.com",
            },
        ]
    )
    barriers = pd.DataFrame(
        [
            {
                "ID": "AB-1",
                "ACCOUNT_ID_C": "ACC-1",
                "BU_NAME": "Acme Corp",
                "SEVERITY_C": "Critical",
                "AB_STATUS_C": "Open",
                "OWNER_EMAIL": "alice@example.com",
            }
        ]
    )
    pulse = pd.DataFrame(
        [
            {
                "ID": "CP-1",
                "ACCOUNT__C": "ACC-1",
                "BU_NAME": "Acme Corp",
                "SCORE__C": 4.0,
                "OWNER_EMAIL": "alice@example.com",
            },
            {
                "ID": "CP-2",
                "ACCOUNT__C": "ACC-2",
                "BU_NAME": "Beta Inc",
                "SCORE__C": 8.5,
                "OWNER_EMAIL": "bob@example.com",
            },
        ]
    )
    priorities = pd.DataFrame(
        [
            {
                "ID": "SP-1",
                "RELATED_CUSTOMER__C": "Acme Corp",
                "STATUS_C": "Open",
            }
        ]
    )
    monkeypatch.setattr(
        generator, "_get_subscriptions_for_cssm", lambda _emails: subscriptions.copy()
    )
    monkeypatch.setattr(
        generator,
        "_fetch_action_plans",
        lambda _accounts, _days, owner_emails=None: action_plans.copy(),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_adoption_barriers",
        lambda _accounts, _days, owner_emails=None: barriers.copy(),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_customer_pulse",
        lambda _accounts, _days, owner_emails=None: pulse.copy(),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_success_priorities",
        lambda _customers, _days: priorities.copy(),
    )
    return generator


def _direct_reports():
    return [
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "Bob", "email": "bob@example.com"},
    ]


def test_leader_builds_once_before_slicing_and_exports_adapter_parity(
    monkeypatch,
    tmp_path,
) -> None:
    generator = _configured_generator(monkeypatch, tmp_path)
    original_build = decision_intelligence.build_analysis_bundle
    calls = []

    def _spy_build(request, sources, **kwargs):
        calls.append(
            {
                "request": request,
                "action_plan_rows": len(sources.action_plans),
                "support_case_rows": len(sources.support_cases),
            }
        )
        return original_build(request, sources, **kwargs)

    monkeypatch.setattr(decision_intelligence, "build_analysis_bundle", _spy_build)

    team_data = generator._collect_team_data(_direct_reports(), days=90)

    assert isinstance(team_data, LeaderTeamData)
    assert set(team_data) == {"Alice", "Bob"}
    assert len(calls) == 1
    assert calls[0]["action_plan_rows"] == 2
    assert calls[0]["support_case_rows"] == 1
    assert calls[0]["request"].portfolio_scope == "leader:Director"
    assert calls[0]["request"].team_scope == (
        "alice@example.com",
        "bob@example.com",
    )

    bundle = team_data.decision_intelligence_bundle
    assert bundle is generator._decision_intelligence_bundle
    assert bundle.reconciliation_errors() == []
    assert team_data.decision_intelligence_risk_profiles == (
        project_legacy_risk_profiles(bundle)
    )
    assert team_data.decision_intelligence_portfolio_metrics == (
        project_canonical_portfolio_metrics(bundle)
    )
    assert team_data.decision_intelligence_metadata["analysis_fingerprint"] == (
        bundle.analysis_fingerprint
    )
    assert team_data.decision_intelligence_metadata["snapshot_path"]
    assert set(team_data.decision_intelligence_excel_frames) >= {
        "Customer_Decision_Briefs",
        "Portfolio_Decision_Brief",
    }
    for sheet_name in ("Customer_Decision_Briefs", "Portfolio_Decision_Brief"):
        manifest = team_data.decision_intelligence_excel_frames[sheet_name].attrs[
            "_decision_intelligence"
        ]
        assert manifest["analysis_fingerprint"] == bundle.analysis_fingerprint


def test_word_decision_brief_reuses_bundle_without_second_build(
    monkeypatch,
    tmp_path,
) -> None:
    generator = _configured_generator(monkeypatch, tmp_path)
    original_build = decision_intelligence.build_analysis_bundle
    build_count = 0

    def _spy_build(*args, **kwargs):
        nonlocal build_count
        build_count += 1
        return original_build(*args, **kwargs)

    monkeypatch.setattr(decision_intelligence, "build_analysis_bundle", _spy_build)
    generator._collect_team_data(_direct_reports(), days=90)
    generator._add_decision_intelligence_word()

    assert build_count == 1
    text = "\n".join(paragraph.text for paragraph in generator.doc.paragraphs)
    assert "Portfolio Decision Brief" in text
    assert "What changed" in text
    projection = generator._decision_intelligence_word_projection
    assert projection["_decision_intelligence"]["analysis_fingerprint"] == (
        generator._decision_intelligence_bundle.analysis_fingerprint
    )
    reconciliation = validate_cross_output_reconciliation(
        generator._decision_intelligence_bundle,
        report=projection,
        export=generator._decision_intelligence_excel_frames,
        require_all=False,
    )
    assert reconciliation.ok, reconciliation.errors


def test_leader_v2_failure_is_visible_and_legacy_mapping_survives(
    monkeypatch,
    tmp_path,
) -> None:
    generator = _configured_generator(monkeypatch, tmp_path)

    def _fail_build(*_args, **_kwargs):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(decision_intelligence, "build_analysis_bundle", _fail_build)
    team_data = generator._collect_team_data(_direct_reports(), days=90)
    generator._add_decision_intelligence_word()

    assert set(team_data) == {"Alice", "Bob"}
    assert team_data.decision_intelligence_bundle is None
    assert team_data.decision_intelligence_warnings[0]["kind"] == (
        "analysis_unavailable"
    )
    text = "\n".join(paragraph.text for paragraph in generator.doc.paragraphs)
    assert "Decision Brief — Unavailable" in text
    assert "legacy activity sections" in text


def test_leader_v2_callsite_precedes_member_slicing() -> None:
    source = inspect.getsource(LeaderReportGenerator._collect_team_data)
    build_position = source.index("_build_request_scoped_decision_intelligence(")
    slicing_position = source.index("for idx, report in enumerate(direct_reports):")
    assert build_position < slicing_position
