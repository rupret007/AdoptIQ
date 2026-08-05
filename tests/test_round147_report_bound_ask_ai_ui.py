"""Round 147 report-bound Ask AI page trust and immutability contracts."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]
ASK_AI_TEMPLATE = ROOT / "templates" / "ask_ai.html"
ASK_AI_CLIENT = ROOT / "static" / "js" / "ask_ai.js"


def _install_verified_report(
    monkeypatch,
    app_simple,
    *,
    analysis_id: str,
    snapshot: dict,
) -> None:
    """Install a path-free verified report context for one GET request."""

    import manager_decision_workspace as decision_workspace

    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_snapshot",
        lambda requested: (snapshot, 200)
        if requested == analysis_id
        else (None, 404),
    )
    monkeypatch.setattr(
        app_simple,
        "_r146_status_for_workspace",
        lambda requested: {"analysis_id": requested},
    )
    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_artifact",
        lambda _status, kind: "/tmp/verified-source-data.xlsx"
        if kind == "excel"
        else None,
    )
    artifact_hash = "a" * 64
    monkeypatch.setattr(
        app_simple,
        "_r146_persisted_excel_hash",
        lambda *_args: artifact_hash,
    )
    monkeypatch.setattr(
        app_simple,
        "_r146_file_sha256",
        lambda *_args: artifact_hash,
    )
    monkeypatch.setattr(
        decision_workspace,
        "select_report_bound_evidence",
        lambda *_args, **_kwargs: {
            "schema": "report-bound-evidence/v1",
            "evidence_contract": "canonical-evidence-links/v1",
            "groups": [],
            "returned_record_count": 0,
        },
    )


def test_report_bound_page_shows_exact_locked_context_and_escapes_values(
    client, app, monkeypatch
) -> None:
    import app_simple

    analysis_id = "round147-bound-page-ui"
    manager = 'Manager <script>alert("manager")</script>'
    scope_label = 'Acme "><img src=x onerror=alert("scope")>'
    technology = '<svg onload=alert("tech")>'
    fingerprint = 'sha256:<script>alert("fingerprint")</script>'
    snapshot = {
        "analysis_id": analysis_id,
        "canonical_snapshot": True,
        "report_type": "leader",
        "manager": manager,
        "technology": technology,
        "days": 120,
        "scope_type": "customer",
        "scope_value": "ACME-ACCOUNT-001",
        "scope_label": scope_label,
        "data_as_of_utc": "2026-08-04T12:34:56Z",
        "fact_fingerprint": fingerprint,
        "evidence_available": True,
        "evidence_contract": "canonical-evidence-links/v1",
    }
    _install_verified_report(
        monkeypatch,
        app_simple,
        analysis_id=analysis_id,
        snapshot=snapshot,
    )
    monkeypatch.setitem(app.config, "LOCAL_ACCEPTANCE_MODE", True)

    response = client.get(f"/ask-ai?report_analysis_id={analysis_id}")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="r147ReportBoundDisclosure"' in page
    assert "Immutable verified report snapshot" in page
    assert "paired Source Data workbook" in page
    assert "does not refresh Snowflake" in page
    assert "Controlled local fixture" in page
    assert "No live Snowflake or enterprise-system validation" in page
    assert 'id="r147ReportBoundContext"' in page
    assert "Leader" in page
    assert "Customer" in page
    assert "120 days" in page
    assert "2026-08-04T12:34:56Z" in page
    assert "Fixed at report generation; no question-time refresh" in page

    # Jinja autoescape must protect both visible fields and hidden option values.
    assert "<script>alert" not in page
    assert "<img src=x" not in page
    assert "<svg onload" not in page
    assert "&lt;script&gt;alert" in page
    assert "&lt;img src=x onerror=alert" in page
    assert "&lt;svg onload=alert" in page
    assert 'id="r147ReportFingerprint"' in page

    # The IDs retained for client compatibility are server-valued, hidden,
    # disabled, and cannot imply an editable portfolio scope.
    assert 'id="r147ReportBoundSelectorValues" class="d-none"' in page
    for selector_id in ("aiManager", "aiTech", "aiDays"):
        assert (
            f'id="{selector_id}" disabled tabindex="-1" '
            'data-report-bound="true"'
        ) in page
    assert "All Managers</option>" not in page
    assert "All Technologies</option>" not in page
    assert "Fetching live data from Snowflake" not in page


def test_legacy_portfolio_page_keeps_live_copy_and_editable_controls(client) -> None:
    response = client.get("/ask-ai")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "AdoptIQ will fetch live data" in page
    assert "snapshot of your Snowflake portfolio data fetched at question time" in page
    assert 'id="aiManager" class="form-select"' in page
    assert 'id="aiTech" class="form-select"' in page
    assert 'id="aiDays" class="form-select"' in page
    assert "All Managers</option>" in page
    assert "All Technologies</option>" in page
    assert 'id="r147ReportBoundContext"' not in page
    assert 'id="r147ReportBoundDisclosure"' not in page


def test_report_bound_client_omits_selector_claims_and_binds_both_transports() -> None:
    client_source = ASK_AI_CLIENT.read_text(encoding="utf-8")
    builder_start = client_source.index("function _r147BuildQuestionPayload")
    builder_end = client_source.index("// Round 68 / Build 42 (C7)", builder_start)
    builder = client_source[builder_start:builder_end]

    assert "if (!_r146ReportPageBound)" in builder
    assert "payload.manager =" in builder
    assert "payload.technology =" in builder
    assert "payload.days =" in builder
    assert builder.index("if (!_r146ReportPageBound)") < builder.index("payload.manager =")
    assert builder.index("payload.days =") < builder.index("_r146ApplyReportBinding(payload)")
    assert "payload.report_context_mode = 'bound'" in client_source
    assert "payload.report_analysis_id = _r146ReportAnalysisId" in client_source
    assert client_source.count("_r147BuildQuestionPayload(") == 3
    assert "if (_r146ReportPageBound) { return; }" in client_source


def test_report_bound_template_is_valid_jinja_and_has_no_inline_context_script() -> None:
    template = ASK_AI_TEMPLATE.read_text(encoding="utf-8")

    Environment(autoescape=True).parse(template)
    assert "report_bound_page" in template
    assert "report_scope_context" in template
    assert "|tojson" not in template
    assert "innerHTML" not in template
