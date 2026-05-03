"""Round 71 / Phase 1 (#5) -- IP spoof bypass closed.

Pre-R71 ``_client_ip_from_request`` honored ``X-Forwarded-For`` whenever
``ADOPTIQ_TRUST_PROXY_HEADERS=1`` was set, regardless of whether the
direct TCP peer was actually a known reverse proxy.  Once an operator
flipped the flag for a legitimate proxy deployment, ANY direct client
could spoof its IP by sending its own XFF header (e.g.
``X-Forwarded-For: 127.0.0.1``) and pass the localhost gate.

Round 71 / Phase 1 (#5) requires the direct ``REMOTE_ADDR`` to match
either ``ADOPTIQ_TRUSTED_PROXY_IPS`` (preferred) or be a loopback peer
(legacy back-compat) before XFF is honored.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import mock


def _make_req(remote_addr: str, headers: dict | None = None) -> SimpleNamespace:
    """Build a stub Flask-like request with REMOTE_ADDR + headers dict."""
    headers = headers or {}
    return SimpleNamespace(
        environ={"REMOTE_ADDR": remote_addr},
        remote_addr=remote_addr,
        headers=headers,
    )


def test_round71_xff_ignored_when_remote_addr_not_trusted_proxy() -> None:
    """Default deploy: no proxy IPs, no legacy flag -> XFF is IGNORED.

    A LAN attacker with a forged ``X-Forwarded-For: 127.0.0.1`` header
    must NOT bypass the localhost gate.
    """
    from app_simple import _client_ip_from_request

    req = _make_req("10.0.0.42", {"X-Forwarded-For": "127.0.0.1"})
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADOPTIQ_TRUSTED_PROXY_IPS", None)
        os.environ.pop("ADOPTIQ_TRUST_PROXY_HEADERS", None)
        ip = _client_ip_from_request(req)
    assert ip == "10.0.0.42", (
        f"XFF must be ignored when neither ADOPTIQ_TRUSTED_PROXY_IPS nor "
        f"ADOPTIQ_TRUST_PROXY_HEADERS is set; got {ip!r}, expected the "
        "raw REMOTE_ADDR (10.0.0.42)."
    )


def test_round71_xff_honored_when_remote_addr_in_trusted_proxy_list() -> None:
    """When the direct peer IS a known proxy IP, XFF must be honored."""
    from app_simple import _client_ip_from_request

    req = _make_req("10.0.0.7", {"X-Forwarded-For": "203.0.113.55, 10.0.0.7"})
    with mock.patch.dict(os.environ, {"ADOPTIQ_TRUSTED_PROXY_IPS": "10.0.0.7,10.0.0.8"}):
        ip = _client_ip_from_request(req)
    assert ip == "203.0.113.55", (
        f"XFF must be honored when REMOTE_ADDR is in ADOPTIQ_TRUSTED_PROXY_IPS; "
        f"got {ip!r}, expected the leftmost XFF token (203.0.113.55)."
    )


def test_round71_xff_ignored_when_remote_addr_not_in_trusted_proxy_list() -> None:
    """Even with ADOPTIQ_TRUSTED_PROXY_IPS set, an UN-trusted peer must
    not be able to spoof via XFF."""
    from app_simple import _client_ip_from_request

    req = _make_req("10.0.0.99", {"X-Forwarded-For": "8.8.8.8"})
    with mock.patch.dict(os.environ, {"ADOPTIQ_TRUSTED_PROXY_IPS": "10.0.0.7,10.0.0.8"}):
        ip = _client_ip_from_request(req)
    assert ip == "10.0.0.99", (
        f"XFF must be IGNORED when REMOTE_ADDR is NOT in the trusted-proxy "
        f"list; got {ip!r}, expected REMOTE_ADDR (10.0.0.99)."
    )


def test_round71_legacy_trust_only_honors_xff_for_loopback_peer() -> None:
    """The legacy ``ADOPTIQ_TRUST_PROXY_HEADERS=1`` path must require a
    loopback REMOTE_ADDR before honoring XFF.  This is the back-compat
    path for developers testing a reverse proxy locally."""
    from app_simple import _client_ip_from_request

    req_loopback = _make_req("127.0.0.1", {"X-Forwarded-For": "203.0.113.5"})
    req_lan = _make_req("10.0.0.42", {"X-Forwarded-For": "203.0.113.5"})

    with mock.patch.dict(
        os.environ,
        {"ADOPTIQ_TRUST_PROXY_HEADERS": "1"},
    ):
        # Drop the trusted-proxy var to take the legacy code path.
        os.environ.pop("ADOPTIQ_TRUSTED_PROXY_IPS", None)
        ip_loopback = _client_ip_from_request(req_loopback)
        ip_lan = _client_ip_from_request(req_lan)

    assert ip_loopback == "203.0.113.5", (
        "Legacy trust path must honor XFF when the direct peer is loopback."
    )
    assert ip_lan == "10.0.0.42", (
        "Legacy trust path must IGNORE XFF when the direct peer is NOT loopback; "
        "this was the spoof bypass closed in Round 71 / Phase 1 (#5)."
    )


def test_round71_ipv6_loopback_allowed_for_legacy_trust() -> None:
    """IPv6 loopback ``::1`` is a valid loopback origin too."""
    from app_simple import _client_ip_from_request

    req = _make_req("::1", {"X-Forwarded-For": "203.0.113.99"})
    with mock.patch.dict(os.environ, {"ADOPTIQ_TRUST_PROXY_HEADERS": "1"}):
        os.environ.pop("ADOPTIQ_TRUSTED_PROXY_IPS", None)
        ip = _client_ip_from_request(req)
    assert ip == "203.0.113.99", (
        f"IPv6 loopback (::1) must be a valid 'origin loopback' for the "
        f"legacy trust path; got {ip!r}."
    )
