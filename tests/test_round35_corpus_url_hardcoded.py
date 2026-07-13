"""Round 35 / native-corpus: pin the hardcoded corpus share URL.

Verifies that:
* :data:`Config.ADOPTIQ_CORPUS_SHARE_URL` is defined and points at the
  expected ``cisco-my.sharepoint.com/:f:/r/personal/jestory_cisco_com``
  share when no env override is set.
* :data:`Config.ADOPTIQ_SHAREPOINT_FOLDER_URL` is now a back-compat
  alias that defaults to the same value (so existing corpus_bootstrap
  consumers keep working without code churn).
* ``adoptiq_settings.load_settings`` no longer accepts
  ``sharepoint_folder_url``; legacy values are stripped on read.
* ``adoptiq_settings.is_valid_sharepoint_url`` is still exported
  (defense-in-depth helper).
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest


# Round 85 / Build 61: rotated to a NEW SharePoint share format -- a
# ``:f:/p/`` guest-pass URL with no query string and no rotating
# ``e=...`` token.  This is fundamentally different from the legacy
# render-link shape (R35 -> R81 -> Build 60) which carried
# ``/personal/jestory_cisco_com/Documents/...?csf=1&web=1&e=<token>``.
# The legacy shape regression guard now lives in
# ``tests/test_round81_sharepoint_url_refresh.py`` (renamed-in-place
# to round85_* test functions) and rejects every legacy fragment
# (``e=O3a4Ij``, ``e=kpHMgs``, ``/personal/jestory_cisco_com``,
# ``csf=1``) so a future cherry-pick / merge cannot silently restore
# either of the older URL shapes.
_EXPECTED_DEFAULT = (
    "https://cisco-my.sharepoint.com/:f:/p/jestory/"
    "IgBm46pU_P9aTpkxyEQ13ZgYAT4BhGVKNfcUsN7DA7zkRJI"
)


def _reload_config():
    import config as _cfg
    return importlib.reload(_cfg).Config


def test_corpus_share_url_constant_exists_with_expected_default(monkeypatch):
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    assert hasattr(cfg, "ADOPTIQ_CORPUS_SHARE_URL"), (
        "Config.ADOPTIQ_CORPUS_SHARE_URL must be defined for Round 35"
    )
    assert cfg.ADOPTIQ_CORPUS_SHARE_URL == _EXPECTED_DEFAULT


def test_corpus_share_url_env_override_wins(monkeypatch):
    override = (
        "https://contoso.sharepoint.com/sites/Marketing/Shared%20Docs"
    )
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", override)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    assert cfg.ADOPTIQ_CORPUS_SHARE_URL == override


def test_sharepoint_folder_url_is_backcompat_alias(monkeypatch):
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    # Round 35 alias contract: when no env override is set, the legacy
    # attribute mirrors the canonical ``ADOPTIQ_CORPUS_SHARE_URL``.
    assert cfg.ADOPTIQ_SHAREPOINT_FOLDER_URL == cfg.ADOPTIQ_CORPUS_SHARE_URL


def test_settings_module_no_longer_accepts_sharepoint_folder_url(
    tmp_path, monkeypatch,
):
    # Point adoptiq_settings at a temp dir so the test doesn't touch
    # the real ~/Library/Application Support/AdoptIQ.
    import adoptiq_settings

    monkeypatch.setattr(
        adoptiq_settings,
        "_app_support_dir",
        lambda: tmp_path,
    )

    # Write a legacy settings.json containing the now-retired key.
    payload = {
        "corpus_knowledge_enabled": True,
        "sharepoint_folder_url": (
            "https://contoso.sharepoint.com/sites/Marketing/Docs"
        ),
    }
    settings_path = tmp_path / adoptiq_settings.SETTINGS_FILENAME
    settings_path.write_text(
        json.dumps(payload), encoding="utf-8"
    )

    loaded = adoptiq_settings.load_settings()
    assert loaded == {"corpus_knowledge_enabled": True}, (
        f"sharepoint_folder_url should be silently dropped from load "
        f"output; got {loaded!r}"
    )

    # save_settings should also drop the key on write.
    adoptiq_settings.save_settings({
        "corpus_knowledge_enabled": False,
        "sharepoint_folder_url": "https://x.sharepoint.com/y",
    })
    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "sharepoint_folder_url" not in on_disk
    assert on_disk == {"corpus_knowledge_enabled": False}


def test_settings_module_still_exports_is_valid_sharepoint_url():
    import adoptiq_settings

    assert hasattr(adoptiq_settings, "is_valid_sharepoint_url")
    # Sanity: positive case.
    assert adoptiq_settings.is_valid_sharepoint_url(
        "https://contoso.sharepoint.com/sites/X/Docs"
    )
    # Sanity: negative case (non-sharepoint host).
    assert not adoptiq_settings.is_valid_sharepoint_url(
        "https://attacker.example.com/x"
    )
