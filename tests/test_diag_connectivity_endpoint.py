"""Tests for the Keeper / Snowflake connectivity self-test.

The function under test is ``connectivity_diagnostics.run_connectivity_diagnostics``.
Each test monkeypatches one or more of the private probe helpers so we never
touch the real network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import connectivity_diagnostics as cd  # noqa: E402


BASE_SECRETS = {
    "KEEPER_URL": "https://keeper.cisco.com",
    "KEEPER_NAMESPACE": "cloudDB",
    "KEEPER_ROLE_ID": "role-id-1234",
    "KEEPER_SECRET_ID": "secret-id-5678",
    "KEEPER_SECRET_PATH": "secret/snowflake/prd/svc/key",
    "SNOWFLAKE_USER": "CX_SWSSBST_ETL_SVC",
    "SNOWFLAKE_ACCOUNT": "cisco.us-east-1",
    "SNOWFLAKE_ROLE": "CX_SWSSBST_ETL_ROLE",
    "SNOWFLAKE_WAREHOUSE": "CX_SWSSBST_ETL_WH",
}


def _ok_dns(host, port):
    return {"name": "dns_keeper", "status": "ok", "ms": 10.0, "detail": f"{host}"}


def _fail_dns(host, port):
    return {
        "name": "dns_keeper",
        "status": "fail",
        "ms": 10.0,
        "error_kind": "dns_failure",
        "detail": "gaierror: nodename nor servname",
    }


def _ok_tls(host, port):
    return {"name": "tcp_tls_keeper", "status": "ok", "ms": 200.0, "detail": "subject=keeper"}


def _fail_tls_cert(host, port):
    return {
        "name": "tcp_tls_keeper",
        "status": "fail",
        "ms": 150.0,
        "error_kind": "tls_cert_verify_failed",
        "detail": "SSLCertVerificationError: self-signed certificate in certificate chain",
    }


def _ok_approle(url, namespace, role_id, secret_id):
    return (
        {"name": "keeper_approle_login", "status": "ok", "ms": 300.0, "detail": "token=abc...xyz"},
        "s.FAKE_TOKEN",
    )


def _fail_approle_unauth(url, namespace, role_id, secret_id):
    return (
        {
            "name": "keeper_approle_login",
            "status": "fail",
            "ms": 350.0,
            "error_kind": "approle_unauthorized",
            "detail": "InvalidRequest: invalid role or secret id",
        },
        None,
    )


def _ok_secret(url, namespace, token, secret_path):
    return (
        {
            "name": "keeper_secret_read",
            "status": "ok",
            "ms": 220.0,
            "detail": "keys_present=['SNOWSQL_PRIVATE_KEY_PASSPHRASE', 'private_key']",
        },
        {"private_key": "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----",
         "SNOWSQL_PRIVATE_KEY_PASSPHRASE": "fake-passphrase"},
    )


def _ok_snowflake(secrets, secret_data):
    return {
        "name": "snowflake_select_now",
        "status": "ok",
        "ms": 900.0,
        "detail": "ts=2026-04-22 10:00:00",
    }


def _fail_snowflake_timeout(secrets, secret_data):
    return {
        "name": "snowflake_select_now",
        "status": "fail",
        "ms": 10000.0,
        "error_kind": "snowflake_timeout",
        "detail": "TimeoutError: login timed out",
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_all_green(monkeypatch):
    monkeypatch.setattr(cd, "_probe_dns", _ok_dns)
    monkeypatch.setattr(cd, "_probe_tls", _ok_tls)
    monkeypatch.setattr(cd, "_probe_approle", _ok_approle)
    monkeypatch.setattr(cd, "_probe_secret_read", _ok_secret)
    monkeypatch.setattr(cd, "_probe_snowflake", _ok_snowflake)

    result = cd.run_connectivity_diagnostics(BASE_SECRETS)

    assert result["ok"] is True
    names = [c["name"] for c in result["checks"]]
    assert names == [
        "dns_keeper",
        "tcp_tls_keeper",
        "keeper_approle_login",
        "keeper_secret_read",
        "snowflake_select_now",
    ]
    for c in result["checks"]:
        assert c["status"] == "ok"
    assert "All checks passed" in result["hint"]


# ---------------------------------------------------------------------------
# DNS fails -> everything downstream skipped
# ---------------------------------------------------------------------------
def test_dns_failure_skips_downstream(monkeypatch):
    monkeypatch.setattr(cd, "_probe_dns", _fail_dns)
    monkeypatch.setattr(cd, "_probe_tls", _ok_tls)  # should never run
    monkeypatch.setattr(cd, "_probe_approle", _ok_approle)
    monkeypatch.setattr(cd, "_probe_secret_read", _ok_secret)
    monkeypatch.setattr(cd, "_probe_snowflake", _ok_snowflake)

    result = cd.run_connectivity_diagnostics(BASE_SECRETS)

    assert result["ok"] is False
    assert result["checks"][0]["status"] == "fail"
    for c in result["checks"][1:]:
        assert c["status"] == "skipped"
    assert "DNS" in result["hint"] or "vpn" in result["hint"].lower()


# ---------------------------------------------------------------------------
# TLS cert verify failure -> the exact case the user is hitting
# ---------------------------------------------------------------------------
def test_tls_cert_verify_failure_classified(monkeypatch):
    monkeypatch.setattr(cd, "_probe_dns", _ok_dns)
    monkeypatch.setattr(cd, "_probe_tls", _fail_tls_cert)
    monkeypatch.setattr(cd, "_probe_approle", _ok_approle)
    monkeypatch.setattr(cd, "_probe_secret_read", _ok_secret)
    monkeypatch.setattr(cd, "_probe_snowflake", _ok_snowflake)

    result = cd.run_connectivity_diagnostics(BASE_SECRETS)

    assert result["ok"] is False
    assert result["checks"][0]["status"] == "ok"
    assert result["checks"][1]["status"] == "fail"
    # Round 7 / Phase 3.15: error_kind is now namespaced
    # ("diag.tls.cert_verify_failed") so a single regex over UI logs
    # cleanly tells "diag.*" apart from "analysis.*".
    assert result["checks"][1]["error_kind"] == "diag.tls.cert_verify_failed"
    for c in result["checks"][2:]:
        assert c["status"] == "skipped"
    assert "tls" in result["hint"].lower() or "cert" in result["hint"].lower()


# ---------------------------------------------------------------------------
# AppRole fail: the other real failure (rotated bundled creds)
# ---------------------------------------------------------------------------
def test_approle_unauthorized_gives_actionable_hint(monkeypatch):
    monkeypatch.setattr(cd, "_probe_dns", _ok_dns)
    monkeypatch.setattr(cd, "_probe_tls", _ok_tls)
    monkeypatch.setattr(cd, "_probe_approle", _fail_approle_unauth)
    monkeypatch.setattr(cd, "_probe_secret_read", _ok_secret)
    monkeypatch.setattr(cd, "_probe_snowflake", _ok_snowflake)

    result = cd.run_connectivity_diagnostics(BASE_SECRETS)
    assert result["ok"] is False
    # Round 7 / Phase 3.15: error_kind namespaced under "diag.*".
    assert result["checks"][2]["error_kind"] == "diag.approle.unauthorized"
    assert result["checks"][3]["status"] == "skipped"
    assert result["checks"][4]["status"] == "skipped"
    assert "rotated" in result["hint"].lower() or "revoked" in result["hint"].lower()


# ---------------------------------------------------------------------------
# Snowflake fails
# ---------------------------------------------------------------------------
def test_snowflake_timeout_surfaced(monkeypatch):
    monkeypatch.setattr(cd, "_probe_dns", _ok_dns)
    monkeypatch.setattr(cd, "_probe_tls", _ok_tls)
    monkeypatch.setattr(cd, "_probe_approle", _ok_approle)
    monkeypatch.setattr(cd, "_probe_secret_read", _ok_secret)
    monkeypatch.setattr(cd, "_probe_snowflake", _fail_snowflake_timeout)

    result = cd.run_connectivity_diagnostics(BASE_SECRETS)
    assert result["ok"] is False
    # Round 7 / Phase 3.15: error_kind namespaced under "diag.*".
    assert result["checks"][-1]["error_kind"] == "diag.snowflake.timeout"
    assert "snowflake" in result["hint"].lower() or "timed out" in result["hint"].lower()


# ---------------------------------------------------------------------------
# No secret material in the response
# ---------------------------------------------------------------------------
def test_no_secret_material_in_response(monkeypatch):
    monkeypatch.setattr(cd, "_probe_dns", _ok_dns)
    monkeypatch.setattr(cd, "_probe_tls", _ok_tls)
    monkeypatch.setattr(cd, "_probe_approle", _ok_approle)
    monkeypatch.setattr(cd, "_probe_secret_read", _ok_secret)
    monkeypatch.setattr(cd, "_probe_snowflake", _ok_snowflake)

    result = cd.run_connectivity_diagnostics(BASE_SECRETS)

    import json
    rendered = json.dumps(result)
    # The test fixtures deliberately include a fake private key in the
    # secret_data returned by _ok_secret; the diagnostics harness must not
    # echo private_key material into the response. We only render "checks"
    # + hint + metadata, so the string should never appear.
    assert "BEGIN PRIVATE KEY" not in rendered
    assert "fake-passphrase" not in rendered
    assert BASE_SECRETS["KEEPER_ROLE_ID"] not in rendered
    assert BASE_SECRETS["KEEPER_SECRET_ID"] not in rendered


# ---------------------------------------------------------------------------
# Shape: the response keys the UI depends on must exist
# ---------------------------------------------------------------------------
def test_response_shape_has_required_keys(monkeypatch):
    monkeypatch.setattr(cd, "_probe_dns", _ok_dns)
    monkeypatch.setattr(cd, "_probe_tls", _ok_tls)
    monkeypatch.setattr(cd, "_probe_approle", _ok_approle)
    monkeypatch.setattr(cd, "_probe_secret_read", _ok_secret)
    monkeypatch.setattr(cd, "_probe_snowflake", _ok_snowflake)

    result = cd.run_connectivity_diagnostics(BASE_SECRETS)
    assert set(["ok", "checks", "hint", "truststore_active", "keeper_host"]).issubset(result.keys())
    for c in result["checks"]:
        assert "name" in c and "status" in c and "ms" in c


# ---------------------------------------------------------------------------
# Empty secrets (no KEEPER_ROLE_ID etc.) should still produce a clean result.
# ---------------------------------------------------------------------------
def test_handles_empty_secrets(monkeypatch):
    # Don't monkeypatch anything except dns -> ok and tls -> ok so the
    # approle probe takes the 'skipped: no role id' branch.
    monkeypatch.setattr(cd, "_probe_dns", _ok_dns)
    monkeypatch.setattr(cd, "_probe_tls", _ok_tls)

    result = cd.run_connectivity_diagnostics({"KEEPER_URL": "https://keeper.cisco.com"})
    assert result["ok"] is False  # approle skipped means overall not ok
    # The approle probe should have skipped with a reason.
    approle = next(c for c in result["checks"] if c["name"] == "keeper_approle_login")
    assert approle["status"] == "skipped"
