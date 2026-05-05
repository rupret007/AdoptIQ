"""Round 85 / Build 61: URL-shape rotation + Preferences-hub card.

Pin the four contracts of Round 85:

A. The new ``:f:/p/`` guest-pass URL passes the canonical
   ``adoptiq_settings._is_valid_sharepoint_url`` allow-list (so the
   POST endpoint and the resolver tiers can persist / read it
   without falling through to the next tier).

B. The R84 three-tier resolver
   (``corpus_share_url_resolver.get_active_corpus_share_url``)
   returns the new URL with source label ``"config.py"`` when no
   settings.json override and no env override are set.

C. The R83 ``app_simple._r83_safe_share_url`` defense-in-depth
   wrapper passes the new URL through untouched (https-only +
   2048-byte cap both clear).

D. The Preferences hub (``templates/preferences.html``) carries
   the same ``[data-corpus-share-url-card]`` markers as the
   analyze-page card AND wires ``static/js/corpus_share_url.js``
   so the existing R84 IIFE module binds without a second handler.

Source-shape pin only -- runtime behaviour of the R84 endpoint
(``POST /api/settings/corpus-share-url``) is unchanged and stays
covered by ``tests/test_round84_corpus_share_url_endpoint.py``.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


# The Round 85 canonical default.  Sourced separately from the test
# fixtures in ``tests/test_round35_corpus_url_hardcoded.py`` and
# ``tests/test_round81_sharepoint_url_refresh.py`` so a future
# rotation that drifts the three pins apart fails LOUDLY here too.
_R85_NEW_URL = (
    "https://cisco-my.sharepoint.com/:f:/p/jestory/"
    "IgBm46pU_P9aTpkxyEQ13ZgYAT4BhGVKNfcUsN7DA7zkRJI"
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PREFERENCES_TEMPLATE = _PROJECT_ROOT / "templates" / "preferences.html"
_ANALYZE_TEMPLATE = _PROJECT_ROOT / "templates" / "analyze.html"


def _reload_config():
    import config as _cfg
    return importlib.reload(_cfg).Config


# ---------------------------------------------------------------------------
# Section A: validator gates the new URL
# ---------------------------------------------------------------------------


def test_r85_new_url_passes_validator_allowlist():
    """The new ``:f:/p/`` guest-pass URL must satisfy the canonical
    SharePoint validator (HTTPS, ``*.sharepoint.com`` host, non-empty
    path, <=2048 bytes).  If this fails, either the URL drifted or
    the validator regressed."""
    import adoptiq_settings as _s
    assert _s._is_valid_sharepoint_url(_R85_NEW_URL) is True, (
        f"validator rejected the Round 85 default URL {_R85_NEW_URL!r}"
    )


def test_r85_new_url_passes_public_validator_alias():
    """External callers use the public alias; it must agree with the
    private validator on the new URL."""
    import adoptiq_settings as _s
    assert _s.is_valid_sharepoint_url(_R85_NEW_URL) is True


def test_r85_new_url_is_under_2048_bytes():
    """Defense-in-depth: pin the URL length cap so a future rotation
    that lands a runaway path cannot bloat ``settings.json``."""
    assert len(_R85_NEW_URL.encode("utf-8")) <= 2048


# ---------------------------------------------------------------------------
# Section B: resolver returns the new URL with source ``"config.py"``
# ---------------------------------------------------------------------------


def test_r85_resolver_returns_new_url_when_no_overrides(monkeypatch, tmp_path):
    """R84 resolver contract: when neither settings.json nor the env
    var is set, the resolver returns ``Config.ADOPTIQ_CORPUS_SHARE_URL``
    with source label ``"config.py"``.  Round 85 pins the value to the
    new ``:f:/p/`` URL."""
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    # Point adoptiq_settings at a fresh tmp dir so any settings.json
    # on the developer's machine cannot ghost-override this assertion.
    import adoptiq_settings as _settings
    monkeypatch.setattr(_settings, "_app_support_dir", lambda: tmp_path)
    _reload_config()
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    url, source = _r.get_active_corpus_share_url()
    assert url == _R85_NEW_URL
    assert source == _r.SOURCE_CONFIG


def test_r85_resolver_env_tier_still_wins_over_config(monkeypatch, tmp_path):
    """Round 85 does NOT change the resolver precedence -- the env
    override still wins over the config.py default.  Pin this so a
    future "simplify the resolver" refactor cannot accidentally
    invert the precedence."""
    import adoptiq_settings as _settings
    monkeypatch.setattr(_settings, "_app_support_dir", lambda: tmp_path)
    override = "https://contoso.sharepoint.com/sites/Marketing/Shared%20Docs"
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", override)
    _reload_config()
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    url, source = _r.get_active_corpus_share_url()
    assert url == override
    assert source == _r.SOURCE_ENV


# ---------------------------------------------------------------------------
# Section C: ``_r83_safe_share_url`` accepts the new URL untouched
# ---------------------------------------------------------------------------


def test_r85_safe_share_url_returns_new_url_unchanged(monkeypatch):
    """The R83 https-only + 2048-byte-cap defense-in-depth wrapper
    must let the new URL through unchanged.  Pinned because the wrapper
    has its own scheme + length checks separate from the validator
    upstream; a regression in either could surface here first."""
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    _reload_config()
    import importlib as _il
    import corpus_share_url_resolver as _r
    _il.reload(_r)
    import app_simple
    _il.reload(app_simple)
    result = app_simple._r83_safe_share_url()
    assert result == _R85_NEW_URL


# ---------------------------------------------------------------------------
# Section D: Preferences template carries the share-URL card markers
# ---------------------------------------------------------------------------


def _read_preferences_html() -> str:
    assert _PREFERENCES_TEMPLATE.exists(), (
        f"templates/preferences.html missing at {_PREFERENCES_TEMPLATE}"
    )
    return _PREFERENCES_TEMPLATE.read_text(encoding="utf-8")


def _read_analyze_html() -> str:
    assert _ANALYZE_TEMPLATE.exists(), (
        f"templates/analyze.html missing at {_ANALYZE_TEMPLATE}"
    )
    return _ANALYZE_TEMPLATE.read_text(encoding="utf-8")


@pytest.mark.parametrize("marker", [
    "data-corpus-share-url-card",
    "data-corpus-share-url-input",
    "data-corpus-share-url-save",
    "data-corpus-share-url-test",
    "data-corpus-share-url-clear",
    "data-corpus-share-url-current",
    "data-corpus-share-url-source-pill",
    "data-corpus-share-url-source-text",
    "data-corpus-share-url-feedback",
])
def test_r85_preferences_template_carries_card_markers(marker):
    """Preferences hub mirror of the R84 analyze-page card: every
    ``data-corpus-share-url-*`` marker the JS module reads MUST be
    present so the existing IIFE binds without a second handler."""
    html = _read_preferences_html()
    assert marker in html, (
        f"templates/preferences.html missing marker {marker!r}"
    )


def test_r85_preferences_template_wires_corpus_share_url_js():
    """The Preferences page must wire ``corpus_share_url.js`` via
    the standard ``url_for`` Jinja helper so the IIFE can bind.  Pin
    both the script reference AND the ``url_for`` form so a relative
    ``/static/...`` shortcut (which would break in a sub-path mount)
    fails this test."""
    html = _read_preferences_html()
    assert "corpus_share_url.js" in html, (
        "templates/preferences.html does not wire corpus_share_url.js"
    )
    assert "url_for('static', filename='js/corpus_share_url.js')" in html, (
        "templates/preferences.html must reference corpus_share_url.js "
        "via the Jinja url_for helper"
    )


def test_r85_analyze_template_card_unchanged():
    """Regression guard: the R84 analyze-page card must still carry
    its markers so adding the Preferences mirror has not accidentally
    deleted it."""
    html = _read_analyze_html()
    assert "data-corpus-share-url-card" in html, (
        "Round 85 regression: analyze-page card markers were removed"
    )
    assert "corpus_share_url.js" in html, (
        "Round 85 regression: analyze-page no longer wires "
        "corpus_share_url.js"
    )


# ---------------------------------------------------------------------------
# Section E: cross-pin against the R35 + R81 fixtures
# ---------------------------------------------------------------------------


def test_r85_url_matches_round35_pin():
    """Cross-pin: the URL constant in this file MUST equal the
    constant pinned in ``tests/test_round35_corpus_url_hardcoded.py``
    -- if the two drift apart, a future rotation that updates only
    one of the three pins will leave a stale assertion in the suite."""
    from tests import test_round35_corpus_url_hardcoded as _r35
    assert _R85_NEW_URL == _r35._EXPECTED_DEFAULT, (
        f"Round 85 URL pins drifted between test files: "
        f"r85={_R85_NEW_URL!r} r35={_r35._EXPECTED_DEFAULT!r}"
    )


def test_r85_url_matches_round81_pin():
    """Cross-pin: the URL constant in this file MUST equal the
    constant pinned in ``tests/test_round81_sharepoint_url_refresh.py``
    (renamed-in-place to round85_* test functions)."""
    from tests import test_round81_sharepoint_url_refresh as _r81
    assert _R85_NEW_URL == _r81._EXPECTED_FULL_URL, (
        f"Round 85 URL pins drifted between test files: "
        f"r85={_R85_NEW_URL!r} r81={_r81._EXPECTED_FULL_URL!r}"
    )
