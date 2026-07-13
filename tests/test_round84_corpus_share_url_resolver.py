"""Round 84 / Build 60: ``corpus_share_url_resolver`` precedence
contract coverage.

Coverage matrix (C3 in the R84 plan):

* Resolver returns ``(url, "settings.json")`` when settings.json has
  the key.
* Resolver returns ``(url, "env")`` when env set, settings.json
  absent.
* Resolver returns ``(url, "config.py")`` for the hardcoded default.
* Invalid settings.json value falls through to env (defense-in-depth).
* Invalid env value falls through to config.py.
* No caching: changing settings.json between calls returns the new
  value.
* Source labels are stable strings (pinned constants).

Mirrors the R69 ``model_resolver`` test pattern. The resolver MUST
NOT cache so a UI flip on the analyze-page card takes effect on the
very next ``/api/corpus/bootstrap-shortcut`` click without a process
restart.
"""

# Round 84 / Build 60

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Section 1: source label constants
# ---------------------------------------------------------------------------


def test_r84_resolver_source_labels_are_stable_strings():
    """The three source labels MUST be exposed as module-level
    constants so callers (the UI source pill, the GET endpoint, the
    audit log) all reference the same strings."""
    import corpus_share_url_resolver as _r
    assert _r.SOURCE_SETTINGS == "settings.json"
    assert _r.SOURCE_ENV == "env"
    assert _r.SOURCE_CONFIG == "config.py"


def test_r84_resolver_get_active_returns_2_tuple():
    """API contract: ``get_active_corpus_share_url`` returns
    ``(url, source)`` -- callers depend on the 2-tuple shape."""
    import corpus_share_url_resolver as _r
    result = _r.get_active_corpus_share_url()
    assert isinstance(result, tuple)
    assert len(result) == 2
    url, source = result
    assert url is None or isinstance(url, str)
    assert isinstance(source, str)


# ---------------------------------------------------------------------------
# Section 2: precedence (settings > env > config.py)
# ---------------------------------------------------------------------------


def test_r84_resolver_settings_layer_wins_over_env(monkeypatch, tmp_path):
    """When settings.json carries a valid override, it MUST win over
    the env layer."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    settings_url = "https://contoso.sharepoint.com/sites/team?e=settings"
    env_url = "https://acme.sharepoint.com/sites/foo?e=env"
    _s.save_settings({"corpus_share_url": settings_url})
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", env_url)
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    url, source = _r.get_active_corpus_share_url()
    assert url == settings_url
    assert source == "settings.json"


def test_r84_resolver_env_layer_wins_over_config_default(monkeypatch, tmp_path):
    """With no settings.json override but env set, the env URL MUST
    win over the hardcoded config default."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    env_url = "https://contoso.sharepoint.com/sites/team?e=envonly"
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", env_url)
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    url, source = _r.get_active_corpus_share_url()
    assert url == env_url
    assert source == "env"


def test_r84_resolver_falls_back_to_config_default_when_unset(monkeypatch, tmp_path):
    """With no override anywhere, the resolver MUST hit
    ``Config.ADOPTIQ_CORPUS_SHARE_URL``."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    url, source = _r.get_active_corpus_share_url()
    assert url is not None
    assert url.startswith("https://")
    assert source == "config.py"


# ---------------------------------------------------------------------------
# Section 3: defense-in-depth fall-through on invalid values
# ---------------------------------------------------------------------------


def test_r84_resolver_invalid_settings_value_falls_through_to_env(monkeypatch, tmp_path):
    """If settings.json on disk has a malformed URL (e.g. a future
    schema bug or a hand-edited file), the resolver MUST fall
    through to the env layer rather than return the malformed value.

    ``load_settings`` already drops invalid values so the resolver
    sees an absent key, but we still want the resolver to be
    defensive: the test simulates a hand-edit by writing the bad
    value DIRECTLY to disk (bypassing the validator)."""
    import adoptiq_settings as _s
    import json as _json
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    target = tmp_path / _s.SETTINGS_FILENAME
    tmp_path.mkdir(parents=True, exist_ok=True)
    target.write_text(_json.dumps({"corpus_share_url": "javascript:alert(1)"}))
    env_url = "https://contoso.sharepoint.com/sites/team?e=falltohere"
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", env_url)
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    url, source = _r.get_active_corpus_share_url()
    assert url == env_url
    assert source == "env"


def test_r84_resolver_invalid_env_falls_through_to_config_default(monkeypatch, tmp_path):
    """A typo'd env var (e.g. ``http://`` instead of ``https://``)
    MUST not propagate -- the resolver falls through to the config
    default."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", "http://cisco-my.sharepoint.com/foo")
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    url, source = _r.get_active_corpus_share_url()
    # Falls through to config.py default (still https-based).
    assert url is not None
    assert url.startswith("https://")
    assert source == "config.py"


def test_r84_resolver_handles_total_exhaustion_gracefully(monkeypatch, tmp_path):
    """If every tier fails (settings.json malformed, env malformed,
    Config import broken), the resolver MUST return
    ``(None, "config.py")`` rather than raise."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", "javascript:alert(1)")
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    # Stub the config-tier reader so even the hardcoded default fails.
    monkeypatch.setattr(_r, "_read_config_value", lambda: None)
    url, source = _r.get_active_corpus_share_url()
    assert url is None
    assert source == "config.py"


# ---------------------------------------------------------------------------
# Section 4: no caching contract
# ---------------------------------------------------------------------------


def test_r84_resolver_does_not_cache_settings_value(monkeypatch, tmp_path):
    """A UI flip mid-process MUST take effect on the next call
    (mirrors R69 model_resolver). The resolver re-reads
    settings.json on every invocation."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    url1 = "https://contoso.sharepoint.com/sites/one?e=t1"
    url2 = "https://contoso.sharepoint.com/sites/two?e=t2"
    _s.save_settings({"corpus_share_url": url1})
    got1, src1 = _r.get_active_corpus_share_url()
    assert got1 == url1
    assert src1 == "settings.json"
    _s.save_settings({"corpus_share_url": url2})
    # Same process, fresh resolution -> sees the flipped value.
    got2, src2 = _r.get_active_corpus_share_url()
    assert got2 == url2
    assert src2 == "settings.json"


def test_r84_resolver_does_not_cache_env_value(monkeypatch, tmp_path):
    """Env-tier changes between calls MUST also take effect on the
    next invocation."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    url1 = "https://contoso.sharepoint.com/sites/one?e=env1"
    url2 = "https://contoso.sharepoint.com/sites/two?e=env2"
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", url1)
    got1, src1 = _r.get_active_corpus_share_url()
    assert got1 == url1
    assert src1 == "env"
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", url2)
    got2, src2 = _r.get_active_corpus_share_url()
    assert got2 == url2
    assert src2 == "env"


# ---------------------------------------------------------------------------
# Section 5: _r83_safe_share_url integration (R83 contract preserved)
# ---------------------------------------------------------------------------


def test_r84_safe_share_url_returns_settings_value_when_persisted(monkeypatch, tmp_path):
    """R83's ``_r83_safe_share_url`` MUST honour the resolver: when
    settings.json carries an override, the bootstrap-shortcut endpoint
    surfaces the override (not the config default)."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    canonical = "https://contoso.sharepoint.com/sites/team?e=settingsval"
    _s.save_settings({"corpus_share_url": canonical})
    import corpus_share_url_resolver as _r
    importlib.reload(_r)
    import app_simple as _app
    importlib.reload(_app)
    safe = _app._r83_safe_share_url()
    assert safe == canonical


def test_r84_safe_share_url_still_filters_non_https_from_resolver(monkeypatch, tmp_path):
    """Defense-in-depth: even if a future resolver bug let a non-https
    value through, ``_r83_safe_share_url`` MUST still reject it via
    its own scheme allow-list. Tests the R83 contract is preserved.

    Stubs the resolver to return a malformed URL and asserts
    ``_r83_safe_share_url`` returns None."""
    import corpus_share_url_resolver as _r
    monkeypatch.setattr(
        _r,
        "get_active_corpus_share_url",
        lambda: ("ftp://cisco-my.sharepoint.com/foo", "settings.json"),
    )
    import app_simple as _app
    safe = _app._r83_safe_share_url()
    assert safe is None
