"""Round 35 / native-corpus: pin the analyze-page Knowledge Corpus
panel.

Verifies that the Build8 SharePoint URL paste UI is fully removed:
* No more ``data-sharepoint-url-input`` element.
* No more ``data-sharepoint-save-url`` button.
* No more ``/api/settings/sharepoint_url`` reference in the bundled JS.
* The panel header is renamed to "AdoptIQ Knowledge Corpus".
* Connect / Sign-out controls remain (daily refresh still needs MSAL).
* The retired Flask route is gone from ``app_simple.py``'s sensitive set.
"""

from __future__ import annotations

from pathlib import Path


_ANALYZE_HTML = (
    Path(__file__).resolve().parent.parent / "templates" / "analyze.html"
)
_INTEL_JS = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "intel_status.js"
)
_APP_PY = (
    Path(__file__).resolve().parent.parent / "app_simple.py"
)


def test_analyze_html_drops_sharepoint_url_input():
    body = _ANALYZE_HTML.read_text(encoding="utf-8")
    assert "data-sharepoint-url-input" not in body, (
        "Round 35 retired the per-user SharePoint URL paste field; "
        "data-sharepoint-url-input must not appear in analyze.html."
    )


def test_analyze_html_drops_save_url_button():
    body = _ANALYZE_HTML.read_text(encoding="utf-8")
    assert "data-sharepoint-save-url" not in body
    assert "Save URL" not in body


def test_analyze_html_renames_panel_to_knowledge_corpus():
    body = _ANALYZE_HTML.read_text(encoding="utf-8")
    assert "AdoptIQ Knowledge Corpus" in body, (
        "Round 35 panel header must read 'AdoptIQ Knowledge Corpus'."
    )
    # And the previous label must NOT appear inside the corpus panel.
    # (The exact substring "SharePoint connection" was the Build8
    # header; verify that the new HTML doesn't carry both.)
    assert "SharePoint connection" not in body


def test_analyze_html_keeps_connect_and_signout_controls():
    body = _ANALYZE_HTML.read_text(encoding="utf-8")
    # MSAL device-code flow is still required for daily refresh.
    assert "data-sharepoint-signin" in body
    assert "data-sharepoint-signout" in body
    assert "Connect to Microsoft" in body
    assert "Sign out" in body


def test_intel_status_js_drops_url_save_route():
    body = _INTEL_JS.read_text(encoding="utf-8")
    assert "/api/settings/sharepoint_url" not in body, (
        "intel_status.js must not POST to the retired URL-save route."
    )
    assert "bindSharepointUrlSave" not in body
    assert "SHAREPOINT_URL_INPUT" not in body
    assert "SHAREPOINT_SAVE_URL" not in body


def test_app_simple_drops_sharepoint_url_endpoint():
    body = _APP_PY.read_text(encoding="utf-8")
    # Route handler def gone.
    assert "def api_settings_sharepoint_url" not in body
    # Sensitive-set entry gone.
    assert "'api_settings_sharepoint_url'" not in body
    assert '"api_settings_sharepoint_url"' not in body
    # Route registration gone.  We allow comments / docstrings to
    # mention the retired route for historical context, but the
    # literal Flask ``@app.route(...)`` decorator must not appear.
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue  # comment line: history references are fine
        # Flask route decorators reference the URL inside the
        # decorator string -- those are the regressions we want to
        # catch.
        if "@app.route" in line and "/api/settings/sharepoint_url" in line:
            raise AssertionError(
                "Round 35 removed POST /api/settings/sharepoint_url; "
                f"found a live route decorator: {line.strip()!r}"
            )
        if "@self.app.route" in line and "/api/settings/sharepoint_url" in line:
            raise AssertionError(
                "Round 35 removed POST /api/settings/sharepoint_url; "
                f"found a live route decorator: {line.strip()!r}"
            )
