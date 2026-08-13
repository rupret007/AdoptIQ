"""Round 167 report history uses report-appropriate focus labels."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

import app_simple


ROOT = Path(__file__).resolve().parents[1]


def test_jobs_dashboard_uses_leader_scope_instead_of_fake_technology() -> None:
    source = (ROOT / "static" / "js" / "report_jobs_dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "function reportFocusLabel(job)" in source
    assert "job.scope_display || job.scope_label || job.scope_value" in source
    assert "team: 'Entire team'" in source
    assert "reportFocusLabel(job)" in source


def test_jobs_dashboard_labels_subscription_without_pending_technology() -> None:
    source = (ROOT / "static" / "js" / "report_jobs_dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "type === 'subscription' || scopeType === 'subscription'" in source
    assert "'Single subscription'" in source


def test_history_focus_uses_canonical_scope_for_non_technology_reports() -> None:
    assert (
        app_simple._r167_history_focus(  # noqa: SLF001
            {
                "report_type": "leader",
                "scope_type": "team",
                "technology": "",
            }
        )
        == "Entire team"
    )
    assert (
        app_simple._r167_history_focus(  # noqa: SLF001
            {
                "report_type": "leader",
                "scope_type": "customer",
                "scope_value": "Acme Corporation",
            }
        )
        == "Acme Corporation"
    )
    assert (
        app_simple._r167_history_focus(  # noqa: SLF001
            {
                "report_type": "subscription",
                "scope_type": "subscription",
                "scope_value": "SUB-001",
            }
        )
        == "SUB-001"
    )


def test_history_focus_retains_customer_and_technology_context() -> None:
    assert (
        app_simple._r167_history_focus(  # noqa: SLF001
            {
                "report_type": "comprehensive",
                "scope_type": "customer",
                "scope_value": "Acme Corporation",
                "technology": "Webex Calling",
            }
        )
        == "Acme Corporation · Webex Calling"
    )
    template = (ROOT / "templates" / "history.html").read_text(encoding="utf-8")
    assert "fa-crosshairs" in template
    assert "Focus" in template
    assert "{{ analysis.focus }}" in template


def test_ask_ai_manager_selector_deduplicates_aggregate_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = (ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")

    assert "m|trim|lower != 'all managers'" in template
    assert "default_manager|trim|lower == 'all managers'" in template
    monkeypatch.setattr(
        app_simple,
        "MANAGERS",
        ["All Managers", "Local Fixture Manager"],
    )
    monkeypatch.setattr(
        app_simple,
        "_r113_resolve_report_defaults",
        lambda: {
            "default_days": 90,
            "default_manager": "All Managers",
            "default_technology": "",
        },
    )
    response = app_simple.app.test_client().get("/ask-ai")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    manager_select = re.search(
        r'<select id="aiManager".*?</select>', html, flags=re.DOTALL
    )
    assert manager_select is not None
    assert manager_select.group(0).count(">All Managers</option>") == 1


def test_ask_ai_fixture_page_never_claims_live_snowflake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(app_simple.app.config, "LOCAL_ACCEPTANCE_MODE", True)
    response = app_simple.app.test_client().get("/ask-ai")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Controlled local fixture:" in html
    assert "never query live enterprise systems" in html
    assert "will fetch live data" not in html
    assert "No live Snowflake" in html
