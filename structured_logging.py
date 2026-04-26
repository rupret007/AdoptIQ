"""Structured-logging helpers for AdoptIQ critical paths.

Round 5 / Phase 6.13: prior to this module, the analysis pipeline emitted
free-form ``logger.info("foo bar baz %s", value)`` lines.  That made it
hard to correlate events across a single analysis run (lines did not
carry the ``analysis_id``) and hard for log scrapers to pivot by report
type / kind / customer.

This module provides:

- ``analysis_logger(analysis_id, **fields)`` -- returns a
  :class:`logging.LoggerAdapter` that automatically prepends a stable
  structured prefix ``[analysis_id=... key=...]`` to every record so
  per-run correlation just works.

- ``StructuredAdapter`` -- the underlying adapter class.  It is safe to
  pass extra kwargs at call time (``log.info("...", extra_kv={"step":
  "fetch"})``) which are merged on top of the bound context.

The adapter is intentionally tiny -- it does *not* try to emit JSON.
We log to a human-readable terminal and a rotating text file (see
``app_simple.py`` Phase 6.6), so a structured *prefix* gives us the
grep-ability we need without breaking the existing log pipeline.

Phase 6.14 builds on this with a per-thread ``contextvars`` carrier so
background workers inherit the bound ``analysis_id`` without having to
pass an adapter explicitly through every call site.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any, Mapping, MutableMapping, Optional


# Round 5 / Phase 6.14: the active analysis ID for the current logical
# task (request thread + any worker threads it spawns).  ``contextvars``
# automatically copies its value across ``threading.Thread`` boundaries
# when ``contextvars.copy_context().run(...)`` is used; for raw
# ``Thread`` callers ``bind_analysis_id`` should be invoked at the
# start of the worker.  Default ``None`` means "no analysis bound";
# the adapter will simply omit the prefix in that case.
_current_analysis_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "adoptiq_current_analysis_id", default=None
)

# Round 13 / Phase 10.7: previously the structured logger only
# correlated by ``analysis_id``.  Flask handlers that are *not* tied
# to an analysis (e.g. ``/api/diag/connectivity``, ``/admin/*``,
# admin UI POSTs) had no way to thread an upstream request id into
# their log lines, so support could not stitch together "this
# /admin/* call landed at 11:35:01" with the actual error log line
# that fired three frames deeper inside a worker pool.  Mirror the
# same contextvar pattern for ``request_id`` so any handler that
# wants per-request correlation can call ``bind_request_id`` once
# in a ``before_request`` hook and every nested log line picks up
# the id automatically -- *without* requiring an adapter to be
# threaded through every call.
_current_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "adoptiq_current_request_id", default=None
)


def bind_analysis_id(analysis_id: Optional[str]) -> contextvars.Token:
    """Bind ``analysis_id`` to the current logical context.

    Returns the ``contextvars.Token`` so callers can ``reset`` later.
    Workers should call ``bind_analysis_id`` once at the top of their
    target function so subsequent ``analysis_logger()`` / ``get_logger``
    calls automatically carry the correlation ID.
    """
    return _current_analysis_id.set(analysis_id)


def get_current_analysis_id() -> Optional[str]:
    """Return the currently-bound analysis ID, or None if unbound."""
    try:
        return _current_analysis_id.get()
    except LookupError:
        return None


def bind_request_id(request_id: Optional[str]) -> contextvars.Token:
    """Bind ``request_id`` to the current logical context (Round 13).

    Same pattern as :func:`bind_analysis_id`.  Returns the
    ``contextvars.Token`` so callers (typically Flask
    ``before_request`` / ``after_request`` hooks) can reset the
    binding once the request lifecycle ends.
    """
    return _current_request_id.set(request_id)


def get_current_request_id() -> Optional[str]:
    """Return the currently-bound request ID, or None if unbound."""
    try:
        return _current_request_id.get()
    except LookupError:
        return None


# Round 7 / Phase 3.16: regex-based value redactor for ``extra_kv``.
# These patterns target the most common PII / secret shapes we have
# historically seen leak through structured log prefixes:
#   - email addresses
#   - GitHub / Stripe / Slack / AWS-style API tokens (rough shape)
#   - JWT (three dot-separated base64 segments)
#   - long random hex / base64 (>=32 chars)
#   - IPv4 addresses
#   - "key=value" credential shapes embedded inside values
import re as _re

_REDACT_PATTERNS: tuple[tuple[str, _re.Pattern[str]], ...] = (
    ("email", _re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    ("token", _re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|sk_live|sk_test|pk_live|pk_test|xox[baprs]|AKIA|ASIA|AGPA)[A-Za-z0-9_\-]{16,}\b")),
    ("jwt", _re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    ("ipv4", _re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("hex_blob", _re.compile(r"\b[a-f0-9]{32,}\b", _re.IGNORECASE)),
)

_REDACT_KEY_PREFIXES: tuple[str, ...] = (
    "secret", "password", "token", "auth", "api_key", "apikey",
    "private_key", "session", "cookie",
)


# Round 8 / Phase 6.6: allowlist of common technical / product-name tokens
# that legitimately appear in log prefixes (report types, integration
# names, module monikers).  The pre-Round-8 ``name-shape`` regex matched
# any "Capitalised Word Capitalised Word Capitalised Word" sequence and
# silently redacted things like "Snowflake Bulk Account Cache" or
# "Cisco Customer Success Hub" -- breaking grep-ability without any
# privacy benefit.  If every token in a candidate phrase is in this
# allowlist, we skip the name-shape redaction.  Anything outside this
# list (i.e. plausible person / company names) still redacts.
_NAME_SHAPE_ALLOWLIST_TOKENS: frozenset[str] = frozenset({
    # Report / pipeline labels
    "Adoption", "Adopt", "Renewal", "Renewals", "Leader", "Executive",
    "Briefing", "Portfolio", "Customer", "Customers", "Account",
    "Accounts", "Analysis", "Pipeline", "Insights", "Insight", "Report",
    "Reports", "Summary", "Status", "Mode", "Phase", "Round",
    # Integrations / vendors / products
    "Snowflake", "Cisco", "AdoptIQ", "Bst", "BST", "Keeper", "PSIRT",
    "Circuit", "OneDrive", "Power", "Salesforce", "SS", "EDW",
    # Technical nouns
    # Round 14 / Phase 4.3: removed duplicate "Worker" and "Mode" tokens
    # (B033) -- duplicates in a frozenset literal are silently collapsed
    # but signal a copy/paste mistake that ruff should keep flagging.
    "Cache", "Bulk", "Worker", "Threaded", "Strict",
    "Ask", "AI", "Audit", "Auditor", "Verbose", "Debug", "Health",
    "Latency", "Profile", "Request", "Response", "Server", "Client",
    "Schema", "Database", "Table", "Tables", "Query", "Index", "View",
    "Hub", "Center", "Service", "Services", "Engine", "Engines",
    "Data", "Dataset", "Frame", "Bundle", "Module", "Modules",
    "Success", "Failure", "Warning", "Warnings", "Error", "Errors",
    "Map", "Maps", "Job", "Jobs", "Run", "Runs", "Record", "Records",
})


def _is_technical_name_shape(s: str) -> bool:
    """Return True if every token in ``s`` is a known technical token.

    Used to suppress the coarse ``name-shape`` redaction when the
    matched phrase is clearly a product / module label rather than a
    person or company name.
    """
    tokens = s.split()
    if not tokens:
        return False
    for t in tokens:
        # Strip surrounding punctuation that the regex left attached.
        bare = t.strip(",.;:!?()[]{}\"'")
        if not bare:
            continue
        if bare not in _NAME_SHAPE_ALLOWLIST_TOKENS:
            return False
    return True


def _redact_extra_kv_value(key: str, value: Any) -> str:
    """Redact sensitive value shapes for structured log prefixes.

    Round 7 / Phase 3.16: returns a short marker
    (``<redacted:KIND>``) for any value that matches a sensitive
    pattern, or whose key looks secret-bearing.  Non-sensitive
    primitives pass through (still ``str()``-coerced).
    """
    try:
        key_l = str(key).lower()
        if any(key_l.startswith(p) or key_l.endswith(p) for p in _REDACT_KEY_PREFIXES):
            return "<redacted:secret-key>"
        if value is None:
            return ""
        s = str(value)
        if not s:
            return ""
        for kind, pat in _REDACT_PATTERNS:
            if pat.search(s):
                return f"<redacted:{kind}>"
        # Coarse customer-name guard: 3+ space-separated capitalised
        # tokens that look like a company / person name.
        # Round 8 / Phase 6.6: do NOT redact when every matched token
        # is in ``_NAME_SHAPE_ALLOWLIST_TOKENS``; those are technical
        # phrases (report types, integration names) that operators
        # need to grep for.
        m = _re.search(r"\b([A-Z][a-z0-9]{2,}\s){2,}[A-Z][a-z0-9]{2,}\b", s)
        if m and not _is_technical_name_shape(m.group(0)):
            return "<redacted:name-shape>"
        return s
    except Exception:
        return "<redacted:unrenderable>"


class StructuredAdapter(logging.LoggerAdapter):
    """LoggerAdapter that prepends a structured ``[k=v ...]`` prefix.

    The bound context is provided via ``extra={"_kv": {...}}``.  Per-call
    extras may be passed via the standard ``extra`` kwarg, or via
    ``extra_kv`` for a friendlier alias that does not collide with
    LogRecord attributes.
    """

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        bound: Mapping[str, Any] = (self.extra or {}).get("_kv", {})
        # Merge per-call ``extra_kv`` if provided.
        per_call: Mapping[str, Any] = {}
        try:
            ekv = kwargs.pop("extra_kv", None)
            if isinstance(ekv, Mapping):
                per_call = ekv
        except Exception:
            per_call = {}

        # If no analysis_id was bound at construction, fall back to the
        # contextvar (Phase 6.14).  This lets workers that did not carry
        # the adapter still emit correlated lines once they have called
        # ``bind_analysis_id``.
        merged: dict[str, Any] = {}
        cur = get_current_analysis_id()
        if cur is not None and "analysis_id" not in bound and "analysis_id" not in per_call:
            merged["analysis_id"] = cur
        # Round 13 / Phase 10.7: same fallback for the request_id
        # contextvar so handlers that bind a request id in
        # ``before_request`` automatically carry it into every
        # nested ``logger.info`` line without needing to thread
        # an adapter through every helper function.
        cur_req = get_current_request_id()
        if (
            cur_req is not None
            and "request_id" not in bound
            and "request_id" not in per_call
        ):
            merged["request_id"] = cur_req
        for k, v in bound.items():
            merged[k] = v
        for k, v in per_call.items():
            merged[k] = v

        if not merged:
            return msg, kwargs

        # Round 7 / Phase 3.16: redact value strings before rendering
        # the prefix.  ``extra_kv`` is structured user-supplied data and
        # has historically carried emails, customer-name samples, and
        # secret-bearing tokens straight into the log line.  Apply a
        # value scrubber so the structured prefix only carries low-risk
        # tokens while sensitive shapes are tagged as ``<redacted:...>``.
        # Round 13 / Phase 10.7: ``request_id`` joins ``analysis_id``
        # at the head of the prefix for stable, grep-friendly
        # ordering.
        ordered_keys = sorted(
            merged.keys(),
            key=lambda k: (
                0 if k == "analysis_id" else (1 if k == "request_id" else 2),
                k,
            ),
        )
        rendered = " ".join(
            f"{k}={_redact_extra_kv_value(k, merged[k])}" for k in ordered_keys
        )
        return f"[{rendered}] {msg}", kwargs


def analysis_logger(
    analysis_id: Optional[str] = None,
    *,
    logger_name: Optional[str] = None,
    **extra_fields: Any,
) -> StructuredAdapter:
    """Return a ``StructuredAdapter`` bound to ``analysis_id``.

    ``logger_name`` lets callers override the underlying logger name
    (defaults to the module-qualified caller).  ``extra_fields`` are
    bound for the lifetime of the adapter and emitted on every record.

    Example::

        log = analysis_logger(analysis_id, report_type="leader")
        log.info("connecting to snowflake")
        # → "[analysis_id=Leader_X_30d_1700 report_type=leader] connecting ..."
    """
    base = logging.getLogger(logger_name) if logger_name else logging.getLogger("adoptiq")
    bound: dict[str, Any] = {}
    if analysis_id:
        bound["analysis_id"] = str(analysis_id)
    for k, v in extra_fields.items():
        if v is None:
            continue
        bound[k] = v
    return StructuredAdapter(base, {"_kv": bound})


__all__ = [
    "StructuredAdapter",
    "analysis_logger",
    "bind_analysis_id",
    "get_current_analysis_id",
    "bind_request_id",
    "get_current_request_id",
]
