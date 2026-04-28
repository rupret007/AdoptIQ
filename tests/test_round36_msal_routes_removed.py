"""Round 36 / onedrive-sync-auth: pin that the MSAL Flask routes are
gone.

Round 33 / 34c shipped three SharePoint MSAL routes:

  * ``POST /api/corpus/sharepoint/signin``
  * ``POST /api/corpus/sharepoint/signout``
  * ``POST /api/corpus/sharepoint/refresh``

Round 36 retired them when the runtime indexer pivoted to OneDrive
sync presence as the auth signal.  The routes MUST NOT be re-added
because:

* They depend on ``sharepoint_corpus_source`` (deleted module).
* They surface a device-code modal in the UI that the user can no
  longer interact with (the panel was redesigned).
* Re-introducing them would re-pull MSAL into the dependency tree
  and re-trigger the Cisco tenant admin-consent block that
  motivated the Round 36 pivot.

This test pins the negative contract: the Flask app MUST NOT
register any of those URLs, and the source MUST NOT contain the
route decorators.
"""

# ruff: noqa: E501

from __future__ import annotations

import os
import re

import pytest


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_app_simple_source() -> str:
    src_path = os.path.join(_REPO_ROOT, "app_simple.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        return fh.read()


def _flask_route_decorators(text: str) -> list[str]:
    """Extract ``@app.route('...')`` URL strings from app_simple.py
    so we can assert the negative without false-positives from
    docstrings or comments."""
    pattern = re.compile(
        r"@app\.route\(\s*['\"]([^'\"]+)['\"]",
        re.MULTILINE,
    )
    return pattern.findall(text)


@pytest.mark.parametrize(
    "url",
    [
        "/api/corpus/sharepoint/signin",
        "/api/corpus/sharepoint/signout",
        "/api/corpus/sharepoint/refresh",
    ],
)
def test_app_simple_no_longer_registers_sharepoint_route(url):
    """The three legacy SharePoint MSAL routes must not appear as
    Flask route decorators.  We grep the source (rather than
    introspecting ``app.url_map``) so the test runs without
    importing the full Flask app -- faster and resilient to
    boot-time failures."""
    text = _read_app_simple_source()
    routes = _flask_route_decorators(text)
    assert url not in routes, (
        f"app_simple.py still registers Flask route {url!r}; "
        f"Round 36 removed the MSAL/Graph runtime path -- this URL "
        f"would 500 on first call (sharepoint_corpus_source is gone). "
        f"Found these /api/corpus/sharepoint/* routes: "
        f"{[r for r in routes if r.startswith('/api/corpus/sharepoint')]!r}"
    )


def test_app_simple_no_sharepoint_endpoints_in_sensitive_list():
    """The Round 5 _SENSITIVE_ENDPOINTS list previously called out
    the SharePoint signin / signout endpoints for CSRF enforcement.
    Now that the endpoints are gone, those entries MUST also be
    pruned so a stale entry cannot route a future endpoint of the
    same name into the wrong CSRF code path."""
    text = _read_app_simple_source()
    # Anchor on the literal endpoint names (Flask view function
    # names) rather than substrings of words like "signin".
    forbidden = (
        "'api_corpus_sharepoint_signin'",
        '"api_corpus_sharepoint_signin"',
        "'api_corpus_sharepoint_signout'",
        '"api_corpus_sharepoint_signout"',
        "'api_corpus_sharepoint_refresh'",
        '"api_corpus_sharepoint_refresh"',
    )
    for needle in forbidden:
        assert needle not in text, (
            f"app_simple.py still references endpoint {needle}; "
            "Round 36 retired all /api/corpus/sharepoint/* routes."
        )


def test_app_simple_no_sharepoint_view_functions():
    """The view function definitions themselves (``def
    api_corpus_sharepoint_*``) must be gone.  Catches the case where
    the route decorator was deleted but the function body lingered
    (unreachable code that still imports the deleted MSAL module)."""
    text = _read_app_simple_source()
    forbidden = (
        "def api_corpus_sharepoint_signin",
        "def api_corpus_sharepoint_signout",
        "def api_corpus_sharepoint_refresh",
    )
    for needle in forbidden:
        assert needle not in text, (
            f"app_simple.py still defines view function {needle!r}; "
            "Round 36 removed all SharePoint MSAL view functions."
        )


@pytest.mark.parametrize(
    "url",
    [
        "/api/corpus/sharepoint/signin",
        "/api/corpus/sharepoint/signout",
        "/api/corpus/sharepoint/refresh",
    ],
)
def test_flask_app_returns_404_for_legacy_sharepoint_url(url):
    """End-to-end: import ``app_simple.app`` and POST to each legacy
    URL.  The Flask test client must return 404 (route not
    registered) -- never 500 (would mean the route exists but its
    handler imports a deleted module) and never 200 (would mean the
    MSAL surface secretly survived)."""
    import app_simple  # type: ignore  # noqa: F401

    client = app_simple.app.test_client()
    resp = client.post(url)
    assert resp.status_code == 404, (
        f"POST {url} returned HTTP {resp.status_code}; expected 404 "
        "after Round 36 retired the SharePoint MSAL routes."
    )


def test_requirements_txt_drops_msal_and_keyring():
    """The ``msal`` and ``keyring`` dependencies were the Python
    surface for the device-code flow + token cache.  Round 36
    removed both because the runtime no longer authenticates
    against Microsoft Graph.  Pin that the requirements file
    reflects this (otherwise the .app ships with an unused 5MB
    of code, and a future regression could re-import it without
    a build error)."""
    req_path = os.path.join(_REPO_ROOT, "requirements.txt")
    with open(req_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    # Strip comment lines so a "# msal removed" annotation does not
    # trip the assertion.
    body_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    body = "\n".join(body_lines).lower()
    # Match the package name at the start of a requirement line so a
    # transitive sub-string ("msalprev") would not false-positive.
    msal_pkg = re.compile(r"^msal(\b|[<>=~!])", re.MULTILINE)
    keyring_pkg = re.compile(r"^keyring(\b|[<>=~!])", re.MULTILINE)
    assert not msal_pkg.search(body), (
        "requirements.txt still pins 'msal'; Round 36 retired the "
        "MSAL/Graph runtime path -- remove the dependency."
    )
    assert not keyring_pkg.search(body), (
        "requirements.txt still pins 'keyring'; the MSAL token cache "
        "was the only consumer and was removed in Round 36."
    )
