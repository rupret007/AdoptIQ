"""Round 116 / Build 85 (D) -- pin the operator-first Help page rewrite.

The Build-83 acceptance feedback was that the Help page was stale and
developer-first ("pip install", ".env file") instead of telling an
operator how to actually run AdoptIQ.  Round 116 rewrote
``templates/help.html`` to be operator-first.  These source-shape +
render assertions pin the new sections so a future template edit
cannot silently regress back to the developer-first content or drop a
required section.

We also re-assert the two things the rewrite MUST preserve:

1. The in-page connectivity self-test button (#adoptiq-diag-btn) and
   its handler.
2. The in-page search input (injected by the extra_js block).
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_HELP_TEMPLATE_PATH = _PROJECT_ROOT / "templates" / "help.html"


def _read_help_html() -> str:
    return _HELP_TEMPLATE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Required operator-first sections (data-help-section anchors)
# ---------------------------------------------------------------------------

_REQUIRED_SECTIONS = [
    "quick-start",
    "report-chooser",
    "data-sources",
    "preferences",
    "intelligence",
    "ask-ai",
    "external-intel",
    "finding-reports",
    "admin-console",
    "troubleshooting",
]


@pytest.mark.parametrize("section", _REQUIRED_SECTIONS)
def test_help_page_has_required_section(section):
    html = _read_help_html()
    assert f'data-help-section="{section}"' in html, (
        f"Round 116 / Build 85 (D): help.html must carry the "
        f"'{section}' operator section."
    )


# ---------------------------------------------------------------------------
# Operator-first content (not developer-first)
# ---------------------------------------------------------------------------


def test_help_page_is_operator_first_not_pip_install():
    """The Quick Start must NOT tell the operator to pip install or edit
    a .env file -- credentials are bundled at build time."""
    html = _read_help_html()
    # The quick-start section explicitly states no setup is required.
    assert_in_source(html, "No setup required", label='html')
    # The old developer-first "Install Dependencies" heading is gone.
    assert "Install Dependencies" not in html, (
        "Round 116: the developer-first 'Install Dependencies' step must "
        "be removed from the operator Help page."
    )
    assert "ModuleNotFoundError" not in html, (
        "Round 116: the developer-first 'Import Errors / ModuleNotFoundError' "
        "troubleshooting item must be removed."
    )


def test_help_page_quick_start_mentions_vpn():
    html = _read_help_html()
    assert "VPN" in html, (
        "Round 116: the Quick Start must tell the operator to connect to "
        "the Cisco VPN first."
    )


def test_help_page_report_chooser_covers_all_four_types():
    html = _read_help_html()
    for report in ("Comprehensive", "Compact", "Renewal", "Leader"):
        assert report in html, (
            f"Round 116: the report chooser must describe the {report} report."
        )


def test_help_page_documents_gemini_default_model():
    """Preferences section must name the Gemini default + Test-before-Save."""
    html = _read_help_html()
    assert_in_source(html, "gemini-3.1-flash-lite", label='html')
    assert_in_source(html, "Test-before-Save", label='html')


def test_help_page_documents_add_shortcut_workflow():
    html = _read_help_html()
    assert "Add shortcut to OneDrive" in html, (
        "Round 116: the Intelligence section must document the "
        "'Add shortcut to OneDrive' corpus workflow."
    )


def test_help_page_troubleshooting_covers_new_items():
    html = _read_help_html()
    for marker in (
        "VPN not connected",
        "OneDrive not synced",
        "Partial-data warnings",
        "Restart-required banner",
        "Force-quit",
    ):
        assert marker in html, (
            f"Round 116: troubleshooting must cover '{marker}'."
        )


def test_help_page_admin_console_is_not_shutdown():
    """The Admin Console section must reassure the operator the link does
    not shut the server down (the Build-83 mis-click confusion)."""
    html = _read_help_html()
    assert "it never shuts the server down" in html.lower() or (
        "never shut" in html.lower() and "Quit" in html
    ), (
        "Round 116: the Admin Console section must clarify the link does "
        "not stop the server."
    )


# ---------------------------------------------------------------------------
# Preserved features (must NOT regress)
# ---------------------------------------------------------------------------


def test_help_page_preserves_connectivity_self_test():
    html = _read_help_html()
    assert_in_source(html, 'id="adoptiq-diag-btn"', label='html')
    assert_in_source(html, "runConnectivityDiagnostics", label='html')
    assert_in_source(html, "/api/diag/connectivity", label='html')


def test_help_page_preserves_search_input():
    html = _read_help_html()
    assert "help-search-input" in html, (
        "Round 116: the in-page help search must be preserved."
    )


# ---------------------------------------------------------------------------
# Renders cleanly through the Flask app (Jinja validity)
# ---------------------------------------------------------------------------


def test_help_page_renders_200(client):
    resp = client.get("/help")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert_in_source(body, "AdoptIQ Operator Guide", label='body')
    assert_in_source(body, 'data-help-section="quick-start"', label='body')
