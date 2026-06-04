"""Tests for ``error_classifier.classify_analysis_error``.

These tests synthesize the exception types the analysis pipeline can raise
and assert the classifier attributes each failure mode to the right ``kind``
and user-facing message. They also lock in the fix for the original bug
where the banner unconditionally said "Please ensure you are connected to
the Cisco VPN" when the real failure was TLS cert verification, a rotated
AppRole, or a Snowflake-side error.
"""

from __future__ import annotations

import socket
import ssl
from typing import Any

import pytest

from error_classifier import classify_analysis_error


# ---------------------------------------------------------------------------
# DNS failures
# ---------------------------------------------------------------------------
def test_classifier_dns_gaierror() -> None:
    e = socket.gaierror("nodename nor servname provided, or not known")
    result = classify_analysis_error(e)
    assert result.kind == "analysis.keeper.dns_failed"
    assert "DNS" in result.user_message
    assert "nodename nor servname" in result.detail_tail


def test_classifier_dns_generic_message() -> None:
    e = RuntimeError("getaddrinfo failed for keeper.cisco.com")
    result = classify_analysis_error(e)
    assert result.kind == "analysis.keeper.dns_failed"


# ---------------------------------------------------------------------------
# TLS cert verification (the root cause of the user's report)
# ---------------------------------------------------------------------------
def test_classifier_ssl_cert_verification_error() -> None:
    # Build a real SSLCertVerificationError that matches what the user saw.
    try:
        raise ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "self-signed certificate in certificate chain (_ssl.c:1006)"
        )
    except ssl.SSLCertVerificationError as e:
        result = classify_analysis_error(e)

    assert result.kind == "analysis.keeper.tls_cert_verify_failed"
    assert "TLS" in result.user_message
    assert "corporate" in result.user_message.lower()
    # Must surface enough detail for support to identify the failure.
    assert "CERTIFICATE_VERIFY_FAILED" in result.detail_tail


def test_classifier_requests_style_ssl_error() -> None:
    """If requests/urllib3 wraps the ssl error, the name still contains SSLError."""

    class SSLError(Exception):
        pass

    e = SSLError(
        "HTTPSConnectionPool(host='keeper.cisco.com', port=443): "
        "certificate verify failed: unable to get local issuer certificate"
    )
    result = classify_analysis_error(e)
    assert result.kind == "analysis.keeper.tls_cert_verify_failed"


# ---------------------------------------------------------------------------
# Rotated AppRole (second real failure the user will hit)
# ---------------------------------------------------------------------------
def test_classifier_approle_invalid_request() -> None:
    class InvalidRequest(Exception):
        """Stand-in for hvac.exceptions.InvalidRequest."""

    e = InvalidRequest(
        "invalid role or secret ID, on post https://keeper.cisco.com/v1/auth/approle/login"
    )
    result = classify_analysis_error(e)
    assert result.kind == "analysis.keeper.approle_unauthorized"
    # Round 7 / Phase 3.14: the user-facing message is now generic and
    # routes operators to the Admin page / runbook for specifics
    # (rotation/revocation hints live in ``detail_tail``, not in the
    # banner shown to end users).  We assert the banner is generic and
    # the detail tail still carries the actionable runbook.
    assert "secrets service" in result.user_message.lower() or "administrator" in result.user_message.lower()
    assert "rotated" in (result.detail_tail or "").lower() or "refresh" in (result.detail_tail or "").lower()


def test_classifier_approle_forbidden() -> None:
    class Forbidden(Exception):
        """Stand-in for hvac.exceptions.Forbidden."""

    e = Forbidden("permission denied, on post https://keeper.cisco.com/v1/auth/approle/login")
    result = classify_analysis_error(e)
    assert result.kind == "analysis.keeper.forbidden"


def test_classifier_secret_path_not_found() -> None:
    class InvalidPath(Exception):
        """Stand-in for hvac.exceptions.InvalidPath."""

    e = InvalidPath("no handler for route secret/wrong/path")
    result = classify_analysis_error(e)
    assert result.kind == "analysis.keeper.secret_path_not_found"


# ---------------------------------------------------------------------------
# Keeper read timeout / unreachable
# ---------------------------------------------------------------------------
def test_classifier_keeper_read_timeout() -> None:
    class ReadTimeout(Exception):
        pass

    e = ReadTimeout(
        "HTTPSConnectionPool(host='keeper.cisco.com', port=443): Read timed out."
    )
    result = classify_analysis_error(e)
    assert result.kind == "analysis.keeper.read_timeout"


def test_classifier_keeper_connection_refused() -> None:
    class ConnectionError(Exception):
        pass

    e = ConnectionError(
        "Max retries exceeded with url: /v1/auth/approle/login "
        "(host='keeper.cisco.com'): connection refused"
    )
    result = classify_analysis_error(e)
    assert result.kind == "analysis.keeper.unreachable"


# ---------------------------------------------------------------------------
# Snowflake
# ---------------------------------------------------------------------------
def test_classifier_snowflake_access_denied() -> None:
    e = RuntimeError(
        "User CX_SWSSBST_ETL_SVC is not allowed to access Snowflake"
    )
    result = classify_analysis_error(e)
    assert result.kind == "analysis.snowflake.access_denied"


def test_classifier_snowflake_connect_timeout() -> None:
    e = RuntimeError("Snowflake connection timed out after 30s")
    result = classify_analysis_error(e)
    assert result.kind == "analysis.snowflake.timeout"


# ---------------------------------------------------------------------------
# Regression: the old banner wording must NOT appear for the user's actual
# exception. This is the specific regression this hotfix exists to prevent.
# ---------------------------------------------------------------------------
def test_regression_tls_error_is_no_longer_attributed_to_vpn() -> None:
    """The user was on VPN and saw `Cisco Keeper Connection Error: Please
    ensure you are connected to the Cisco VPN.` That exact wording must no
    longer be emitted for a TLS cert verify error.
    """

    e = ssl.SSLCertVerificationError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "self-signed certificate in certificate chain (_ssl.c:1006)"
    )
    result = classify_analysis_error(e)
    assert "Please ensure you are connected to the Cisco VPN" not in result.user_message
    assert result.kind == "analysis.keeper.tls_cert_verify_failed"


def test_regression_rotated_approle_is_not_attributed_to_vpn() -> None:
    class InvalidRequest(Exception):
        pass

    e = InvalidRequest("invalid role or secret ID")
    result = classify_analysis_error(e)
    assert "Please ensure you are connected to the Cisco VPN" not in result.user_message
    assert result.kind == "analysis.keeper.approle_unauthorized"


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------
def test_classifier_unknown_error_preserves_detail() -> None:
    e = RuntimeError("something completely unexpected broke")
    result = classify_analysis_error(e)
    assert result.kind == "analysis.unknown"
    # Round 7 / Phase 3.14: the user-facing message is now generic so
    # exception strings (which can leak internal hostnames, query text,
    # or secret tail bytes) cannot reach the UI.  The original message
    # is preserved in ``detail_tail`` for the Admin page / log digest.
    assert "something completely unexpected broke" in (result.detail_tail or "")


def test_classifier_generic_keeper_fallback() -> None:
    e = RuntimeError(
        "Unhandled error calling https://keeper.cisco.com/v1/sys/health"
    )
    result = classify_analysis_error(e)
    assert result.kind == "analysis.keeper.generic"
    assert "self-test" in result.user_message.lower()


def test_classifier_detail_tail_redacts_long_ids() -> None:
    e = RuntimeError(
        "role_id=abcdefghijklmnopqrstuvwxyz0123456789ABCDEF and secret_id="
        "FEDCBA9876543210zyxwvutsrqponmlkjihgfedcba"
    )
    result = classify_analysis_error(e)
    assert "<redacted>" in result.detail_tail


def test_classifier_returns_structured_shape() -> None:
    e = RuntimeError("whatever")
    result = classify_analysis_error(e)
    assert isinstance(result.kind, str) and result.kind
    assert isinstance(result.user_message, str) and result.user_message
    assert isinstance(result.detail_tail, str) and result.detail_tail


# ---------------------------------------------------------------------------
# Round 130: redacted Snowflake wrapper with chained driver detail
# ---------------------------------------------------------------------------
def test_classifier_redacted_snowflake_wrapper_with_allowlist_cause() -> None:
    driver = RuntimeError(
        "250001: Incoming request with IP/Token ... is not allowed to access Snowflake"
    )
    wrapped = RuntimeError("Failed to connect to Snowflake")
    wrapped.__cause__ = driver
    result = classify_analysis_error(wrapped)
    assert result.kind == "analysis.snowflake.access_denied"
    assert result.kind != "analysis.unknown"
    assert "VPN" in result.user_message or "allowlisted" in result.user_message


def test_classifier_redacted_snowflake_wrapper_generic() -> None:
    driver = RuntimeError("Network error talking to snowflake")
    wrapped = RuntimeError("Failed to connect to Snowflake")
    wrapped.__cause__ = driver
    result = classify_analysis_error(wrapped)
    assert result.kind == "analysis.snowflake.error"
    assert result.kind != "analysis.unknown"
