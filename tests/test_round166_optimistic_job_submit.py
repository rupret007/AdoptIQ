"""Round 166 optimistic job row before start POST returns."""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_report_jobs_dashboard_exports_pending_helpers() -> None:
    js = (PROJECT_ROOT / "static/js/report_jobs_dashboard.js").read_text(encoding="utf-8")
    for name in (
        "recordPendingJob",
        "promotePendingJob",
        "discardPendingJob",
    ):
        assert_in_source(js, name + ":", label=name)


def test_analyze_form_records_pending_job_before_fetch() -> None:
    html = (PROJECT_ROOT / "templates/analyze.html").read_text(encoding="utf-8")
    pending = html.index("recordPendingJob")
    fetch = html.index("const response = await fetch(endpoint, fetchOptions);")
    promote = html.index("promotePendingJob")
    discard = html.index("discardPendingJob")
    assert pending < fetch < promote
    assert discard > fetch


def test_leader_form_records_pending_job_before_fetch() -> None:
    html = (PROJECT_ROOT / "templates/leader_report_form.html").read_text(encoding="utf-8")
    pending = html.index("recordPendingJob")
    fetch = html.index("const response = await fetch('/start_leader_report'")
    promote = html.index("promotePendingJob")
    discard = html.index("discardPendingJob")
    assert pending < fetch < promote
    assert discard > fetch


def test_leader_card_drops_permanent_border_and_checked_ring() -> None:
    html = (PROJECT_ROOT / "templates/analyze.html").read_text(encoding="utf-8")
    assert 'id="leader-card"' in html
    assert 'id="leader-card"' in html
    leader_slice = html[html.index('id="leader-card"') - 80 : html.index('id="leader-card"') + 80]
    assert "border-2" not in leader_slice
    css = (PROJECT_ROOT / "static/css/manager_decision_workspace.css").read_text(encoding="utf-8")
    assert ".workspace-report-card:has(.form-check-input:checked) {" not in css
    assert ":has(.form-check-input:checked):hover" in css
