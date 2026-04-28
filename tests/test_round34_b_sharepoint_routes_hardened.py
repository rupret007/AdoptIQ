"""Round 34 / B -- harden the new Build8 SharePoint POST routes.

Build8 originally added three POST routes for the user-facing
SharePoint panel:

  * ``POST /api/corpus/sharepoint/signin``
  * ``POST /api/corpus/sharepoint/signout``
  * ``POST /api/settings/sharepoint_url``  (RETIRED in Round 35)

They were CSRF-protected via ``_r17_2_authorize_corpus_admin`` but
were NOT in ``_SENSITIVE_ENDPOINTS``, so they bypassed:

  - the localhost-only gate (``restrict_sensitive_routes_to_localhost``)
  - the response-hardening headers (Cache-Control: no-store,
    X-Content-Type-Options: nosniff, X-Frame-Options: DENY,
    Referrer-Policy: no-referrer, per-response CSP)

Round 34 / B1 added them to ``_SENSITIVE_ENDPOINTS``.

Round 35 / native-corpus retired the per-user URL paste UI, so the
``/api/settings/sharepoint_url`` route is gone.  This file now
exercises only the two remaining sign-in / sign-out routes; the
SharePoint URL allow-list helper (``is_valid_sharepoint_url``)
stays exported as a defense-in-depth helper for any caller that
still needs to validate a tenant URL (e.g. an env-overridden
``ADOPTIQ_CORPUS_SHARE_URL``).

Round 34 / B2 also adds Host-header validation as defense-in-depth
against DNS rebinding -- an attacker tab on ``evil.com`` whose DNS
flips to 127.0.0.1 still has ``request.remote_addr == 127.0.0.1``,
so the existing IP gate alone does not block it.  The Host header
the browser actually sent (``evil.com``) is the only invariant the
attacker cannot forge from a same-origin fetch.

These tests are deterministic and offline.  They use the Flask test
client so no real socket / DNS / Graph traffic happens.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# B1 -- routes are now in the sensitive set
# ---------------------------------------------------------------------------


def test_b1_two_remaining_sharepoint_routes_in_sensitive_endpoints():
    """The two surviving Build8 routes (sign-in / sign-out) must be
    in ``_SENSITIVE_ENDPOINTS`` so they pick up the localhost gate
    AND the response headers.

    Round 35 retired ``api_settings_sharepoint_url`` (URL paste UI is
    gone), so it is no longer in the sensitive set.
    """
    import app_simple

    sensitive = app_simple._SENSITIVE_ENDPOINTS
    for endpoint in (
        "api_corpus_sharepoint_signin",
        "api_corpus_sharepoint_signout",
    ):
        assert endpoint in sensitive, (
            f"endpoint {endpoint!r} missing from _SENSITIVE_ENDPOINTS; "
            f"would bypass localhost gate + security headers"
        )
    # And the retired endpoint must NOT be in the set -- if a future
    # round re-adds the route, it should be re-hardened explicitly.
    assert "api_settings_sharepoint_url" not in sensitive, (
        "Round 35 retired api_settings_sharepoint_url; resurrecting "
        "it in _SENSITIVE_ENDPOINTS without re-registering the route "
        "leaks a phantom endpoint name into the gate."
    )


def test_b1_retired_sharepoint_url_route_is_gone(client, app):
    """Round 35: the URL paste route is retired.  POST against the
    legacy path must 404 -- header hardening is moot when the route
    no longer exists."""
    app.config["WTF_CSRF_ENABLED"] = False
    resp = client.post(
        "/api/settings/sharepoint_url",
        json={"url": ""},
    )
    assert resp.status_code in (404, 405), (
        f"Round 35 retired POST /api/settings/sharepoint_url; "
        f"expected 404/405, got {resp.status_code}"
    )


def test_b1_sharepoint_signin_response_carries_security_headers(client, app):
    app.config["WTF_CSRF_ENABLED"] = False
    resp = client.post("/api/corpus/sharepoint/signin")
    assert resp.headers.get("Cache-Control") == "no-store"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"


def test_b1_sharepoint_signout_response_carries_security_headers(client, app):
    app.config["WTF_CSRF_ENABLED"] = False
    resp = client.post("/api/corpus/sharepoint/signout")
    assert resp.headers.get("Cache-Control") == "no-store"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


# ---------------------------------------------------------------------------
# B2 -- Host-header validation (DNS-rebinding defense)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host_header",
    [
        "127.0.0.1",
        "127.0.0.1:5151",
        "localhost",
        "localhost:5151",
        "localhost:9999",  # any port on localhost is fine
        "[::1]",
        "[::1]:5151",
    ],
)
def test_b2_allowed_hosts_pass(client, app, host_header):
    """All loopback / localhost hostnames (with or without port) must
    pass the Host gate so the legitimate browser flow keeps working.

    Probe ``/history`` because it's in ``_SENSITIVE_ENDPOINTS`` and
    therefore subject to the Host gate.  ``/api/intel/status`` is
    intentionally NOT in the sensitive set (the JS poller hits it
    every 5-60s and a 403 there would loop the UI), so it would not
    exercise the gate."""
    app.config["WTF_CSRF_ENABLED"] = False
    resp = client.get(
        "/history",
        headers={"Host": host_header},
    )
    # ``/history`` returns 200 (or a render) for legitimate hosts.
    # We only care that it's NOT the 403 the Host gate would emit.
    assert resp.status_code != 403, (
        f"Host={host_header!r} unexpectedly Host-rejected: {resp.status_code}"
    )


@pytest.mark.parametrize(
    "host_header",
    [
        "evil.com",
        "evil.com:5151",
        "attacker.example.org",
        "127.0.0.1.attacker.tld",  # subdomain confusion
        "127-0-0-1.evil.com",
    ],
)
def test_b2_unexpected_host_blocked_on_sensitive_endpoint(client, app, host_header):
    """DNS-rebinding scenario: TCP peer is 127.0.0.1 (so the legacy
    ``_is_local_client`` check passes) but the browser sent a non-
    loopback Host header.  The new gate must reject with 403."""
    app.config["WTF_CSRF_ENABLED"] = False
    resp = client.get(
        "/history",
        headers={"Host": host_header},
    )
    assert resp.status_code == 403, (
        f"Host={host_header!r} should be rejected by the rebinding "
        f"gate, got {resp.status_code}"
    )
    body = resp.get_json() or {}
    assert "host" in (body.get("error") or "").lower(), (
        f"403 body must mention Host header; got {body!r}"
    )


def test_b2_host_allowlist_env_extends_set(client, app, monkeypatch):
    """Operators who bind to a LAN IP can extend the allow-list via
    ``ADOPTIQ_HOST_ALLOWLIST`` so the production deployment still
    answers requests with the operator's chosen Host header."""
    app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setenv("ADOPTIQ_HOST_ALLOWLIST", "intranet.example.com,10.0.0.5")

    resp = client.get(
        "/history",
        headers={"Host": "intranet.example.com"},
    )
    assert resp.status_code != 403

    resp2 = client.get(
        "/history",
        headers={"Host": "10.0.0.5:5151"},
    )
    assert resp2.status_code != 403


def test_b2_bind_host_env_is_honored(client, app, monkeypatch):
    """``ADOPTIQ_BIND_HOST`` is the operator's explicit declaration of
    where they pinned the listener.  That value must pass the Host
    gate without needing a separate ALLOWLIST entry."""
    app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setenv("ADOPTIQ_BIND_HOST", "10.20.30.40")

    resp = client.get(
        "/history",
        headers={"Host": "10.20.30.40:5151"},
    )
    assert resp.status_code != 403


def test_b2_public_ui_shells_unaffected_by_host_check(client, app):
    """The Host gate must apply ONLY to sensitive endpoints.  The
    public UI shell ``/`` must remain reachable from any Host (so a
    LAN preview or port-forward keeps rendering)."""
    app.config["WTF_CSRF_ENABLED"] = False
    resp = client.get(
        "/",
        headers={"Host": "lab.evil.com"},
    )
    # ``/`` is not in _SENSITIVE_ENDPOINTS; should be served.
    assert resp.status_code == 200


def test_b2_host_gate_protects_remaining_sharepoint_post_routes(client, app):
    """End-to-end pin: the two surviving Build8 POST routes that area
    B added to ``_SENSITIVE_ENDPOINTS`` must reject a hostile Host
    header even when CSRF is disabled (test mode).  Without this
    gate, a DNS-rebinding tab could drive the SharePoint sign-in
    flow.

    Round 35 retired ``/api/settings/sharepoint_url``; it is now a
    404 regardless of Host header (covered by
    ``test_b1_retired_sharepoint_url_route_is_gone``).
    """
    app.config["WTF_CSRF_ENABLED"] = False
    for path in (
        "/api/corpus/sharepoint/signin",
        "/api/corpus/sharepoint/signout",
    ):
        resp = client.post(
            path,
            json={"url": ""},
            headers={"Host": "evil.com"},
        )
        assert resp.status_code == 403, (
            f"{path} accepted Host=evil.com (status={resp.status_code}); "
            f"DNS-rebinding gate did not fire"
        )


# ---------------------------------------------------------------------------
# B-misc -- existing SharePoint URL allow-list still rejects attack vectors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # IDN homoglyph (Cyrillic с in 'сontoso')
        "https://сontoso.sharepoint.com/path",
        # Userinfo injection
        "https://contoso.sharepoint.com@evil.com/path",
        "https://evil.com@contoso.sharepoint.com/path",
        # Subdomain confusion -- multi-level domain
        "https://evil.sharepoint.com.attacker.tld/path",
        # Bare host (no path)
        "https://contoso.sharepoint.com",
        "https://contoso.sharepoint.com/",
        # HTTP (not HTTPS)
        "http://contoso.sharepoint.com/path",
        # NUL byte
        "https://contoso.sharepoint.com/path\x00",
        # Newline injection
        "https://contoso.sharepoint.com/path\nhttps://other",
        # URL-encoded port
        "https://contoso.sharepoint.com%3A8443/path",
        # Literal port
        "https://contoso.sharepoint.com:8443/path",
        # Wrong host
        "https://example.com/path",
    ],
)
def test_b_settings_url_validator_rejects_attack_vectors(url):
    """The Build8 ``is_valid_sharepoint_url`` allow-list regex must
    reject every attack vector listed in the prompt."""
    import adoptiq_settings

    # The bare-host cases need the trailing ``/`` (or ``/`` only) to
    # be rejected.  Verify both shapes here.
    assert not adoptiq_settings.is_valid_sharepoint_url(url), (
        f"validator unexpectedly accepted {url!r}"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://contoso.sharepoint.com/sites/AdoptIQ_CSOne_Reports",
        "https://contoso.sharepoint.com/sites/team/Shared%20Documents/Folder",
        "https://my-tenant-1.sharepoint.com/personal/user_contoso_com/Documents/AdoptIQ",
        "",  # Empty is "unset" and is allowed.
    ],
)
def test_b_settings_url_validator_accepts_legitimate(url):
    import adoptiq_settings

    assert adoptiq_settings.is_valid_sharepoint_url(url), (
        f"validator unexpectedly rejected {url!r}"
    )


def test_b_settings_url_validator_rejects_oversized_url():
    """2 KiB cap defends against DoS / log-flooding via giant URLs."""
    import adoptiq_settings

    assert not adoptiq_settings.is_valid_sharepoint_url(
        "https://contoso.sharepoint.com/" + ("a" * 2100)
    )
