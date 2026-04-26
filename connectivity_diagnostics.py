"""
Keeper / Snowflake connectivity self-test.

Exposes :func:`run_connectivity_diagnostics` which performs a strict, bounded
DNS -> TCP/TLS -> AppRole -> secret-read -> Snowflake chain and returns a
structured JSON-friendly payload suitable for rendering in the admin dashboard
or the analysis error banner.

This module intentionally does *not* depend on Flask so the probe can be
unit-tested by monkeypatching the individual helpers.

Security notes:
- Never echoes secret material. Role IDs / Secret IDs / private keys /
  passphrases are replaced with ``<redacted>``.
- When a check fails, downstream checks are marked ``skipped`` rather than
  attempted, which avoids compounding errors and masking the real cause.
- All network operations are bounded by short timeouts (DNS 2s, TLS 3s,
  AppRole 5s, secret read 5s, Snowflake 10s).
"""

from __future__ import annotations

import re
import socket
import ssl
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------
DNS_TIMEOUT_S = 2.0
TLS_TIMEOUT_S = 3.0
APPROLE_TIMEOUT_S = 5.0
SECRET_READ_TIMEOUT_S = 5.0
SNOWFLAKE_TIMEOUT_S = 10.0


_DETAIL_TAIL_CHARS = 240


def _mask_id(value: Optional[str], show: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= show * 2:
        return "*" * len(value)
    return f"{value[:show]}...{value[-show:]} (len={len(value)})"


_URL_RE = re.compile(r"https?://[^\s'\"]+")
_HOST_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?::\d{1,5})?\b"
)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_PATH_RE = re.compile(r"(?<!\w)/[A-Za-z0-9_\-./]{6,120}")


def _error_detail(e: BaseException) -> str:
    """Build a short, redacted exception tail suitable for the UI.

    Round 6 / Phase 4.20: scrub absolute URLs, hostnames, IPv4
    literals, and absolute filesystem paths out of the user-facing
    string.  These leak internal infrastructure topology (Keeper /
    Snowflake / CircuIT endpoints, secret paths) when surfaced in the
    diagnostics page or admin UI.  The full text is still available
    via server-side ``logger.exception()`` calls in the caller.
    """

    msg = str(e)
    msg = _URL_RE.sub("<url-redacted>", msg)
    msg = _HOST_RE.sub("<host-redacted>", msg)
    msg = _IPV4_RE.sub("<host-redacted>", msg)
    msg = _PATH_RE.sub("<path-redacted>", msg)
    head = f"{type(e).__name__}: {msg}"
    if len(head) > _DETAIL_TAIL_CHARS:
        head = head[: _DETAIL_TAIL_CHARS - 3] + "..."
    return head


# ---------------------------------------------------------------------------
# Individual probes (kept small so tests can monkeypatch them)
# ---------------------------------------------------------------------------
def _probe_dns(host: str, port: int) -> Dict[str, Any]:
    """Resolve ``host:port`` with a per-call timeout.

    Round 5 / Phase 6.15: the previous implementation called
    ``socket.setdefaulttimeout(DNS_TIMEOUT_S)`` and reset it in
    ``finally``.  ``setdefaulttimeout`` is *process-wide*, not
    per-thread, so any other socket operation that happened to start
    while the diagnostic was in flight (e.g. a Snowflake fetch on a
    worker thread, the LLM HTTP call) inherited the diagnostic's
    timeout for the duration of the probe -- producing flaky
    "timeout after 5s" failures in unrelated requests.  Run the
    blocking resolution call in a worker thread with
    ``concurrent.futures`` and bound the wait there instead, leaving
    the global default untouched.
    """
    import concurrent.futures

    t0 = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
            future = _ex.submit(
                socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM
            )
            addrs = future.result(timeout=DNS_TIMEOUT_S)
        first = addrs[0][4][0] if addrs else None
        return {
            "name": "dns_keeper",
            "status": "ok",
            "ms": round((time.monotonic() - t0) * 1000, 1),
            "detail": f"{host} -> {first} ({len(addrs)} records)",
        }
    except concurrent.futures.TimeoutError:
        return {
            "name": "dns_keeper",
            "status": "fail",
            "ms": round((time.monotonic() - t0) * 1000, 1),
            "error_kind": "dns_timeout",
            "detail": f"DNS resolution for {host} did not complete within {DNS_TIMEOUT_S}s",
        }
    except Exception as e:  # noqa: BLE001 - probe surfaces the real exception
        return {
            "name": "dns_keeper",
            "status": "fail",
            "ms": round((time.monotonic() - t0) * 1000, 1),
            "error_kind": "dns_failure",
            "detail": _error_detail(e),
        }


def _probe_tls(host: str, port: int) -> Dict[str, Any]:
    """TCP + TLS handshake. Uses the default trust store (which, if
    truststore.inject_into_ssl() has been called, is the OS keychain).
    """

    t0 = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=TLS_TIMEOUT_S) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
                subj = dict(x[0] for x in cert.get("subject", []))
                issuer = dict(x[0] for x in cert.get("issuer", []))
                return {
                    "name": "tcp_tls_keeper",
                    "status": "ok",
                    "ms": round((time.monotonic() - t0) * 1000, 1),
                    "detail": (
                        f"subject={subj.get('commonName', '?')} "
                        f"issuer={issuer.get('commonName', '?')}"
                    ),
                }
    except ssl.SSLCertVerificationError as e:
        return {
            "name": "tcp_tls_keeper",
            "status": "fail",
            "ms": round((time.monotonic() - t0) * 1000, 1),
            "error_kind": "tls_cert_verify_failed",
            "detail": _error_detail(e),
        }
    except ssl.SSLError as e:
        return {
            "name": "tcp_tls_keeper",
            "status": "fail",
            "ms": round((time.monotonic() - t0) * 1000, 1),
            "error_kind": "tls_error",
            "detail": _error_detail(e),
        }
    except socket.timeout as e:
        return {
            "name": "tcp_tls_keeper",
            "status": "fail",
            "ms": round((time.monotonic() - t0) * 1000, 1),
            "error_kind": "tcp_timeout",
            "detail": _error_detail(e),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "name": "tcp_tls_keeper",
            "status": "fail",
            "ms": round((time.monotonic() - t0) * 1000, 1),
            "error_kind": "tcp_error",
            "detail": _error_detail(e),
        }


def _probe_approle(
    url: str, namespace: str, role_id: str, secret_id: str
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Attempt AppRole login. Returns (check_dict, token_or_None)."""

    t0 = time.monotonic()
    if not (role_id and secret_id):
        return (
            {
                "name": "keeper_approle_login",
                "status": "skipped",
                "ms": 0.0,
                "detail": "No KEEPER_ROLE_ID / KEEPER_SECRET_ID configured",
            },
            None,
        )
    try:
        import hvac  # local import so tests can run without the dep

        client = hvac.Client(url=url, namespace=namespace, timeout=APPROLE_TIMEOUT_S)
        resp = client.auth.approle.login(role_id=role_id, secret_id=secret_id)
        token = (resp or {}).get("auth", {}).get("client_token")
        if not token:
            return (
                {
                    "name": "keeper_approle_login",
                    "status": "fail",
                    "ms": round((time.monotonic() - t0) * 1000, 1),
                    "error_kind": "approle_no_token",
                    "detail": "Keeper returned 200 but no client_token in response",
                },
                None,
            )
        return (
            {
                "name": "keeper_approle_login",
                "status": "ok",
                "ms": round((time.monotonic() - t0) * 1000, 1),
                "detail": f"token={_mask_id(token)}",
            },
            token,
        )
    except Exception as e:  # noqa: BLE001
        # Classify the most common hvac errors so the UI can show a hint.
        kind = "approle_error"
        name = type(e).__name__
        if "InvalidRequest" in name or "invalid role" in str(e).lower() or "invalid secret" in str(e).lower():
            kind = "approle_unauthorized"
        elif "Forbidden" in name or "403" in str(e):
            kind = "approle_forbidden"
        elif "SSLCertVerificationError" in name or "CERTIFICATE_VERIFY_FAILED" in str(e):
            # TLS failure surfaced through hvac (shouldn't happen once the
            # tls probe passed, but keep the mapping honest).
            kind = "tls_cert_verify_failed"
        return (
            {
                "name": "keeper_approle_login",
                "status": "fail",
                "ms": round((time.monotonic() - t0) * 1000, 1),
                "error_kind": kind,
                "detail": _error_detail(e),
            },
            None,
        )


def _probe_secret_read(
    url: str, namespace: str, token: str, secret_path: str
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    t0 = time.monotonic()
    if not secret_path:
        return (
            {
                "name": "keeper_secret_read",
                "status": "skipped",
                "ms": 0.0,
                "detail": "No KEEPER_SECRET_PATH configured",
            },
            None,
        )
    try:
        import hvac

        sc = hvac.Client(
            url=url,
            namespace=namespace,
            token=token,
            timeout=SECRET_READ_TIMEOUT_S,
        )
        raw = sc.read(secret_path) or {}
        data = raw.get("data") or {}
        present = sorted(data.keys())
        return (
            {
                "name": "keeper_secret_read",
                "status": "ok",
                "ms": round((time.monotonic() - t0) * 1000, 1),
                "detail": f"keys_present={present}",
            },
            data,
        )
    except Exception as e:  # noqa: BLE001
        kind = "secret_read_error"
        name = type(e).__name__
        if "InvalidPath" in name or "404" in str(e) or "no handler for route" in str(e).lower():
            kind = "secret_path_not_found"
        elif "Forbidden" in name or "403" in str(e):
            kind = "secret_forbidden"
        return (
            {
                "name": "keeper_secret_read",
                "status": "fail",
                "ms": round((time.monotonic() - t0) * 1000, 1),
                "error_kind": kind,
                "detail": _error_detail(e),
            },
            None,
        )


def _probe_snowflake(secrets: Dict[str, str], secret_data: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.monotonic()
    private_key_pem = (secret_data or {}).get("private_key")
    passphrase = (secret_data or {}).get("SNOWSQL_PRIVATE_KEY_PASSPHRASE")
    if not (private_key_pem and passphrase):
        return {
            "name": "snowflake_select_now",
            "status": "skipped",
            "ms": 0.0,
            "detail": "Keeper secret did not include private_key + passphrase",
        }
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
        import snowflake.connector

        pk = serialization.load_pem_private_key(
            private_key_pem.encode(),
            password=passphrase.encode(),
            backend=default_backend(),
        )
        pkb = pk.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        conn = snowflake.connector.connect(
            user=secrets.get("SNOWFLAKE_USER") or "",
            account=secrets.get("SNOWFLAKE_ACCOUNT") or "",
            role=secrets.get("SNOWFLAKE_ROLE") or "",
            warehouse=secrets.get("SNOWFLAKE_WAREHOUSE") or "",
            private_key=pkb,
            login_timeout=int(SNOWFLAKE_TIMEOUT_S),
            network_timeout=int(SNOWFLAKE_TIMEOUT_S),
        )
        try:
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_TIMESTAMP()")
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
        return {
            "name": "snowflake_select_now",
            "status": "ok",
            "ms": round((time.monotonic() - t0) * 1000, 1),
            "detail": f"ts={row[0] if row else '?'}",
        }
    except Exception as e:  # noqa: BLE001
        kind = "snowflake_error"
        name = type(e).__name__
        msg = str(e)
        if "is not allowed to access Snowflake" in msg or "access denied" in msg.lower():
            kind = "snowflake_access_denied"
        elif "timed out" in msg.lower() or "Timeout" in name:
            kind = "snowflake_timeout"
        elif "Incorrect username or password" in msg or "authentication failed" in msg.lower():
            kind = "snowflake_auth_failed"
        return {
            "name": "snowflake_select_now",
            "status": "fail",
            "ms": round((time.monotonic() - t0) * 1000, 1),
            "error_kind": kind,
            "detail": _error_detail(e),
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def _skip(name: str, reason: str) -> Dict[str, Any]:
    return {"name": name, "status": "skipped", "ms": 0.0, "detail": reason}


def _build_hint(checks: List[Dict[str, Any]]) -> str:
    """Human-readable hint based on the first failing check."""
    for c in checks:
        if c.get("status") != "fail":
            continue
        kind = c.get("error_kind", "")
        if kind == "dns_failure":
            return (
                "DNS for keeper.cisco.com did not resolve. Connect to the Cisco "
                "AnyConnect VPN (or confirm split-tunnel routes internal "
                "hostnames) and retry."
            )
        if kind == "tls_cert_verify_failed":
            return (
                "TLS cert verification failed. Cisco corporate TLS inspection is "
                "re-signing keeper.cisco.com with an internal CA that Python's "
                "certifi bundle does not trust. Quit + relaunch AdoptIQ "
                "(the app now prefers the system keychain via truststore). If "
                "this persists, install the Cisco corporate root CA into System "
                "Keychain and mark it trusted for SSL."
            )
        if kind in ("tls_error", "tcp_timeout", "tcp_error"):
            return (
                "Network path to keeper.cisco.com is blocked or unreachable. "
                "Check VPN, then try a different egress."
            )
        if kind == "approle_unauthorized":
            return (
                "Bundled KEEPER_ROLE_ID / KEEPER_SECRET_ID have been rotated or "
                "revoked. Ask the Keeper admin for a fresh AppRole secret and "
                "rebuild AdoptIQ (update secrets.env, run embed_credentials.py "
                "and build_mac.sh)."
            )
        if kind == "approle_forbidden":
            return (
                "Keeper rejected the AppRole with 403. Policy may not grant "
                "access to this secret path."
            )
        if kind == "secret_path_not_found":
            return (
                "Keeper returned 'secret not found' for the configured path. "
                "Confirm KEEPER_SECRET_PATH and rebuild AdoptIQ with the "
                "correct value."
            )
        if kind == "secret_forbidden":
            return (
                "AppRole lacks read permission on the configured secret path. "
                "Ask the Keeper admin to extend the policy."
            )
        if kind == "snowflake_access_denied":
            return (
                "Snowflake denied the service user/role. Ask the data platform "
                "team to verify CX_SWSSBST_ETL_ROLE is still granted."
            )
        if kind == "snowflake_timeout":
            return (
                "Snowflake connect timed out. Warehouse may be suspended or "
                "network path is slow."
            )
        if kind == "snowflake_auth_failed":
            return (
                "Snowflake authentication failed. The private key returned by "
                "Keeper is stale or the service user changed."
            )
        return "See check detail; all Keeper/Snowflake subsystems have specific error kinds."
    return "All checks passed."


# Round 6 / Phase 6.15: namespace probe ``error_kind`` values at the
# public-API boundary so callers (Admin dashboard, error_classifier,
# audit JSONL mirror, structured logs) can use the same kind taxonomy
# as ``analysis.*`` / ``llm.*`` -- e.g. ``diag.dns.timeout``,
# ``diag.tls.cert_verify_failed``, ``diag.snowflake.timeout``.  The
# internal probes still emit short flat kinds because ``_build_hint``
# is keyed on those legacy strings; mapping happens once here at the
# JSON boundary so refactors of either side stay independent.
_DIAG_KIND_NAMESPACE_MAP: Dict[str, str] = {
    # DNS
    "dns_timeout": "diag.dns.timeout",
    "dns_failure": "diag.dns.failure",
    # TLS / TCP
    "tls_cert_verify_failed": "diag.tls.cert_verify_failed",
    "tls_error": "diag.tls.error",
    "tcp_timeout": "diag.tcp.timeout",
    "tcp_error": "diag.tcp.error",
    # AppRole / Keeper auth
    "approle_no_token": "diag.approle.no_token",
    "approle_unauthorized": "diag.approle.unauthorized",
    "approle_forbidden": "diag.approle.forbidden",
    "approle_error": "diag.approle.error",
    # Secret read
    "secret_read_error": "diag.secret.read_error",
    "secret_path_not_found": "diag.secret.path_not_found",
    "secret_forbidden": "diag.secret.forbidden",
    # Snowflake
    "snowflake_error": "diag.snowflake.error",
    "snowflake_access_denied": "diag.snowflake.access_denied",
    "snowflake_timeout": "diag.snowflake.timeout",
    "snowflake_auth_failed": "diag.snowflake.auth_failed",
}


def _namespace_check_kind(check: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of ``check`` with ``error_kind`` namespaced.

    The legacy flat kind is preserved on ``error_kind_legacy`` so any
    pre-existing dashboard / log analytics that grep for
    ``dns_timeout`` etc. keep working through one full release of
    backward-compat overlap.
    """
    if not isinstance(check, dict):
        return check
    legacy = check.get("error_kind")
    if not legacy:
        return check
    namespaced = _DIAG_KIND_NAMESPACE_MAP.get(legacy, f"diag.{legacy}")
    out = dict(check)
    out["error_kind"] = namespaced
    out["error_kind_legacy"] = legacy
    return out


def run_connectivity_diagnostics(secrets: Dict[str, str]) -> Dict[str, Any]:
    """Run the full DNS -> Snowflake self-test.

    Parameters
    ----------
    secrets:
        Mapping of the same shape that ``_bundled_secrets.get_secrets()``
        returns: ``KEEPER_URL``, ``KEEPER_NAMESPACE``, ``KEEPER_ROLE_ID``,
        ``KEEPER_SECRET_ID``, ``KEEPER_SECRET_PATH``, ``SNOWFLAKE_USER``,
        ``SNOWFLAKE_ACCOUNT``, ``SNOWFLAKE_ROLE``, ``SNOWFLAKE_WAREHOUSE``.

    Returns
    -------
    dict with::

        {
            "ok": bool,          # True iff every non-skipped check was ok
            "checks": [ {name, status, ms, detail, error_kind?}, ... ],
            "hint": str,         # actionable hint for the first failing check
            "truststore_active": bool,
            "keeper_host": str,
            "namespace": str,
            "secret_path": str,
        }
    """

    import os as _os

    url = secrets.get("KEEPER_URL") or "https://keeper.cisco.com"
    namespace = secrets.get("KEEPER_NAMESPACE") or ""
    role_id = secrets.get("KEEPER_ROLE_ID") or ""
    secret_id = secrets.get("KEEPER_SECRET_ID") or ""
    secret_path = secrets.get("KEEPER_SECRET_PATH") or ""

    parsed = urlparse(url)
    host = parsed.hostname or "keeper.cisco.com"
    port = parsed.port or 443

    checks: List[Dict[str, Any]] = []

    # DNS
    dns_check = _probe_dns(host, port)
    checks.append(dns_check)
    dns_ok = dns_check["status"] == "ok"

    # TLS
    if dns_ok:
        tls_check = _probe_tls(host, port)
    else:
        tls_check = _skip("tcp_tls_keeper", "DNS failed")
    checks.append(tls_check)
    tls_ok = tls_check["status"] == "ok"

    # AppRole
    if tls_ok:
        approle_check, token = _probe_approle(url, namespace, role_id, secret_id)
    else:
        approle_check, token = _skip("keeper_approle_login", "TLS failed"), None
    checks.append(approle_check)

    # Secret read
    if token:
        secret_check, secret_data = _probe_secret_read(url, namespace, token, secret_path)
    else:
        secret_check = _skip(
            "keeper_secret_read",
            "AppRole login failed" if tls_ok else "TLS failed",
        )
        secret_data = None
    checks.append(secret_check)

    # Snowflake
    if secret_data:
        sf_check = _probe_snowflake(secrets, secret_data)
    else:
        sf_check = _skip(
            "snowflake_select_now",
            "Keeper secret not read",
        )
    checks.append(sf_check)

    # Green only when the entire chain actually succeeded. Skipped stages
    # count as not-ok so the banner never turns green just because upstream
    # probes passed.
    ok = all(c["status"] == "ok" for c in checks)

    # Round 6 / Phase 6.15: namespace ``error_kind`` on every check
    # before returning to the JSON boundary.  ``_build_hint`` runs
    # against the pre-namespace list because it greps the legacy
    # short kinds and we don't want to fan out the if/elif ladder.
    hint = _build_hint(checks)
    namespaced_checks = [_namespace_check_kind(c) for c in checks]

    # Round 7 / Phase 3.15: redact infrastructure identifiers
    # (``keeper_host``, ``namespace``, ``secret_path``,
    # ``keys_present``, resolved IPs) from the public payload.  These
    # were originally surfaced for ops-side debugging but landed in
    # responses returned to non-localhost callers, leaking the
    # internal Keeper topology.  Local callers (loopback) still get
    # the full payload via the optional ``include_infra`` kwarg added
    # below; everyone else only sees the actionable status fields.
    public_checks: List[Dict[str, Any]] = []
    for chk in namespaced_checks:
        chk_copy = dict(chk)
        det = str(chk_copy.get("detail") or "")
        if "keys_present" in det.lower():
            chk_copy["detail"] = "secret read succeeded"
        # Strip resolved IPs from detail strings (``host -> 10.x.x.x``).
        import re as _re
        chk_copy["detail"] = _re.sub(
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip-redacted>", str(chk_copy.get("detail") or "")
        )
        public_checks.append(chk_copy)

    return {
        "ok": ok,
        "checks": public_checks,
        "hint": hint,
        "truststore_active": bool(_os.environ.get("ADOPTIQ_TRUSTSTORE_INJECTED")),
        # Keeper host / namespace / secret_path used to be in the public
        # payload; replaced with redacted markers per Phase 3.15.  An
        # operator running diagnostics on the local machine can read
        # the un-redacted values from the server log instead.
        "keeper_host": "<redacted>",
        "namespace": "<redacted>",
        "secret_path": "<redacted>",
    }


__all__ = [
    "run_connectivity_diagnostics",
    "DNS_TIMEOUT_S",
    "TLS_TIMEOUT_S",
    "APPROLE_TIMEOUT_S",
    "SECRET_READ_TIMEOUT_S",
    "SNOWFLAKE_TIMEOUT_S",
]
