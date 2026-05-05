"""Round 84 / Build 60: per-call corpus share URL resolution.

Resolves the active SharePoint share URL used by the analyze-page
bootstrap-shortcut button (``signed_in_no_corpus`` panel state) and
the optional ``odopen://`` deep link. Pre-R84 this was hardcoded to
``Config.ADOPTIQ_CORPUS_SHARE_URL`` with an env-var override, which
meant rotating the SharePoint share token (the ``e=...`` query
parameter) required a DMG rebuild. The R84 resolver layers operator
control via ``settings.json`` on top, mirroring the R69
``model_resolver`` pattern.

Resolution order, highest precedence first:

1. ``adoptiq_settings.load_settings()['corpus_share_url']`` (UI
   override; takes effect on next request -- this module
   intentionally does NOT cache so the operator does not have to
   restart the .app).
2. ``ADOPTIQ_CORPUS_SHARE_URL`` env var (operator-set environment
   override -- exists pre-R84, preserved verbatim).
3. ``Config.ADOPTIQ_CORPUS_SHARE_URL`` (the hardcoded default that
   ships with the build; the same constant the bake script uses for
   informational logging).

Defensive posture: every value -- settings.json, env, config -- is
re-vetted through ``adoptiq_settings.is_valid_sharepoint_url`` before
being returned. A malformed value at any tier silently falls through
to the next tier, so a corrupted ``settings.json`` (or a hand-edited
file that bypassed the schema validator on save) cannot leak a
malformed URL into the bootstrap-shortcut endpoint or be passed to
``window.open`` / a browser-launching ``webbrowser.open`` call.

R83 contract preserved: this resolver feeds ``_r83_safe_share_url``
which applies its own R83 https-only + 2048-byte cap before
returning to the API. The encryption / sentinel / decrypt path is
NOT touched -- a stolen DMG without OneDrive auth is still useless
ciphertext (see ``corpus_bootstrap._run_index_pass`` calling
``open_corpus_for_user(..., allow_local_sentinel=False)``).

Test contract: callers MUST be tolerant of the resolver returning a
different URL on a subsequent call within the same process (the UI
toggle mutates settings.json mid-run). Per-request resolution is
intentional so the operator's flip on the analyze-page card takes
effect on the next bootstrap-shortcut click without a process
restart.
"""

# Round 84 / Build 60

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Source labels surfaced to the UI / GET endpoint so the operator can
# see which precedence tier produced the active URL. Stable string
# values pinned by ``tests/test_round84_corpus_share_url_resolver.py``.
SOURCE_SETTINGS: str = "settings.json"
SOURCE_ENV: str = "env"
SOURCE_CONFIG: str = "config.py"

# Env var name -- intentionally identical to the variable consumed at
# config.py import time so the operator who already exports it for
# the build pipeline gets the same effective URL when running the
# .app from a dev shell.
_ENV_VAR_NAME: str = "ADOPTIQ_CORPUS_SHARE_URL"


def _vet_share_url(value: object) -> Optional[str]:
    """Return ``value`` (stripped) if it is a non-empty string that
    passes the ``adoptiq_settings._is_valid_sharepoint_url`` allow-list,
    else ``None``. Wrapped in a broad try/except because the resolver
    MUST NOT raise -- a missing ``adoptiq_settings`` module in some
    test fixture should silently fall through to the next layer rather
    than break the analyze-page panel rendering.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        import adoptiq_settings as _settings
    except Exception:  # noqa: BLE001 - resolver MUST NOT raise
        return None
    try:
        if not _settings.is_valid_sharepoint_url(candidate):
            return None
    except Exception:  # noqa: BLE001 - defensive
        return None
    return candidate


def _read_settings_value() -> Optional[str]:
    """Return the validated ``settings.json['corpus_share_url']`` value
    or ``None``. Wrapped in a broad try/except so a corrupt
    ``settings.json`` falls through to the env layer rather than
    breaking the analyze-page bootstrap-shortcut endpoint.
    """
    try:
        import adoptiq_settings as _settings
    except Exception:  # noqa: BLE001
        return None
    try:
        loaded = _settings.load_settings()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(loaded, dict):
        return None
    raw = loaded.get("corpus_share_url")
    vetted = _vet_share_url(raw)
    if raw and not vetted:
        # load_settings() should have already dropped invalid values,
        # but defense-in-depth: if a future schema change relaxes the
        # validator, the resolver still catches malformed URLs before
        # they reach the bootstrap-shortcut endpoint.
        logger.warning(
            "corpus_share_url_resolver: settings.json value failed "
            "allow-list re-vet; falling through to env"
        )
    return vetted


def _read_env_value() -> Optional[str]:
    """Return ``os.environ['ADOPTIQ_CORPUS_SHARE_URL']`` if set AND
    allow-list-clean, else ``None``."""
    raw = os.environ.get(_ENV_VAR_NAME)
    vetted = _vet_share_url(raw)
    if raw and not vetted:
        logger.warning(
            "corpus_share_url_resolver: env %s failed allow-list "
            "re-vet; falling through to config.py default",
            _ENV_VAR_NAME,
        )
    return vetted


def _read_config_value() -> Optional[str]:
    """Return the hardcoded ``Config.ADOPTIQ_CORPUS_SHARE_URL`` default
    if it passes the allow-list, else ``None``. Wrapped in a broad
    try/except because the resolver MUST NOT raise even if config.py
    is partially imported (e.g. in a test fixture that patches
    ``Config``).
    """
    try:
        from config import Config as _Config
    except Exception:  # noqa: BLE001 - resolver MUST NOT raise
        return None
    raw = getattr(_Config, "ADOPTIQ_CORPUS_SHARE_URL", None)
    return _vet_share_url(raw)


def get_active_corpus_share_url() -> Tuple[Optional[str], str]:
    """Return ``(url, source)`` for the active corpus share URL.

    ``url`` is the validated SharePoint share URL or ``None`` if no
    tier produced a value that passed the allow-list.

    ``source`` is a stable label describing which tier won:

    * ``"settings.json"`` -- operator-set via the analyze-page card
    * ``"env"``           -- ``ADOPTIQ_CORPUS_SHARE_URL`` env override
    * ``"config.py"``     -- the hardcoded default that ships with the build

    On total exhaustion (every tier rejected) returns
    ``(None, "config.py")`` so callers always get a stable string for
    the source label and can distinguish "no URL configured" from
    "URL came from a specific source".

    Pre-R84 callers used ``Config.ADOPTIQ_CORPUS_SHARE_URL`` directly;
    R84 wires this resolver in front of ``_r83_safe_share_url`` so the
    operator can rotate the share URL via ``settings.json`` without a
    DMG rebuild.
    """
    candidate = _read_settings_value()
    if candidate:
        return candidate, SOURCE_SETTINGS
    candidate = _read_env_value()
    if candidate:
        return candidate, SOURCE_ENV
    candidate = _read_config_value()
    if candidate:
        return candidate, SOURCE_CONFIG
    return None, SOURCE_CONFIG


__all__ = [
    "SOURCE_SETTINGS",
    "SOURCE_ENV",
    "SOURCE_CONFIG",
    "get_active_corpus_share_url",
]
