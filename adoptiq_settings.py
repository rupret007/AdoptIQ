"""Persistent user-settings store for AdoptIQ.

Round 32 / Phase 2.E: surfaces a small JSON file under the platform
Application Support directory so the user can toggle Intelligence (and
later other persistent flags) without restarting the .app or editing
environment variables.

Resolution order at startup (highest precedence first):

1. ``settings.json`` value, if present and valid.
2. ``os.environ`` value (whatever ``config.py`` resolved at import).
3. ``config.py`` default.

Security posture:

* File mode 0o600 (owner read/write only) — settings can include
  feature toggles that could leak operational intent.
* Parent directory mode 0o700 to align with the existing
  ~/.adoptiq / ~/Library/Application Support/AdoptIQ contract pinned
  by ``tests/test_round6_adoptiq_dir_0700.py``.
* Atomic writes via ``os.replace`` so a crash mid-write cannot leave a
  half-written / truncated JSON document.
* Allow-listed keys only.  Unknown keys in the on-disk file are
  preserved on read but stripped on save, so a hand-edited file
  cannot grow unbounded.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


# Allow-list of keys we are willing to read/write.  Each entry maps to
# a ``(coercer, default)`` tuple so callers can rely on the returned
# value's type.
#
# Round 33 / Build8: ``sharepoint_folder_url`` joins the schema so the
# analyze-page Intelligence card can persist a per-user SharePoint
# folder URL without baking a hardcoded personal/tenant URL into the
# build.  Validation lives in ``_VALIDATORS`` below so a hand-edited
# settings.json containing an arbitrary URL cannot redirect Graph
# downloads to an attacker-controlled host.
_SCHEMA: Dict[str, tuple] = {
    "corpus_knowledge_enabled": (bool, False),
    "sharepoint_folder_url": (str, ""),
}

SETTINGS_FILENAME = "settings.json"


# Round 33 / Build8: strict allow-list for SharePoint URLs.  The host
# must be ``<tenant>.sharepoint.com`` (anchored ``^https://``); the
# path is required so a bare host can't be saved.  Empty strings are
# allowed and are interpreted by the startup hook as "fall back to env
# / config default".
_SHAREPOINT_URL_RE = re.compile(
    r"^https://[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.sharepoint\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$"
)


def _is_valid_sharepoint_url(value: Any) -> bool:
    """Return True if ``value`` is empty (= unset) or matches the
    ``https://<tenant>.sharepoint.com/<path>`` allow-list.

    The regex anchors both ends, restricts host to a single
    ``<tenant>.sharepoint.com`` label, and requires a non-empty path
    component (so a bare host like ``https://x.sharepoint.com`` is
    rejected -- callers always need a folder reference for the Graph
    download to succeed).
    """
    if value is None or value == "":
        return True
    if not isinstance(value, str):
        return False
    if len(value) > 2048:
        return False
    return bool(_SHAREPOINT_URL_RE.match(value))


# Per-key validators.  A validator returning False causes the key to
# be dropped (with a warning) on both load and save.  Keys without a
# validator entry pass through after type coercion.
_VALIDATORS: Dict[str, Callable[[Any], bool]] = {
    "sharepoint_folder_url": _is_valid_sharepoint_url,
}


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _app_support_dir() -> Path:
    """Return the platform-appropriate writable settings directory.

    Mirrors the resolution in ``app_simple.py`` so settings live next
    to the existing ``analysis_status.json`` / ``admin_monitoring_v2.db``.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "AdoptIQ"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "AdoptIQ"
    else:
        base = Path.home() / ".adoptiq"
    return base


def _settings_path() -> Path:
    return _app_support_dir() / SETTINGS_FILENAME


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_settings() -> Dict[str, Any]:
    """Read ``settings.json`` and return the allow-listed key/value pairs.

    Returns an empty dict on any error (file missing, malformed JSON,
    permission denied, unreadable types).  Never raises.
    """
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("adoptiq_settings: cannot read %s: %s", path, e)
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as e:
        logger.warning("adoptiq_settings: malformed JSON in %s: %s", path, e)
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "adoptiq_settings: expected JSON object in %s, got %s",
            path, type(parsed).__name__,
        )
        return {}
    out: Dict[str, Any] = {}
    for key, (coercer, _default) in _SCHEMA.items():
        if key not in parsed:
            continue
        try:
            coerced = coercer(parsed[key])
        except (TypeError, ValueError) as e:
            logger.warning(
                "adoptiq_settings: dropping invalid value for %r in %s: %s",
                key, path, e,
            )
            continue
        validator = _VALIDATORS.get(key)
        if validator is not None and not validator(coerced):
            logger.warning(
                "adoptiq_settings: dropping value for %r in %s: failed allow-list validation",
                key, path,
            )
            continue
        out[key] = coerced
    return out


def save_settings(settings: Mapping[str, Any]) -> Path:
    """Persist ``settings`` to ``settings.json`` atomically.

    Only allow-listed keys are written; unknown keys are silently
    dropped.  Parent directory is created with mode 0o700 if missing.
    The output file is written to a same-directory tempfile and then
    ``os.replace``-d into place so partial writes are impossible.
    Returns the final on-disk path.
    """
    if not isinstance(settings, Mapping):
        raise TypeError("settings must be a mapping")

    payload: Dict[str, Any] = {}
    for key, value in settings.items():
        if key not in _SCHEMA:
            continue
        coercer, _default = _SCHEMA[key]
        try:
            coerced = coercer(value)
        except (TypeError, ValueError) as e:
            logger.warning(
                "adoptiq_settings: dropping invalid value for %r on save: %s",
                key, e,
            )
            continue
        validator = _VALIDATORS.get(key)
        if validator is not None and not validator(coerced):
            logger.warning(
                "adoptiq_settings: dropping value for %r on save: failed allow-list validation",
                key,
            )
            continue
        payload[key] = coerced

    parent = _app_support_dir()
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError as e:
        # Non-fatal on platforms where chmod is a no-op (Windows) or
        # the directory is already correct.
        logger.debug("adoptiq_settings: chmod 0700 on %s skipped: %s", parent, e)

    target = parent / SETTINGS_FILENAME
    fd, tmp_path = tempfile.mkstemp(
        prefix=".settings.", suffix=".tmp", dir=str(parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            os.chmod(tmp_path, 0o600)
        except OSError as e:
            logger.debug(
                "adoptiq_settings: chmod 0600 on temp file failed: %s", e,
            )
        os.replace(tmp_path, target)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
    return target


def get(key: str, default: Optional[Any] = None) -> Any:
    """Return a single setting value, or ``default`` if unset/unknown."""
    if key not in _SCHEMA:
        return default
    return load_settings().get(key, default)


def set(key: str, value: Any) -> Path:  # noqa: A001 - mirrors load/save naming
    """Write a single setting value, preserving other allow-listed keys."""
    if key not in _SCHEMA:
        raise KeyError(f"adoptiq_settings: unknown key {key!r}")
    current = load_settings()
    current[key] = value
    return save_settings(current)


def schema_keys() -> tuple:
    """Return the tuple of allow-listed setting keys (introspection)."""
    return tuple(_SCHEMA.keys())


def is_valid_sharepoint_url(value: Any) -> bool:
    """Public alias for the SharePoint URL allow-list check.

    Use from request handlers (``POST /api/settings/sharepoint_url``)
    so the route can return a 400 before ever calling
    :func:`save_settings` -- otherwise the save would silently drop
    the value and the user would see no feedback.
    """
    return _is_valid_sharepoint_url(value)


__all__ = [
    "SETTINGS_FILENAME",
    "load_settings",
    "save_settings",
    "get",
    "set",
    "schema_keys",
    "is_valid_sharepoint_url",
]
