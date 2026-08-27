"""Round 146 manager decision workspace template/static contracts."""

from __future__ import annotations
from source_shape_utils import assert_in_source

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ANALYZE = (ROOT / "templates" / "analyze.html").read_text(encoding="utf-8")
HISTORY = (ROOT / "templates" / "previous_reports.html").read_text(encoding="utf-8")
WORKSPACE_JS = (ROOT / "static" / "js" / "manager_decision_workspace.js").read_text(encoding="utf-8")
HISTORY_JS = (ROOT / "static" / "js" / "report_history_workspace.js").read_text(encoding="utf-8")
ASK_AI_JS = (ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
WORKSPACE_CSS = (ROOT / "static" / "css" / "manager_decision_workspace.css").read_text(encoding="utf-8")


def test_round146_workspace_routes_render_new_assets(client):
    analyze = client.get("/")
    assert analyze.status_code == 200
    analyze_body = analyze.get_data(as_text=True)
    assert "Manager Decision Workspace" in analyze_body
    assert "/static/js/manager_decision_workspace.js" in analyze_body
    assert "/static/css/manager_decision_workspace.css" in analyze_body

    history = client.get("/previous-reports")
    assert history.status_code == 200
    history_body = history.get_data(as_text=True)
    assert "data-history-filter-form" in history_body
    assert "/static/js/report_history_workspace.js" in history_body


def test_round146_analyze_is_one_manager_decision_configuration_surface():
    assert "Manager Decision Workspace" in ANALYZE
    assert ANALYZE.count('id="analysisForm"') == 1
    for report_type in (
        "leader",
        "comprehensive",
        "compact",
        "renewal",
        "renewal_portfolio",
        "subscription",
    ):
        assert f'value="{report_type}"' in ANALYZE
    assert 'data-workspace-leader-scope' in ANALYZE
    assert 'data-workspace-config-card' in ANALYZE
    assert 'name="workspace_scope_type"' in ANALYZE
    assert 'name="scope_type"' in ANALYZE
    assert 'name="scope_value"' in ANALYZE
    assert 'name="scope_member"' in ANALYZE
    assert 'name="subscription_id"' in ANALYZE


def test_round146_leader_is_the_manager_recommended_default():
    assert 'id="leader" value="leader" checked' in ANALYZE
    assert 'id="comprehensive" value="comprehensive" checked' not in ANALYZE
    assert "|| 'comprehensive'" not in ANALYZE
    assert "const leaderRadio = document.getElementById('leader');" in ANALYZE


def test_round146_analyze_preserves_existing_generation_routes():
    for endpoint in (
        "/start_analysis",
        "/start_compact_analysis",
        "/start_leader_report",
        "/start_subscription_analysis",
    ):
        assert endpoint in ANALYZE
    assert "AdoptIQReportJobs.recordStartedJob" in ANALYZE
    assert "adoptiq:report-started" in ANALYZE


def test_round146_scope_preview_and_decision_view_have_accessible_states():
    assert 'data-workspace-preview-body' in ANALYZE
    assert 'aria-live="polite"' in ANALYZE
    assert 'data-decision-report-panel' in ANALYZE
    assert 'data-decision-kpis' in ANALYZE
    assert 'data-decision-charts' in ANALYZE
    assert 'data-decision-accounts' in ANALYZE
    assert 'data-decision-actions' in ANALYZE
    assert 'data-decision-limitations' in ANALYZE
    assert 'data-decision-insights' in ANALYZE
    assert 'data-customer-share-readiness' in ANALYZE
    assert "Evidence-backed insights" in ANALYZE
    assert "Internal preview — not customer shareable" in ANALYZE
    assert 'data-workspace-ask-scope' in ANALYZE
    assert "/api/decision-workspace/scope-preview" in WORKSPACE_JS
    assert "/api/decision-workspace/report/" in WORKSPACE_JS
    assert "/api/leader_scope_options" in WORKSPACE_JS
    assert "report.charts" in WORKSPACE_JS
    assert "report.decision_insights" in WORKSPACE_JS
    assert "customer_share_readiness" in WORKSPACE_JS
    assert "cannot publish or send anything" in WORKSPACE_JS
    assert "chart.series" in WORKSPACE_JS
    assert "source_warnings" in WORKSPACE_JS
    assert "Controlled local fixture" in WORKSPACE_JS
    assert "prioritizeWorkspace" in WORKSPACE_JS
    assert "no live validation was performed" not in WORKSPACE_JS.lower()


def test_round146_history_filters_reopens_and_compares_canonical_reports():
    for selector in (
        "data-history-filter-form",
        "data-history-results",
        "data-history-show-more",
        "data-compare-button",
        "data-compare-metrics",
        "data-compare-actions",
        "data-compare-sources",
        "data-history-detail-panel",
    ):
        assert selector in HISTORY
    assert "Source availability changes" in HISTORY
    assert "canonical facts" in HISTORY
    assert "/api/decision-workspace/history" in HISTORY_JS
    assert "/api/decision-workspace/compare" in HISTORY_JS
    assert "/api/decision-workspace/report/" in HISTORY_JS
    assert "before_analysis_id" in HISTORY_JS
    assert "after_analysis_id" in HISTORY_JS
    assert "X-CSRFToken" in HISTORY_JS
    assert "visibleLimit = 12" in HISTORY_JS
    assert "report.charts" in HISTORY_JS
    assert "report.decision_insights" in HISTORY_JS
    assert "customer_share_readiness" in HISTORY_JS
    assert "insight.caveat" in HISTORY_JS
    assert "insight.evidence_key" in HISTORY_JS
    assert "linked source record(s)" in HISTORY_JS
    assert "source_warnings" in HISTORY_JS


def test_round146_ui_never_formats_raw_warning_objects_as_manager_copy():
    assert "partial_data_warnings" not in WORKSPACE_JS
    assert "partial_data_warnings" not in HISTORY_JS
    assert "warning.kind" not in WORKSPACE_JS
    assert "item.kind" not in HISTORY_JS


def test_round146_history_retains_progressive_download_fallback():
    assert "No previous reports found in the output folder." in HISTORY
    assert "Download Word File" in HISTORY
    assert "Download Excel File" in HISTORY
    assert "Back to Main Page" in HISTORY
    assert "data-history-fallback" in HISTORY


def test_round146_ask_ai_posts_same_origin_report_binding_on_both_transports():
    assert "new URL(window.location.href)" in ASK_AI_JS
    assert "_r146PageUrl.origin === window.location.origin" in ASK_AI_JS
    assert "searchParams.has('report_analysis_id')" in ASK_AI_JS
    assert "payload.report_analysis_id = _r146ReportAnalysisId" in ASK_AI_JS
    assert "payload.report_context_mode = 'bound'" in ASK_AI_JS
    assert "headers['X-AdoptIQ-Report-Context'] = 'bound'" in ASK_AI_JS
    assert "_r146ApplyReportBinding(payload, _requestHeaders)" in ASK_AI_JS
    assert "_r146ApplyReportBinding(payload, requestHeaders)" in ASK_AI_JS


@pytest.mark.parametrize("source", [WORKSPACE_JS, HISTORY_JS])
def test_round146_api_text_is_not_rendered_as_html(source: str):
    for unsafe_sink in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert unsafe_sink not in source
    assert_in_source(source, ".textContent", label='source')
    assert_in_source(source, "replaceChildren", label='source')


def test_round146_workspace_styles_are_responsive_and_reduced_motion_safe():
    assert ".workspace-report-grid" in WORKSPACE_CSS
    assert ".workspace-chart-grid" in WORKSPACE_CSS
    assert ".workspace-chart__bar" in WORKSPACE_CSS
    assert "grid-template-columns: repeat(auto-fit" in WORKSPACE_CSS
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in WORKSPACE_CSS
    assert ".workspace-report-card-badge-slot" in WORKSPACE_CSS
    assert ".workspace-report-card .card-body" in WORKSPACE_CSS
    assert "@media (max-width: 575.98px)" in WORKSPACE_CSS
    assert "@media (prefers-reduced-motion: reduce)" in WORKSPACE_CSS
    assert "var(--bg-surface" in WORKSPACE_CSS
    assert "var(--border-subtle)" in WORKSPACE_CSS


def test_round162_report_cards_reserve_badge_slot_on_every_type():
    assert ANALYZE.count("workspace-report-card-badge-slot") == 5
    assert 'workspace-report-card-badge-slot' in ANALYZE
    assert "Recommended for managers" in ANALYZE


def test_round162_report_type_radiogroup_has_accessible_name():
    assert 'id="report-type-label"' in ANALYZE
    assert 'role="radiogroup"' in ANALYZE
    assert 'aria-labelledby="report-type-label"' in ANALYZE
    assert 'aria-describedby="report-type-hint"' in ANALYZE


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
@pytest.mark.parametrize(
    "script",
    [
        ROOT / "static" / "js" / "manager_decision_workspace.js",
        ROOT / "static" / "js" / "report_history_workspace.js",
        ROOT / "static" / "js" / "ask_ai.js",
    ],
)
def test_round146_workspace_javascript_parses(script: Path):
    subprocess.run(["node", "--check", str(script)], check=True, capture_output=True, text=True)
