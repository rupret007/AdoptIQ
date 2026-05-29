"""Round 69 / Build 43: per-call-site CircuIT model resolution.

Resolves the active model name for the two LLM call sites that we
intentionally split in Round 69:

* Ask AI (interactive Q&A surfaces -- `/api/ask-ai-portfolio`,
  `/api/ask-intel`).  Tuned for low latency and conversational
  ergonomics.
* Report narratives (per-customer storyboards, portfolio summary,
  subscription analysis, compact AI insights).  Tuned for
  grounding-validator pass rate -- changes here can move the R16/R27
  rejection rate, so the operator should test before promoting.

Resolution order, highest precedence first:

1. ``settings.json`` value managed by ``adoptiq_settings`` (UI-set
   override; takes effect on next request -- this module intentionally
   does NOT cache so the operator does not have to restart the .app).
2. ``CIRCUIT_MODEL_NAME_ASK_AI`` / ``CIRCUIT_MODEL_NAME_REPORT`` env
   var (per-site env override; surfaced via ``CIRCUIT_CONFIG``).
3. ``CIRCUIT_MODEL_NAME`` env var (single-model legacy default;
   surfaced via ``CIRCUIT_CONFIG``).
4. Hardcoded ``gemini-3.1-flash-lite`` (config.py default; the resolver
   MUST never return an empty string).  Round 77 / Build 53 flipped this
   from ``gpt-5-nano``; see :data:`_HARDCODED_DEFAULT` for rationale.

Defensive posture: every value that comes off settings.json AND every
env value is re-vetted through ``adoptiq_settings.is_valid_model_name``
before being returned.  A malformed env var (e.g. operator pasted a
shell-quoted token by mistake) silently falls through to the next
layer -- we never propagate a value that would be rejected by
``CircuitChatClient``'s strict allow-list at the next layer up.

Test contract: callers MUST be tolerant of the resolver returning a
different model on a subsequent call within the same process (the UI
toggle mutates settings.json mid-run).  Per-request resolution is
intentional so the operator's flip in the navbar takes effect on the
next Ask AI question without a process restart.
"""

# Round 69 / Build 43

from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Hard-coded fallback that mirrors ``config.py``'s ``CIRCUIT_MODEL_NAME``
# default.  Duplicated rather than imported because callers of this
# module sometimes run before ``config.Config`` is fully initialised
# (e.g. during ``app_simple`` import-time wiring) and we never want
# the resolver to raise.
#
# Round 77 / Build 53: flipped from ``gpt-5-nano`` to
# ``gemini-3.1-flash-lite``.  Both options are CircuIT free-tier (15
# RPM, 120K peak tokens/min, 50M monthly input, 5M completion, $0
# quarterly).  Operator testing showed flash-lite delivered materially
# lower per-customer latency on the comprehensive report's per-customer
# storyboard loop while preserving the R66/B11 + R67/B8 grounding-pass
# rate.  ``gpt-5-nano`` remains a one-click toggle in the
# ``[data-r69-model-input]`` dropdowns on the analyze + ask-ai pages
# AND the admin console (R69 / Build 43 + R73 / UX-3 contracts).
_HARDCODED_DEFAULT = "gemini-3.1-flash-lite"  # Round 77
_R103_STALE_DEFAULT_MODEL = "gpt-5-nano"

# Round 71 / Phase 5 (#28): inline allow-list regex used as a
# defense-in-depth fallback when ``adoptiq_settings`` is unimportable.
# Pre-R71 the env-var validation path swallowed the ImportError and
# returned the env value verbatim, which meant a malformed env-var (e.g.
# operator pasted ``"gpt-5-nano; rm -rf /"`` or a shell-quoted token by
# mistake) propagated past this resolver into the CircuitChatClient
# constructor, where the strict allow-list rejected it -- but with a
# confusing "no model name provided" message because the value was
# silently dropped at a deeper layer.  The inline regex below mirrors
# the ``adoptiq_settings.is_valid_model_name`` contract: alphanumeric +
# hyphen + dot + underscore, 1-128 chars.  Rejected values fall through
# to the next layer just like settings.json values that fail the rich
# allow-list.
_R71_INLINE_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _r71_inline_is_valid_model_name(value: str) -> bool:
    """Defense-in-depth model-name validator used when adoptiq_settings
    fails to import.  Mirrors the canonical ``is_valid_model_name``
    allow-list contract (alphanumeric + ``-`` + ``.`` + ``_``,
    bounded length).  Returns ``False`` for any value the strict
    rule would reject.
    """
    if not value or not isinstance(value, str):
        return False
    return bool(_R71_INLINE_MODEL_NAME_RE.match(value.strip()))


def _read_settings_value(key: str) -> Optional[str]:
    """Return the validated settings.json value for ``key`` or None.

    Wrapped in a broad try/except because the resolver MUST NOT raise
    -- a corrupt settings.json or a missing adoptiq_settings module
    in some test fixture should silently fall through to the env
    layer rather than break Ask AI or report generation.
    """
    try:
        import adoptiq_settings as _settings
    except Exception:  # noqa: BLE001
        return None
    try:
        # Round 108 / Corpus Smoothness + Round 115 / Build 84: run the
        # R103 + R108 + R115 model-default migrations before reading
        # settings so stale gpt-5-nano from upgraded installs resolves to
        # Gemini once more (R115 re-stomps installs that already carried
        # both earlier markers but were still pinned to nano).
        _settings.ensure_model_defaults_migrated()
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = _settings.get(key, "")
    except Exception:  # noqa: BLE001
        return None
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        if not _settings.is_valid_model_name(raw):
            logger.warning(
                "model_resolver: settings.json[%r] failed allow-list "
                "(value digest=%s); falling through to env layer",
                key, _short_digest(raw),
            )
            return None
    except Exception:  # noqa: BLE001
        return None
    return raw


def _r103_coerce_stale_default(raw: str, source: str) -> str:
    """Map stale gpt-5-nano defaults to Gemini for demo-ready builds.

    The UI still allows a deliberate post-migration gpt-5-nano selection
    through settings.json. This coercion applies to stale env/bundled
    defaults where no settings override exists.
    """
    if raw == _R103_STALE_DEFAULT_MODEL:
        logger.info(
            "model_resolver: Round 103 mapped stale %s=%s to %s",
            source,
            _R103_STALE_DEFAULT_MODEL,
            _HARDCODED_DEFAULT,
        )
        return _HARDCODED_DEFAULT
    return raw


def _read_env_value(env_var: str) -> Optional[str]:
    """Return ``os.environ[env_var]`` if set AND allow-list-clean."""
    raw = os.environ.get(env_var)
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        import adoptiq_settings as _settings
        if not _settings.is_valid_model_name(raw):
            logger.warning(
                "model_resolver: env %s failed allow-list "
                "(value digest=%s); falling through",
                env_var, _short_digest(raw),
            )
            return None
    except Exception:  # noqa: BLE001
        # Round 71 / Phase 5 (#28): adoptiq_settings is unimportable
        # in this context.  Pre-R71 the env value was accepted
        # verbatim, propagating malformed values past the resolver.
        # Apply the inline allow-list as defense-in-depth; rejected
        # values fall through to the next layer just like settings.json
        # values that fail the canonical validator.
        if not _r71_inline_is_valid_model_name(raw):
            logger.warning(
                "model_resolver: env %s failed inline allow-list "
                "(adoptiq_settings unimportable; value digest=%s); "
                "falling through",
                env_var, _short_digest(raw),
            )
            return None
    return _r103_coerce_stale_default(raw, env_var)


def _short_digest(value: str) -> str:
    """Return an 8-char SHA-256 digest of ``value`` for log correlation
    without echoing potentially-malformed user input."""
    try:
        import hashlib
        return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:8]
    except Exception:  # noqa: BLE001
        return "n/a"


def get_active_ask_ai_model() -> str:
    """Return the active CircuIT model name for Ask AI requests.

    Layered precedence:

    1. ``adoptiq_settings.get('ask_ai_model_name')`` (UI override)
    2. ``CIRCUIT_MODEL_NAME_ASK_AI`` env var
    3. ``CIRCUIT_MODEL_NAME`` env var (legacy single-model default)
    4. ``_HARDCODED_DEFAULT`` (matches ``config.py`` default)
    """
    candidate = _read_settings_value("ask_ai_model_name")
    if candidate:
        return candidate
    candidate = _read_env_value("CIRCUIT_MODEL_NAME_ASK_AI")
    if candidate:
        return candidate
    candidate = _read_env_value("CIRCUIT_MODEL_NAME")
    if candidate:
        return candidate
    return _HARDCODED_DEFAULT


def get_active_report_model() -> str:
    """Return the active CircuIT model name for report-narrative requests.

    Layered precedence (mirrors ``get_active_ask_ai_model`` but
    intentionally separate so a UI flip in one card does not move the
    other card's value).
    """
    candidate = _read_settings_value("report_model_name")
    if candidate:
        return candidate
    candidate = _read_env_value("CIRCUIT_MODEL_NAME_REPORT")
    if candidate:
        return candidate
    candidate = _read_env_value("CIRCUIT_MODEL_NAME")
    if candidate:
        return candidate
    return _HARDCODED_DEFAULT


__all__ = [
    "get_active_ask_ai_model",
    "get_active_report_model",
]
