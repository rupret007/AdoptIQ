"""
Error classification for AdoptIQ analysis failures.

The old classifier in ``app_simple.py`` only inspected the ``str(e)`` form of
an exception and treated *anything* containing the substring
``keeper.cisco.com`` as "Please ensure you are connected to the Cisco VPN".

That banner is actively misleading in several common failure modes:

- DNS does not resolve (split-tunnel not routing ``keeper.cisco.com``).
- TCP connects but TLS cert cannot be verified (corporate TLS inspection
  re-issues certificates with an internal CA; ``certifi`` does not trust it).
- AppRole returns 401 ``invalid role or secret id`` because the bundled
  ``KEEPER_ROLE_ID``/``KEEPER_SECRET_ID`` have been rotated.
- Keeper secret path 404 (wrong path).
- Snowflake itself denies the role/warehouse.

``classify_analysis_error`` replaces the substring cascade with a typed
classifier that is pure (no Flask / no network / no side effects), so it is
trivially unit-testable.

Typical usage from the Flask request handler::

    from error_classifier import classify_analysis_error

    classification = classify_analysis_error(exc)
    status['error'] = classification.user_message
    status['error_kind'] = classification.kind
    status['error_detail'] = classification.detail_tail
    logger.error(
        "Analysis %s failed: %s: %s",
        analysis_id,
        type(exc).__name__,
        exc,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# Max characters of the underlying exception text we echo into the UI banner.
# Enough for a human to paste into a ticket, short enough not to blow out the
# progress-page banner or accidentally surface long request bodies.
_DETAIL_TAIL_CHARS = 240


@dataclass(frozen=True)
class AnalysisErrorClassification:
    """Structured classification of a failure thrown during an analysis run.

    Round 5 / Phase 6.12: ``kind`` values are *namespaced* using
    ``"<category>.<subcategory>.<reason>"`` so log scrapers, dashboards,
    and the support runbook can group failures by category without
    relying on substring matches.  Categories currently in use:

    - ``analysis.keeper.*``    -- Cisco Keeper / HashiCorp Vault failures
    - ``analysis.snowflake.*`` -- Snowflake connection / auth failures
    - ``analysis.network.*``   -- generic network connectivity issues
    - ``analysis.unknown``     -- unclassified analysis failure
    - ``llm.*``                -- CircuIT / Azure OpenAI failures (set by
                                  ``CircuitChatClient.chat`` and the
                                  classifier's CircuIT branch)
    """

    kind: str
    user_message: str
    detail_tail: str


def _safe_type_name(e: BaseException) -> str:
    """Return only the bare class name of the exception.

    Round 6 / Phase 6.19: previously returned ``module.ClassName``
    (e.g. ``hvac.exceptions.InvalidRequest``).  That is useful for
    server-side debugging but the value is interpolated into
    ``detail_tail``, which is read by the UI banner and the audit
    JSONL mirror -- both end up in places where leaking internal
    module paths gives an attacker free reconnaissance about which
    third-party libraries are bundled and at what import paths.
    Return only the short class name; full module qualification is
    kept in the underlying exception traceback that the admin Errors
    view (and structured log) still capture.
    """

    cls = type(e)
    return getattr(cls, "__name__", "Exception")


def _normalized_message(e: BaseException) -> str:
    try:
        return str(e)
    except Exception:  # pragma: no cover - defensive
        return repr(e)


def _detail_tail(e: BaseException) -> str:
    """Short, scrubbed tail of the exception text suitable for a UI banner.

    Intentionally prefixes the type name so support can tell a 401 from a TLS
    failure at a glance. Trims to ``_DETAIL_TAIL_CHARS`` to avoid surfacing
    long URLs or accidentally-logged payloads.
    """

    msg = _normalized_message(e)
    import re as _re

    # Strip absolute Keeper secret paths and role IDs that might appear in
    # some hvac errors. Best-effort only.
    lowered = msg.lower()
    if "role_id" in lowered or "secret_id" in lowered:
        # Replace any alphanumeric run of length >= 20 with a placeholder.
        msg = _re.sub(r"[A-Za-z0-9\-]{20,}", "<redacted>", msg)

    # Round 6 / Phase 4.20: scrub absolute URLs and host:port pairs out
    # of the user-facing detail tail.  Internal Cisco hostnames and
    # path tokens (Keeper secret paths, Snowflake account names) are
    # not useful to the end-user but ARE useful to attackers / leak
    # internal infrastructure topology.  Logs still keep the full
    # exception text via logger.exception()/logger.error.
    msg = _re.sub(r"https?://[^\s'\"]+", "<url-redacted>", msg)
    # Bare host:port like "internal-host.cisco.com:443" or IPv4 literal.
    msg = _re.sub(
        r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?::\d{1,5})?\b",
        "<host-redacted>",
        msg,
    )
    msg = _re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b", "<host-redacted>", msg)
    # Filesystem-style absolute paths (cap at ~120 chars to avoid
    # eating short field labels like "type=/path/to" prefixes).
    msg = _re.sub(r"(?<!\w)/[A-Za-z0-9_\-./]{6,120}", "<path-redacted>", msg)

    head = f"{type(e).__name__}: {msg}"
    if len(head) > _DETAIL_TAIL_CHARS:
        head = head[: _DETAIL_TAIL_CHARS - 3] + "..."
    return head


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(n.lower() in t for n in needles)


def classify_analysis_error(e: BaseException) -> AnalysisErrorClassification:
    """Classify an exception raised during analysis execution.

    This function is intentionally pure. It does not log, does not import
    optional modules eagerly, and returns a stable structured result even when
    the exception class is not one of the ones it recognises.
    """

    type_name = _safe_type_name(e)
    message = _normalized_message(e)
    detail = _detail_tail(e)

    # ------------------------------------------------------------------
    # 1. DNS failure: ``socket.gaierror`` (name resolution)
    # ------------------------------------------------------------------
    # ``gaierror`` subclasses ``OSError`` so match on the bare class name.
    if type_name.endswith("gaierror") or _contains_any(
        message,
        (
            "Name or service not known",
            "nodename nor servname provided",
            "Temporary failure in name resolution",
            "getaddrinfo failed",
        ),
    ):
        return AnalysisErrorClassification(
            kind="analysis.keeper.dns_failed",
            user_message=(
                "DNS lookup for keeper.cisco.com failed. You are likely not on a "
                "network route that resolves internal Cisco hostnames. Connect to "
                "the Cisco AnyConnect VPN (or check split-tunnel routing) and "
                "retry."
            ),
            detail_tail=detail,
        )

    # ------------------------------------------------------------------
    # 2. TLS cert verify failure (corporate TLS inspection / truststore
    #    mismatch). This is the most common "on VPN but still fails" case.
    # ------------------------------------------------------------------
    if (
        "SSLCertVerificationError" in type_name
        or "SSLError" in type_name
        or _contains_any(
            message,
            (
                "CERTIFICATE_VERIFY_FAILED",
                "self-signed certificate in certificate chain",
                "self signed certificate in certificate chain",
                "unable to get local issuer certificate",
                "certificate verify failed",
            ),
        )
    ):
        return AnalysisErrorClassification(
            kind="analysis.keeper.tls_cert_verify_failed",
            user_message=(
                "TLS certificate verification to keeper.cisco.com failed. This "
                "almost always means Cisco corporate TLS inspection is re-signing "
                "the connection with an internal CA that Python's bundled trust "
                "store does not know about. Install the Cisco corporate root CA "
                "into your system keychain (it usually already is on Cisco-issued "
                "laptops) and restart AdoptIQ -- the app now prefers the system "
                "keychain automatically via truststore when available."
            ),
            detail_tail=detail,
        )

    # ------------------------------------------------------------------
    # 3. Read timeout talking to Keeper
    # ------------------------------------------------------------------
    if (
        "ReadTimeout" in type_name
        or "ReadTimeoutError" in type_name
        or (
            "keeper.cisco.com" in message.lower()
            and _contains_any(message, ("Read timed out", "timed out"))
        )
    ):
        return AnalysisErrorClassification(
            kind="analysis.keeper.read_timeout",
            user_message=(
                "Timed out waiting for a response from keeper.cisco.com. The VPN "
                "appears connected but the Keeper service is slow or briefly "
                "unavailable. Retry in a minute; if it persists, check "
                "https://status.cisco.com or ping #keeper-support."
            ),
            detail_tail=detail,
        )

    # ------------------------------------------------------------------
    # 4. TCP connect refused/reset (not a cert issue; service unreachable)
    # ------------------------------------------------------------------
    if (
        "ConnectionRefusedError" in type_name
        or "ConnectionResetError" in type_name
        or "ConnectionError" in type_name
        or _contains_any(
            message,
            (
                "Connection refused",
                "Connection reset by peer",
                "Network is unreachable",
                "No route to host",
                "Max retries exceeded",
            ),
        )
    ):
        # Only attribute this to Keeper if the message actually points at it.
        if "keeper.cisco.com" in message.lower():
            return AnalysisErrorClassification(
                kind="analysis.keeper.unreachable",
                user_message=(
                    "Cannot reach keeper.cisco.com over TCP. VPN may be connected "
                    "but the Keeper service or its front door is unreachable from "
                    "this network path. Retry, or try a different network egress."
                ),
                detail_tail=detail,
            )
        return AnalysisErrorClassification(
            kind="analysis.network.unreachable",
            user_message=(
                "A network connection was refused or dropped during analysis. "
                "Retry; if it persists, check VPN and upstream Cisco service "
                "status."
            ),
            detail_tail=detail,
        )

    # ------------------------------------------------------------------
    # 5. Keeper / HashiCorp Vault HTTP responses (hvac.exceptions.*)
    # ------------------------------------------------------------------
    # Covers both real hvac classes and substring clues when frozen builds
    # strip module paths.
    if "InvalidPath" in type_name or _contains_any(
        message, ("no handler for route", "preflight capability check returned 403")
    ):
        return AnalysisErrorClassification(
            kind="analysis.keeper.secret_path_not_found",
            user_message=(
                "Keeper returned 'secret not found' for the configured path. The "
                "embedded KEEPER_SECRET_PATH is wrong or the secret was moved. "
                "Ask the Keeper admin for the current path and rebuild AdoptIQ "
                "with updated credentials."
            ),
            detail_tail=detail,
        )

    if "Forbidden" in type_name or _contains_any(
        message,
        (
            "permission denied",
            "403 Forbidden",
            "access denied by policy",
        ),
    ):
        # If the message clearly references Snowflake, prefer the Snowflake
        # classification below; otherwise attribute to Keeper.
        if "snowflake" not in message.lower():
            return AnalysisErrorClassification(
                kind="analysis.keeper.forbidden",
                user_message=(
                    "Keeper rejected the request with 403 Forbidden. The AppRole "
                    "exists but the attached policy does not grant access to the "
                    "configured secret path. Ask the Keeper admin to extend the "
                    "policy or rebuild AdoptIQ with an AppRole that can read this "
                    "path."
                ),
                detail_tail=detail,
            )

    if (
        "InvalidRequest" in type_name
        or _contains_any(
            message,
            (
                "invalid role or secret id",
                "invalid role id",
                "invalid secret id",
                "missing client token",
            ),
        )
    ):
        # Round 7 / Phase 3.14: keep ``user_message`` generic and move
        # internal script names (``embed_credentials.py``,
        # ``build_mac.sh``) into ``detail_tail`` so they only appear in
        # the admin Errors view, not in user-visible banners.
        _approle_detail = (
            f"Operator runbook: refresh KEEPER_ROLE_ID / KEEPER_SECRET_ID, "
            f"update secrets.env, then re-run embed_credentials.py and "
            f"build_mac.sh.  Original error tail: {detail}"
        )
        return AnalysisErrorClassification(
            kind="analysis.keeper.approle_unauthorized",
            user_message=(
                "Authentication to the secrets service was rejected. "
                "Please contact your AdoptIQ administrator to refresh the "
                "service credentials."
            ),
            detail_tail=_approle_detail,
        )

    # ------------------------------------------------------------------
    # 6. Snowflake-side failures. Hit these *after* Keeper checks so a
    #    rotated AppRole is not misattributed to Snowflake.
    # ------------------------------------------------------------------
    if (
        "snowflake" in type_name.lower()
        or "is not allowed to access Snowflake" in message
        or "Failed to connect to DB" in message
        or "Incorrect username or password" in message
    ):
        if _contains_any(message, ("is not allowed to access Snowflake",)):
            return AnalysisErrorClassification(
                kind="analysis.snowflake.access_denied",
                user_message=(
                    "Snowflake denied the service account. Confirm the bundled "
                    "SNOWFLAKE_ROLE and warehouse are still granted to the ETL "
                    "service user, then rebuild AdoptIQ."
                ),
                detail_tail=detail,
            )
        return AnalysisErrorClassification(
            kind="analysis.snowflake.error",
            user_message=(
                "Snowflake rejected or dropped the connection. Verify the "
                "bundled SNOWFLAKE_USER / role / warehouse are still valid and "
                "retry; if it persists, ask the data platform team to check the "
                "service account."
            ),
            detail_tail=detail,
        )

    # ------------------------------------------------------------------
    # 7. Snowflake connect timeout wrapped in RuntimeError by
    #    adoptiq_backend._connect_with_keeper
    # ------------------------------------------------------------------
    if (
        "Snowflake connection timed out" in message
        or "Snowflake connection timeout" in message
        or "TimeoutError" in type_name
        or "concurrent.futures._base.TimeoutError" in type_name
    ):
        return AnalysisErrorClassification(
            kind="analysis.snowflake.timeout",
            user_message=(
                "Timed out establishing a Snowflake session (30s). Keeper may "
                "have answered but Snowflake itself is slow or the warehouse is "
                "suspended. Retry; if it persists, check Snowflake status."
            ),
            detail_tail=detail,
        )

    # ------------------------------------------------------------------
    # 8. Generic "mentions keeper.cisco.com" fallback - preserves the old
    #    behaviour so we never regress for truly-unclassified Keeper errors.
    # ------------------------------------------------------------------
    if "keeper.cisco.com" in message.lower():
        return AnalysisErrorClassification(
            kind="analysis.keeper.generic",
            user_message=(
                "A call to Cisco Keeper failed. Run the connectivity self-test "
                "from the Admin page (or GET /api/diag/connectivity) to see "
                "which stage (DNS, TLS, AppRole, secret read) is the culprit."
            ),
            detail_tail=detail,
        )

    # ------------------------------------------------------------------
    # 9. CircuIT / AzureOpenAI side (kept for parity with old classifier)
    # ------------------------------------------------------------------
    if "CircuIT" in message or "AzureOpenAI" in message or "openai" in type_name.lower():
        return AnalysisErrorClassification(
            kind="llm.service_error",
            user_message=(
                "The CircuIT AI service did not respond cleanly. Retry in a few "
                "minutes; if it persists, check CircuIT status."
            ),
            detail_tail=detail,
        )

    # ------------------------------------------------------------------
    # 10. Unknown - include the tail in ``detail_tail`` (which is server
    # logs / admin only) but keep ``user_message`` static and generic.
    # ------------------------------------------------------------------
    # Round 6 / Phase 6.4: previously this branch concatenated the
    # raw ``detail`` tail into ``user_message``, which is rendered in
    # toasts and ultimately echoed to the browser.  Even after the
    # Phase 4.20 redaction pass, the tail can still leak module
    # paths / line numbers / partial exception strings that point at
    # internal file layout.  Keep a single static sentence here and
    # rely on the admin Errors view to read ``detail_tail`` from the
    # log / audit mirror.
    return AnalysisErrorClassification(
        kind="analysis.unknown",
        user_message=(
            "An unexpected error occurred during analysis. "
            "Open the Admin page for the full traceback."
        ),
        detail_tail=detail,
    )


__all__ = [
    "AnalysisErrorClassification",
    "classify_analysis_error",
]
