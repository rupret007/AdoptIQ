"""Round 147 source contracts for the report evidence drill-down UI."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]
ANALYZE = (ROOT / "templates" / "analyze.html").read_text(encoding="utf-8")
LEADER_FORM = (ROOT / "templates" / "leader_report_form.html").read_text(
    encoding="utf-8"
)
WORKSPACE_JS = (
    ROOT / "static" / "js" / "manager_decision_workspace.js"
).read_text(encoding="utf-8")
WORKSPACE_CSS = (
    ROOT / "static" / "css" / "manager_decision_workspace.css"
).read_text(encoding="utf-8")


def test_round147_has_one_reusable_accessible_evidence_dialog() -> None:
    assert ANALYZE.count("data-evidence-dialog") == 1
    assert 'aria-labelledby="workspace-evidence-title"' in ANALYZE
    assert 'aria-describedby="workspace-evidence-description"' in ANALYZE
    assert 'aria-modal="true"' in ANALYZE
    assert 'tabindex="-1"' in ANALYZE
    assert 'role="status"' in ANALYZE
    assert 'aria-live="polite"' in ANALYZE
    assert ANALYZE.count("data-evidence-close") == 2
    for hook in (
        "data-decision-evidence-notice",
        "data-evidence-state",
        "data-evidence-content",
        "data-evidence-summary",
        "data-evidence-records",
        "data-evidence-limitations",
        "data-evidence-source-data",
    ):
        assert hook in ANALYZE


def test_round147_changed_templates_parse() -> None:
    environment = Environment(autoescape=True)
    environment.parse(ANALYZE)
    environment.parse(LEADER_FORM)


def test_round147_requests_only_server_bound_evidence_keys() -> None:
    assert "var EVIDENCE_LIMIT = 25;" in WORKSPACE_JS
    assert "REPORT_URL + encodeURIComponent(reportId) + '/evidence?'" in WORKSPACE_JS
    assert "new URLSearchParams({ evidence_key: key, limit: text(EVIDENCE_LIMIT) })" in WORKSPACE_JS
    assert "metric.evidence_key || metric.metric_key" in WORKSPACE_JS
    assert "item.metric_key" in WORKSPACE_JS
    assert "account.evidence_key" in WORKSPACE_JS
    assert "action.evidence_key" in WORKSPACE_JS
    assert "report.evidence_available !== true" in WORKSPACE_JS
    assert "report.evidence_notice" in WORKSPACE_JS
    assert "safeEvidenceKey" in WORKSPACE_JS
    assert "payload.evidence" in WORKSPACE_JS


def test_round147_evidence_panel_renders_exact_record_metadata() -> None:
    for field in (
        "record.source_sheet",
        "record.source_row_number",
        "record.record_id",
        "record.record_id_quality",
        "record.customer",
        "record.title",
        "record.status",
        "record.date",
        "record.owner",
        "record.summary",
    ):
        assert field in WORKSPACE_JS
    for response_field in (
        "evidence.evidence_key",
        "evidence.source_state",
        "evidence.total_records",
        "evidence.scope_label",
        "evidence.data_as_of_utc",
        "evidence.source_data_url",
        "evidence.limitations",
    ):
        assert response_field in WORKSPACE_JS
    assert "safeRelativeHref(evidence.source_data_url)" in WORKSPACE_JS


def test_round147_account_exceptions_offer_customer_deep_dive() -> None:
    assert "Open Customer 360" in WORKSPACE_JS
    assert "'/customer/' + encodeURIComponent(text(account.customer))" in WORKSPACE_JS
    assert "noopener noreferrer" in WORKSPACE_JS
    assert "Open Customer 360 deep dive for " in WORKSPACE_JS


def test_round147_account_cards_render_per_field_incomplete_states() -> None:
    assert "account.field_states" in WORKSPACE_JS
    for field in (
        "risk_band",
        "risk_score_0_100",
        "open_action_plans",
        "overdue_action_plans",
        "critical_high_barriers",
        "tac_cases",
    ):
        assert field in WORKSPACE_JS
    assert "Risk unavailable (" in WORKSPACE_JS
    assert "Unavailable (" in WORKSPACE_JS


def test_round147_evidence_panel_has_loading_error_empty_and_focus_contracts() -> None:
    assert "setEvidenceState('loading'" in WORKSPACE_JS
    assert "'error'," in WORKSPACE_JS
    assert "setEvidenceState('empty'" in WORKSPACE_JS
    assert "dialog.showModal()" in WORKSPACE_JS
    assert "dialog.addEventListener('cancel'" in WORKSPACE_JS
    assert "dialog.addEventListener('close', restoreEvidenceFocus)" in WORKSPACE_JS
    assert "returnTarget.focus()" in WORKSPACE_JS
    assert "evidenceController.abort()" in WORKSPACE_JS
    assert "View evidence" in WORKSPACE_JS
    assert "aria-label', 'View evidence for '" in WORKSPACE_JS


def test_round147_api_values_remain_text_and_never_become_markup() -> None:
    for unsafe_sink in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
    ):
        assert unsafe_sink not in WORKSPACE_JS
    assert ".textContent" in WORKSPACE_JS
    assert "replaceChildren" in WORKSPACE_JS


def test_round147_evidence_styles_cover_dialog_records_mobile_and_backdrop() -> None:
    for selector in (
        ".workspace-evidence-button",
        ".workspace-evidence-dialog",
        ".workspace-evidence-dialog::backdrop",
        ".workspace-evidence-state--loading",
        ".workspace-evidence-state--error",
        ".workspace-evidence-state--empty",
        ".workspace-evidence-summary",
        ".workspace-evidence-record__details",
    ):
        assert selector in WORKSPACE_CSS
    assert "@media (max-width: 575.98px)" in WORKSPACE_CSS
    assert "var(--bg-surface" in WORKSPACE_CSS
    assert "var(--border-subtle)" in WORKSPACE_CSS


def test_round147_leader_form_promises_concise_word_and_separate_source_data() -> None:
    assert "concise decision report" in LEADER_FORM
    assert "full supporting records stay in the paired Source Data workbook" in LEADER_FORM
    assert "Word decision report:" in LEADER_FORM
    assert "Source Data workbook:" in LEADER_FORM
    assert "Detailed list of all adoption barriers" not in LEADER_FORM
    assert "Detailed AdoptIQ summaries for each direct report" not in LEADER_FORM
    assert "Generate comprehensive team activity reports" not in LEADER_FORM


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_round147_workspace_javascript_parses() -> None:
    subprocess.run(
        ["node", "--check", str(ROOT / "static" / "js" / "manager_decision_workspace.js")],
        check=True,
        capture_output=True,
        text=True,
    )
