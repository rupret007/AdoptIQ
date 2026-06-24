"""Round 134 — WxCC health input exporter (deterministic, LLM-free)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sample_context(**overrides):
    from wxcc_health_input_exporter import (
        CustomerScope,
        SourceDiagnostic,
        WxccHealthContext,
    )

    scope = CustomerScope(
        canonical_name="ACME CORP",
        customer_query="ACME",
        subscription_id=None,
        days=90,
        team_subs_df=pd.DataFrame({"BU_NAME": ["ACME CORP"], "ACCOUNT_ID_C": ["001"]}),
        account_ids=["001"],
    )
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    ctx = WxccHealthContext(
        scope=scope,
        technology="Webex Contact Center",
        days=90,
        period_start=now,
        period_end=now,
        ab_df=pd.DataFrame(
            {
                "BU_NAME": ["ACME CORP"],
                "SEVERITY_C": ["P2"],
                "STATUS_C": ["Open"],
                "open_age_days": [14],
                "Title": ["Onboarding stalled"],
            }
        ),
        csone_df=pd.DataFrame(),
        tac_df=pd.DataFrame(
            {
                "BU_NAME": ["ACME CORP"],
                "Case #": ["C-1"],
                "Title": ["Login failure"],
                "Severity": ["P2"],
            }
        ),
        action_plans_df=pd.DataFrame(
            {
                "BU_NAME": ["ACME CORP"],
                "SUBJECT_C": ["Training plan"],
                "Status": ["Open"],
                "ID": ["ap-1"],
            }
        ),
        pulse_df=pd.DataFrame(
            {"BU_NAME": ["ACME CORP"], "CUSTOMER_PULSE__C": ["Yellow"], "COMMENTS__C": ["Needs help"]}
        ),
        subs_slice=scope.team_subs_df,
        ext_incidents=[],
        matched_bugs=[],
        risk_profile={"risk_score_0_100": 42.0, "risk_band": "MEDIUM"},
        partial_data_warnings=[{"kind": "csone_missing", "message": "no file"}],
        source_diagnostics=[
            SourceDiagnostic("snowflake_subscriptions", "Snowflake subscriptions", "used", 1),
            SourceDiagnostic("csone", "CSOne TAC/BEMS", "unavailable", 0, reason="no_onedrive_sync"),
        ],
    )
    for key, val in overrides.items():
        setattr(ctx, key, val)
    return ctx


class TestTechnologyAlias:
    def test_wxcc_alias_maps_to_webex_contact_center(self):
        from wxcc_health_input_exporter import normalize_technology_arg

        assert normalize_technology_arg("wxcc") == "Webex Contact Center"
        assert normalize_technology_arg("WXCC") == "Webex Contact Center"


class TestCustomerSlice:
    def test_slice_df_by_customer_matches_exact_bu_name(self):
        from wxcc_health_input_exporter import slice_df_by_customer

        df = pd.DataFrame({"BU_NAME": ["ACME CORP", "OTHER CO"]})
        subs = pd.DataFrame({"BU_NAME": ["ACME CORP"]})
        out = slice_df_by_customer(df, "ACME CORP", subs)
        assert len(out) == 1
        assert out.iloc[0]["BU_NAME"] == "ACME CORP"


class TestBuildWxccHealthText:
    def test_section_order_and_ops_not_invented(self):
        from wxcc_health_input_exporter import NOT_AVAILABLE, build_wxcc_health_text

        text = build_wxcc_health_text(_sample_context())
        names = [
            "Queue performance",
            "Agent availability",
            "Platform and integrations",
            "Adoption barriers",
            "Support cases (TAC)",
            "Customer pulse",
            "Action plans",
            "Contract / renewal",
            "Additional notes",
        ]
        positions = [text.index(n) for n in names]
        assert positions == sorted(positions)
        assert f"Queue metrics: {NOT_AVAILABLE}" in text
        assert f"Service level / ASA: {NOT_AVAILABLE}" in text
        assert "Open adoption barriers: 1" in text
        assert "Total TAC cases: 1" in text
        assert "Open action plans: 1" in text
        assert "Data sources used (debug):" in text
        assert "Data sources unavailable (debug):" in text
        assert "csone: no_onedrive_sync" in text
        assert "Partial data (csone_missing)" in text

    def test_no_llm_markers_in_exporter_module(self):
        src = _read(PROJECT_ROOT / "wxcc_health_input_exporter.py")
        assert "generate_llm" not in src
        assert "compose_grounded_answer" not in src


class TestExportOrchestration:
    def test_empty_export_raises(self, monkeypatch):
        from wxcc_health_input_exporter import WxccExportError, export_wxcc_health_input

        def _fake_fetch(scope, technology, **kwargs):
            ctx = _sample_context()
            ctx.ab_df = pd.DataFrame()
            ctx.tac_df = pd.DataFrame()
            ctx.pulse_df = pd.DataFrame()
            ctx.action_plans_df = pd.DataFrame()
            ctx.ext_incidents = []
            return ctx

        def _fake_scope(**kwargs):
            return _sample_context().scope

        monkeypatch.setattr(
            "wxcc_health_input_exporter.resolve_customer_scope",
            lambda **kw: _fake_scope(**kw),
        )
        monkeypatch.setattr(
            "wxcc_health_input_exporter.fetch_customer_datasets",
            lambda scope, tech, **kw: _fake_fetch(scope, tech, **kw),
        )

        with pytest.raises(WxccExportError) as exc:
            export_wxcc_health_input(customer="ACME CORP", days=90)
        assert exc.value.code == "empty_export"

    def test_atomic_write_utf8(self, tmp_path, monkeypatch):
        from wxcc_health_input_exporter import export_wxcc_health_input

        monkeypatch.setattr(
            "wxcc_health_input_exporter.resolve_customer_scope",
            lambda **kw: _sample_context().scope,
        )
        monkeypatch.setattr(
            "wxcc_health_input_exporter.fetch_customer_datasets",
            lambda scope, tech, **kw: _sample_context(),
        )

        out = tmp_path / "out.txt"
        result = export_wxcc_health_input(
            customer="ACME CORP",
            days=90,
            output_path=str(out),
        )
        assert out.exists()
        assert result.text == out.read_text(encoding="utf-8")
        assert "WxCC Contact Center Health Snapshot" in result.text


class TestApiRoute:
    def test_post_returns_plain_text_attachment(self, client, monkeypatch):
        from wxcc_health_input_exporter import ExportResult

        def _fake_export(**kwargs):
            return ExportResult(
                text="WxCC export body\n",
                canonical_customer_name="ACME CORP",
            )

        monkeypatch.setattr(
            "wxcc_health_input_exporter.export_wxcc_health_input",
            _fake_export,
        )
        monkeypatch.setattr(
            "app_simple.get_latest_csone_from_folder_diag",
            lambda: (None, "not_synced", 0),
        )

        resp = client.post(
            "/api/export/wxcc-health-input",
            json={
                "customer_name": "ACME CORP",
                "technology": "wxcc",
                "days": 90,
            },
        )
        assert resp.status_code == 200
        assert resp.mimetype.startswith("text/plain")
        assert b"WxCC export body" in resp.data
        assert 'filename="ACME_CORP_wxcc_health_input.txt"' in resp.headers.get(
            "Content-Disposition", ""
        )

    def test_post_maps_customer_not_found_to_404(self, client, monkeypatch):
        from wxcc_health_input_exporter import WxccExportError

        def _raise(**kwargs):
            raise WxccExportError("customer_not_found", "no match")

        monkeypatch.setattr(
            "wxcc_health_input_exporter.export_wxcc_health_input",
            _raise,
        )
        monkeypatch.setattr(
            "app_simple.get_latest_csone_from_folder_diag",
            lambda: (None, "not_synced", 0),
        )

        resp = client.post(
            "/api/export/wxcc-health-input",
            json={"customer_name": "MISSING"},
        )
        assert resp.status_code == 404
        payload = resp.get_json()
        assert payload["error_kind"] == "customer_not_found"


class TestUiSourceShape:
    def test_analyze_page_has_export_button_and_script(self):
        html = _read(PROJECT_ROOT / "templates" / "analyze.html")
        assert "data-wxcc-export-btn" in html
        assert "wxcc_health_export.js" in html

    def test_customer_360_has_export_button_and_script(self):
        html = _read(PROJECT_ROOT / "templates" / "customer_360.html")
        assert "data-wxcc-export-customer360" in html
        assert "wxcc_health_export.js" in html

    def test_js_uses_textcontent_not_innerhtml(self):
        js = _read(PROJECT_ROOT / "static" / "js" / "wxcc_health_export.js")
        assert "Round 134" in js
        assert ".innerHTML" not in js
        assert "exportWxccHealthInput" in js
        assert "/api/export/wxcc-health-input" in js

    def test_app_simple_route_marker(self):
        src = _read(PROJECT_ROOT / "app_simple.py")
        assert "Round 134" in src
        assert "/api/export/wxcc-health-input" in src


class TestPackagingPin:
    def test_hidden_import_in_mac_and_pc_specs(self):
        mac = _read(PROJECT_ROOT / "adoptiq_mac.spec")
        pc = _read(PROJECT_ROOT / "adoptiq_pc.spec")
        assert "'wxcc_health_input_exporter'" in mac
        assert "'wxcc_health_input_exporter'" in pc


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.joinpath("..").exists(),
    reason="sanity",
)
class TestCliScriptShape:
    def test_cli_module_exists(self):
        cli = PROJECT_ROOT / "scripts" / "export_wxcc_health_input.py"
        assert cli.is_file()
        src = cli.read_text(encoding="utf-8")
        assert "--dry-run" in src
        assert "export_wxcc_health_input" in src
