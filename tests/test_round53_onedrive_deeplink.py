"""Round 53 / Phase 53.4.1: pin the OneDrive deep-link UX helper.

Background
----------
When the corpus is in the new ``blocked_no_onedrive`` state the
analyze-page panel surfaces a CTA that takes the user directly to
the canonical OneDrive folder (``AI Projects/AdoptIQ_CSOne_Reports``).
Without this helper the user has to navigate manually through
OneDrive's web UI -- a poor experience the user explicitly called
out as a hindrance.

The deep link is exposed via two layers of validation:

1. ``Config.ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK`` (defaults to
   ``ADOPTIQ_CORPUS_SHARE_URL``; can be overridden via env to use
   ``odopen://`` / ``ms-onedrive://`` for one-click sync).
2. ``app_simple._r53_safe_onedrive_deep_link`` which validates the
   scheme against an allow-list (``http://``, ``https://``,
   ``odopen:``, ``ms-onedrive:``) before exposing it on the
   ``/api/corpus/status`` payload.  Anything outside the allow-list
   becomes ``None`` so a hostile env override cannot smuggle a
   ``javascript:`` URL into the panel.

These tests pin the contract, including the security-critical
allow-list.  A regression that drops the validator would re-enable
an open-redirect / XSS-via-href attack vector.
"""

from __future__ import annotations

import importlib

import pytest


# ---------------------------------------------------------------------------
# _r53_safe_onedrive_deep_link -- scheme allow-list
# ---------------------------------------------------------------------------


@pytest.fixture
def _module(monkeypatch):
    """Import app_simple.  We don't need a Flask test client here --
    the helper is a plain function."""
    import app_simple as mod
    yield mod


def _set_link(monkeypatch, value):
    """Override the live config value for a single test."""
    import config as _live_config
    monkeypatch.setattr(
        _live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK", value,
        raising=False,
    )


def test_safe_link_accepts_https(monkeypatch, _module):
    _set_link(monkeypatch, "https://cisco.sharepoint.com/personal/x/Documents/AI%20Projects")
    out = _module._r53_safe_onedrive_deep_link()
    assert out == "https://cisco.sharepoint.com/personal/x/Documents/AI%20Projects"


def test_safe_link_accepts_http(monkeypatch, _module):
    """``http://`` is on the allow-list (some legacy redirects still
    use it).  Validators that strip http would cause those legacy
    redirects to silently disappear."""
    _set_link(monkeypatch, "http://example.cisco.com/legacy-onedrive")
    assert _module._r53_safe_onedrive_deep_link() == (
        "http://example.cisco.com/legacy-onedrive"
    )


def test_safe_link_accepts_odopen_scheme(monkeypatch, _module):
    """The ``odopen://`` scheme is registered by the macOS OneDrive
    client and gives one-click sync.  Must pass the allow-list."""
    _set_link(monkeypatch, "odopen://sync?webUrl=https%3A%2F%2Fcisco.sharepoint.com%2FAdoptIQ")
    out = _module._r53_safe_onedrive_deep_link()
    assert out is not None
    assert out.startswith("odopen://")


def test_safe_link_accepts_ms_onedrive_scheme(monkeypatch, _module):
    """The ``ms-onedrive://`` scheme is registered by the Windows
    OneDrive client.  Must pass the allow-list."""
    _set_link(monkeypatch, "ms-onedrive://open?folder=AdoptIQ_CSOne_Reports")
    assert _module._r53_safe_onedrive_deep_link() == (
        "ms-onedrive://open?folder=AdoptIQ_CSOne_Reports"
    )


def test_safe_link_rejects_javascript_scheme(monkeypatch, _module):
    """Security-critical: ``javascript:`` URLs in the href would let
    a hostile env override XSS the analyze panel.  MUST be rejected."""
    _set_link(monkeypatch, "javascript:alert(document.cookie)")
    assert _module._r53_safe_onedrive_deep_link() is None


def test_safe_link_rejects_data_uri(monkeypatch, _module):
    """``data:`` URLs are another XSS vector via base64-encoded HTML."""
    _set_link(monkeypatch, "data:text/html,<script>fetch('/api/corpus/reset')</script>")
    assert _module._r53_safe_onedrive_deep_link() is None


def test_safe_link_rejects_file_uri(monkeypatch, _module):
    """``file://`` URLs would let a malicious config exfiltrate local
    files via referer leakage when the user clicks."""
    _set_link(monkeypatch, "file:///etc/passwd")
    assert _module._r53_safe_onedrive_deep_link() is None


def test_safe_link_rejects_unknown_scheme(monkeypatch, _module):
    """Anything that isn't on the explicit allow-list is rejected.
    This is the catch-all that prevents a future scheme drift from
    becoming an attack vector by default."""
    for hostile in (
        "vbscript:msgbox('boom')",
        "ftp://internal.cisco/files",
        "ssh://git@cisco/foo",
        "mailto:attacker@example.com",
        "tel:+1-555-0100",
    ):
        _set_link(monkeypatch, hostile)
        assert _module._r53_safe_onedrive_deep_link() is None, (
            f"hostile scheme {hostile!r} was not rejected"
        )


def test_safe_link_returns_none_when_unset(monkeypatch, _module):
    _set_link(monkeypatch, None)
    assert _module._r53_safe_onedrive_deep_link() is None
    _set_link(monkeypatch, "")
    assert _module._r53_safe_onedrive_deep_link() is None
    _set_link(monkeypatch, "   ")
    assert _module._r53_safe_onedrive_deep_link() is None


def test_safe_link_is_case_insensitive_for_scheme(monkeypatch, _module):
    """The allow-list match is case-insensitive (browsers normalize
    schemes); a mixed-case scheme should still pass."""
    _set_link(monkeypatch, "HTTPS://cisco.sharepoint.com/AdoptIQ")
    out = _module._r53_safe_onedrive_deep_link()
    assert out == "HTTPS://cisco.sharepoint.com/AdoptIQ", (
        "validator must preserve original casing once the scheme "
        "passes the allow-list check"
    )


# ---------------------------------------------------------------------------
# Status payload exposure
# ---------------------------------------------------------------------------


def test_status_payload_exposes_safe_deep_link(monkeypatch, _module):
    """The ``/api/corpus/status`` payload must surface the validated
    deep link under ``boot.onedrive_deep_link``."""
    _set_link(monkeypatch, "https://cisco.sharepoint.com/AdoptIQ_CSOne_Reports")
    payload = _module._r17_corpus_status_payload()
    boot = payload.get("boot", {})
    assert boot.get("onedrive_deep_link") == (
        "https://cisco.sharepoint.com/AdoptIQ_CSOne_Reports"
    )


def test_status_payload_strips_javascript_link(monkeypatch, _module):
    """Even if the env smuggles in a ``javascript:`` URL, the status
    payload's serializer must replace it with ``None`` so the panel
    never renders an exploit href."""
    _set_link(monkeypatch, "javascript:alert(1)")
    payload = _module._r17_corpus_status_payload()
    boot = payload.get("boot", {})
    assert boot.get("onedrive_deep_link") is None, (
        "status payload exposed a javascript: URL -- this is the "
        "Round 53 / Phase 53.4.1 XSS regression."
    )


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------


def test_config_exposes_round53_deep_link_constant():
    """``Config.ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK`` must be defined
    so the env override path works.  A regression that removes the
    constant would silently fall back to ``None`` everywhere."""
    import config as _live_config
    assert hasattr(_live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK"), (
        "Round 53 / Phase 53.4.1: Config must define "
        "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK so the env override path "
        "and the deep-link CTA can find a value to render."
    )


def test_config_deep_link_defaults_to_share_url():
    """When ``ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK`` env var is not set,
    the constant must fall through to ``ADOPTIQ_CORPUS_SHARE_URL`` --
    so installs that have already configured the share URL get the
    deep link for free without an extra config step.

    This test re-imports config under a clean env to assert the
    fallback shape; we cannot rely on the live module's value because
    earlier tests may have monkey-patched it.
    """
    import os
    # Snapshot current env so we can restore after the test.
    original_env = os.environ.pop("ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK", None)
    try:
        import config as _live_config
        importlib.reload(_live_config)
        deep = getattr(_live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK", None)
        share = getattr(_live_config.Config, "ADOPTIQ_CORPUS_SHARE_URL", None)
        assert deep == share, (
            f"deep-link must default to share-URL when env var is "
            f"unset; got deep={deep!r} share={share!r}"
        )
    finally:
        if original_env is not None:
            os.environ["ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK"] = original_env
        # Reload the module again so the in-process constant matches
        # the original env state for any subsequent test.
        import config as _live_config
        importlib.reload(_live_config)
